import asyncio
from dataclasses import asdict, dataclass
from collections.abc import Awaitable, Callable
import json
import logging
import uuid
from typing import Optional

from constants import InferRequestStateType, LlmErrorCategory, LlmProtocol, LlmProviderType
from model.coreModel.gtCoreChatModel import GtCoreAgentDialogContext
from model.dbModel.agentMessage import AgentMessage
from service.llmService.llmErrorClassifier import classify_llm_error, RETRYABLE_CATEGORIES
from service.llmService.llmRequestRules import apply_llm_request_rules
from util import configUtil, llmApiUtil

from util.configUtil.configTypes import LlmModelConfig, LlmProviderConfig, LlmContextConfig
import appPaths
import os

logger = logging.getLogger(__name__)

_INFER_RETRY_DELAYS_SECONDS = (2, 4, 8, 16, 32, 32, 32)


@dataclass
class InferResult:
    ok: bool
    response: Optional[llmApiUtil.OpenAIResponse] = None
    error_message: str = ""
    error: Optional[Exception] = None
    error_category: Optional[LlmErrorCategory] = None
    request_id: str = ""

    @classmethod
    def success(cls, response: llmApiUtil.OpenAIResponse, request_id: str = "") -> "InferResult":
        return cls(ok=True, response=response, request_id=request_id)

    @classmethod
    def failure(cls, error: Exception, request_id: str = "") -> "InferResult":
        return cls(
            ok=False,
            error_message=str(error),
            error=error,
            error_category=classify_llm_error(error),
            request_id=request_id,
        )

    @property
    def usage(self) -> llmApiUtil.OpenAIUsage | None:
        if self.response is None:
            return None
        return self.response.usage


@dataclass
class InferRequestStatusEvent:
    state: InferRequestStateType
    request_id: str = ""
    attempt: int = 0
    max_attempts: int = 0
    retry_delay_seconds: int | None = None
    error_message: str | None = None


InferRequestStatusEventHandler = Callable[[InferRequestStatusEvent], Awaitable[None]]


def get_provider_url(provider: LlmProviderConfig, protocol: LlmProtocol) -> str:
    proto = protocol.value if isinstance(protocol, LlmProtocol) else protocol
    if proto in provider.urls and provider.urls[proto]:
        return provider.urls[proto]

    provider_type = provider.type.value if isinstance(provider.type, LlmProviderType) else provider.type
    preset_path = os.path.join(appPaths.ASSETS_DIR, "preset", "providerDefaultUrls.json")
    if os.path.isfile(preset_path):
        with open(preset_path, "r", encoding="utf-8") as f:
            presets = json.load(f)
        if provider_type in presets:
            preset_urls = presets[provider_type]
            if proto in preset_urls:
                return preset_urls[proto]
    return ""

def resolve_model(model_name: str | None) -> tuple[LlmProviderConfig, LlmModelConfig]:
    """
    解析模型字符串为 provider 配置和合并后的模型配置。

    Args:
        model_name: 可选的模型标识字符串，格式为 "model@provider"。
                    系统槽位格式为 "slot@system"，如 "primary@system"。
                    若为 None 或空字符串，则使用 default_models.primary。

    Returns:
        (provider_config, merged_model_config)
        其中 merged_model_config 已合并 context_config，
        protocol 可从 model_config.protocol 获取。

    Raises:
        ValueError: 配置缺失、格式错误或模型/Provider 未启用。
    """
    setting = configUtil.get_app_config().setting

    if model_name is None or model_name == "":
        model_name = "primary@system"

    # 格式校验
    if "@" not in model_name:
        raise ValueError(f"模型标识格式错误（应为 model@provider）：{model_name}")

    model_part, provider_name = model_name.rsplit("@", 1)

    # 解析系统槽位：xxx@system → 查 default_models 获取实际 model@provider
    if provider_name == "system":
        slot_model = setting.get_slot_model_name(model_part)
        if slot_model == "":
            raise ValueError(f"未配置有效的系统槽位：{model_part}")
        model_part, provider_name = slot_model.rsplit("@", 1)
    else:
        pass  # model_part, provider_name 已就绪

    # 查找 provider
    provider_config = setting.find_provider(provider_name)
    if provider_config is None:
        raise ValueError(f"找不到提供商：{provider_name}")
    if provider_config.enable is False:
        raise ValueError(f"提供商 {provider_name} 已禁用")

    # 查找 model
    model_config = provider_config.find_model(model_part)
    if model_config is None:
        raise ValueError(f"在提供商 {provider_name} 中找不到模型：{model_part}")
    if model_config.enabled is False:
        raise ValueError(f"模型 {model_part} 在提供商 {provider_name} 中已禁用")

    merged_model = model_config.model_copy(update={
        "context_config": (model_config.context_config or LlmContextConfig()).resolve_with_global(setting.context_config),
    })

    return provider_config, merged_model

async def startup() -> None:
    setting = configUtil.get_app_config().setting
    if not setting.is_llm_configured:
        logger.warning("当前未配置可用的 LLM 服务，Agent 推理功能不可用。请通过 Web Console 或手动编辑 setting.json 完成配置。")

def get_default_model_or_none() -> str | None:
    setting = configUtil.get_app_config().setting
    if not setting.is_llm_configured:
        return None
    return setting.default_models.primary

def get_default_model() -> str:
    model = get_default_model_or_none()
    if not model:
        raise ValueError("未配置可用的 LLM 服务（提供商全部被禁用或未设置默认模型槽位）")
    return model


def _usage_to_log_json(usage: llmApiUtil.OpenAIUsage | None) -> str:
    if usage is None:
        return "null"
    return json.dumps(usage.model_dump(mode="json", exclude_none=False), ensure_ascii=False, default=str)


def _split_tool_result_messages(messages: list[AgentMessage]) -> list[llmApiUtil.OpenAIMessage]:
    """把 AgentMessage 列表转成发送用 OpenAIMessage 列表。

    两步：
    1. 直接转换：每条消息转 OpenAIMessage；带图片附件的 TOOL 消息拆出 USER 图片消息（紧跟其后）。
    2. 调整顺序：工具结果段（连续的 TOOL / USER）内稳定分区，TOOL 在前、USER（含图片）在后，
       避免多 tool_call 时图片 USER 消息插在工具结果中间（OpenAI 规范：tool 消息紧随
       assistant(tool_calls)，image_url 仅允许在 user 角色）。

    注：模型能力门控（非视觉模型不发图片）暂未启用，后续按模型 input 能力接入。
    """
    # 1) 直接转换
    converted: list[llmApiUtil.OpenAIMessage] = []
    for msg in messages:
        converted.append(msg.to_openai_message())
        for att in (msg.attachments or []):
            if att.kind == "image":
                blocks: list[llmApiUtil.OpenAIContentBlock] = []
                if att.caption:
                    blocks.append(llmApiUtil.OpenAITextContentBlock(text=att.caption))
                url = att.url or f"data:{att.mime_type or 'image/png'};base64,{att.data}"
                blocks.append(llmApiUtil.OpenAIImageUrlContentBlock(image_url={"url": url}))
                converted.append(llmApiUtil.OpenAIMessage(
                    role=llmApiUtil.OpenaiApiRole.USER,
                    content=blocks,
                ))

    # 2) 调整顺序：一次遍历，检测「图片 USER 紧挨在 TOOL 之前」的逆序相邻对并交换。
    #    交换后回退一步，让图片继续往后冒泡，直到所有 TOOL 都排在图片之前。
    def _is_image_user(m: llmApiUtil.OpenAIMessage) -> bool:
        return (
            m.role == llmApiUtil.OpenaiApiRole.USER
            and isinstance(m.content, list)
            and any(isinstance(b, llmApiUtil.OpenAIImageUrlContentBlock) for b in m.content)
        )

    i = 0
    while i < len(converted):
        if converted[i].role == llmApiUtil.OpenaiApiRole.TOOL and i > 0 and _is_image_user(converted[i - 1]):
            converted[i - 1], converted[i] = converted[i], converted[i - 1]
            i -= 1  # 图片后移了一位，可能还要继续往后冒泡，回退重新检查
        else:
            i += 1
    return converted


def _build_request(
    *,
    ctx: GtCoreAgentDialogContext,
    model_config: LlmModelConfig,
) -> tuple[llmApiUtil.OpenAIRequest, tuple[str, ...]]:
    # ctx.messages 为 AgentMessage（存储域类型），这里统一转换回 OpenAIMessage（发送格式）
    messages: list[llmApiUtil.OpenAIMessage] = [
        llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.SYSTEM, ctx.system_prompt),
        *_split_tool_result_messages(ctx.messages),
    ]
    
    # 获取上下文配置 (优先使用模型独立配置)
    setting = configUtil.get_app_config().setting
    context_cfg = model_config.context_config if model_config.context_config else setting.context_config

    # model_config.extra_params 已经是合并好的（resolve_model 中合并）
    request = llmApiUtil.OpenAIRequest(
        model=model_config.name,
        messages=messages,
        tools=ctx.tools,
        tool_choice=ctx.tool_choice,
        prompt_cache=ctx.prompt_cache,
        max_tokens=context_cfg.reserve_output_tokens,
        temperature=model_config.temperature,
        extra_params=model_config.extra_params,
    )
    return apply_llm_request_rules(request)


async def _safe_call_handler(
    on_status_event: InferRequestStatusEventHandler | None,
    event: InferRequestStatusEvent,
) -> None:
    if on_status_event is None:
        return
    try:
        await on_status_event(event)
    except Exception:
        logger.exception(f"LLM request status event callback failed: {event.request_id=}, {event.state.name=}")


async def _send_with_retry(
    send_request: Callable[..., Awaitable[llmApiUtil.OpenAIResponse]],
    args: tuple,
    kwargs: dict,
    on_status_event: InferRequestStatusEventHandler | None = None,
) -> llmApiUtil.OpenAIResponse:
    last_error: Exception | None = None
    total_attempts = len(_INFER_RETRY_DELAYS_SECONDS) + 1
    request_id = kwargs.get("request_id", "")
    request_name = getattr(send_request, "__name__", repr(send_request))

    for attempt in range(1, total_attempts + 1):
        try:
            return await send_request(*args, **kwargs)

        except Exception as e:

            last_error = e

            if classify_llm_error(e) not in RETRYABLE_CATEGORIES:
                raise

            if attempt >= total_attempts:
                raise

            delay = _INFER_RETRY_DELAYS_SECONDS[attempt - 1]
            await _safe_call_handler(
                on_status_event,
                InferRequestStatusEvent(
                    state=InferRequestStateType.RETRY_SCHEDULED,
                    request_id=request_id,
                    attempt=attempt,
                    max_attempts=total_attempts,
                    retry_delay_seconds=delay,
                    error_message=str(e),
                ),
            )
            logger.warning(f"LLM infer retry scheduled: {request_id=}, {request_name=}, {attempt=}, {total_attempts=}, {delay=}, {e=}")
            await asyncio.sleep(delay)
            await _safe_call_handler(
                on_status_event,
                InferRequestStatusEvent(
                    state=InferRequestStateType.RETRYING,
                    request_id=request_id,
                    attempt=attempt + 1,
                    max_attempts=total_attempts,
                ),
            )

    assert last_error is not None
    raise last_error


async def infer(
    model: str | None,
    ctx: GtCoreAgentDialogContext,
    on_status_event: InferRequestStatusEventHandler | None = None,
) -> InferResult:
    """根据 GtCoreAgentDialogContext 组装请求并调用 LLM 推理接口，统一返回成功/失败结果。"""
    request_id = uuid.uuid4().hex
    resolved_model_name = model
    resolved_provider: str | None = None
    try:
        provider_config, model_config = resolve_model(model)
        resolved_provider = provider_config.name
        resolved_model_name = f"{model_config.name}@{provider_config.name}"

        request, applied_rules = _build_request(
            ctx=ctx,
            model_config=model_config,
        )
        logger.info(
            "LLM infer start: request_id=%s, stream=%s, model=%s, provider=%s, protocol=%s, message_count=%d, tool_count=%d, tool_choice=%s, prompt_cache=%s, applied_rules=%s",
            request_id, False, model_config.name, provider_config.name, model_config.protocol, len(request.messages), len(ctx.tools or []), request.tool_choice,
            ctx.prompt_cache, list(applied_rules),
        )
        url = get_provider_url(provider_config, model_config.protocol)
        response = await _send_with_retry(
            send_request=llmApiUtil.send_request_non_stream,
            args=(),
            kwargs={
                "request": request,
                "url": url,
                "api_key": provider_config.api_key,
                "custom_llm_provider": model_config.protocol.value,
                "extra_headers": model_config.extra_headers,
                "request_id": request_id,
            },
            on_status_event=on_status_event,
        )
        logger.info(
            "LLM infer success: request_id=%s, stream=%s, upstream_request_id=%s, usage=%s",
            request_id, False, response.request_id, _usage_to_log_json(response.usage),
        )
        return InferResult.success(response, request_id=request_id)
    except Exception as e:
        logger.exception(
            "LLM infer failed: request_id=%s, stream=%s, model=%s",
            request_id, False, model,
        )
        return InferResult.failure(e, request_id=request_id)


def shutdown() -> None:
    pass


@dataclass
class InferStreamProgress:
    """流式推理进度回调数据。"""
    delta_text: str
    current_completion_tokens: int | None = None
    current_total_tokens: int | None = None

    def to_metadata_patch(self) -> dict:
        """返回适合 metadata 浅合并的字典（排除 delta_text 和 None 值）。"""
        return {k: v for k, v in asdict(self).items() if k != "delta_text" and v is not None}


async def infer_stream(
    model: str | None,
    ctx: GtCoreAgentDialogContext,
    on_progress: Callable[[InferStreamProgress], Awaitable[None] | None] | None = None,
    on_status_event: InferRequestStatusEventHandler | None = None,
) -> InferResult:
    """流式推理：边迭代 chunk 边回调 on_progress，完成后返回与 infer() 一致的 InferResult。"""
    request_id = uuid.uuid4().hex
    resolved_model_name = model
    resolved_provider: str | None = None
    try:
        provider_config, model_config = resolve_model(model)
        resolved_provider = provider_config.name
        resolved_model_name = f"{model_config.name}@{provider_config.name}"

        request, applied_rules = _build_request(
            ctx=ctx,
            model_config=model_config,
        )
        logger.info(
            "LLM infer start: request_id=%s, stream=%s, model=%s, provider=%s, protocol=%s, message_count=%d, tool_count=%d, tool_choice=%s, prompt_cache=%s, applied_rules=%s",
            request_id, True, model_config.name, provider_config.name, model_config.protocol, len(request.messages), len(ctx.tools or []), request.tool_choice,
            ctx.prompt_cache, list(applied_rules),
        )

        completion_tokens = 0

        async def _on_chunk(chunk: llmApiUtil.ModelResponseStream) -> None:
            nonlocal completion_tokens
            if on_progress is None:
                return

            delta_text = ""
            choices = getattr(chunk, "choices", None)
            if choices and len(choices) > 0:
                delta = getattr(choices[0], "delta", None)
                if delta:
                    delta_text = getattr(delta, "content", None) or ""

            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage and getattr(chunk_usage, "completion_tokens", None) is not None:
                current_ct = chunk_usage.completion_tokens
                current_total = getattr(chunk_usage, "total_tokens", None)
            else:
                if delta_text:
                    completion_tokens += 1
                current_ct = completion_tokens
                current_total = None

            progress = InferStreamProgress(
                delta_text=delta_text,
                current_completion_tokens=current_ct,
                current_total_tokens=current_total,
            )
            result = on_progress(progress)
            if result is not None:
                import inspect
                if inspect.isawaitable(result):
                    await result

        url = get_provider_url(provider_config, model_config.protocol)
        response = await _send_with_retry(
            send_request=llmApiUtil.send_request_stream,
            args=(),
            kwargs={
                "request": request,
                "url": url,
                "api_key": provider_config.api_key,
                "custom_llm_provider": model_config.protocol.value,
                "extra_headers": model_config.extra_headers,
                "on_chunk": _on_chunk,
                "request_id": request_id,
            },
            on_status_event=on_status_event,
        )
        logger.info(
            "LLM infer success: request_id=%s, stream=%s, upstream_request_id=%s, usage=%s",
            request_id, True, response.request_id, _usage_to_log_json(response.usage),
        )
        return InferResult.success(response, request_id=request_id)
    except Exception as e:
        logger.exception(
            "LLM infer failed: request_id=%s, stream=%s, model=%s",
            request_id, True, model,
        )
        return InferResult.failure(e, request_id=request_id)

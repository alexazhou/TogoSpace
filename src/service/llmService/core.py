import asyncio
from dataclasses import asdict, dataclass
from collections.abc import Awaitable, Callable
import json
import logging
import uuid
from typing import Optional

from constants import InferRequestStateType, LlmErrorCategory, LlmProtocol, LlmProviderType
from model.coreModel.gtCoreChatModel import GtCoreAgentDialogContext
from service.llmService.llmErrorClassifier import classify_llm_error, RETRYABLE_CATEGORIES
from service.llmService.llmRequestRules import apply_llm_request_rules
from util import configUtil, llmApiUtil

from util.configTypes import LlmModelConfig, LlmProviderConfig, LlmContextConfig
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
        其中 merged_model_config 已合并 extra_headers，
        protocol 可从 model_config.protocol 获取。

    Raises:
        ValueError: 配置缺失或模型/Provider 未找到。
    """
    setting = configUtil.get_app_config().setting

    if not model_name:
        model_name = "primary@system"

    # 解析系统槽位：slot@system → 查 default_models 获取实际 model@provider
    if model_name.endswith("@system"):
        slot_name = model_name[:-7]  # 去掉 "@system"
        slot_map = {
            "primary": setting.default_models.primary,
            "lite": setting.default_models.lite,
            "vision": setting.default_models.vision,
            "advanced": setting.default_models.advanced,
        }
        model_name = slot_map.get(slot_name, "")
        if not model_name:
            raise ValueError(f"未配置有效的系统槽位：{slot_name}")

    if "@" not in model_name:
        raise ValueError(f"模型标识格式错误（应为 model@provider）：{model_name}")

    model_part, provider_name = model_name.rsplit("@", 1)

    provider_config = next((p for p in setting.llm_providers if p.name == provider_name and p.enable), None)
    if not provider_config:
        raise ValueError(f"找不到启用的提供商：{provider_name}")

    model_config = next((m for m in provider_config.models if m.name == model_part and m.enabled), None)
    if not model_config:
        raise ValueError(f"在提供商 {provider_name} 中找不到启用的模型：{model_part}")

    merged_model = model_config.model_copy(update={
        "context_config": model_config.context_config or setting.context_config,
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


def _build_request(
    *,
    model: str,
    ctx: GtCoreAgentDialogContext,
    model_config: LlmModelConfig,
    provider_config: LlmProviderConfig,
) -> tuple[llmApiUtil.OpenAIRequest, tuple[str, ...]]:
    messages: list[llmApiUtil.OpenAIMessage] = [
        llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.SYSTEM, ctx.system_prompt),
        *ctx.messages,
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
            model=model_config.name,
            ctx=ctx,
            model_config=model_config,
            provider_config=provider_config,
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
            model=model_config.name,
            ctx=ctx,
            model_config=model_config,
            provider_config=provider_config,
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

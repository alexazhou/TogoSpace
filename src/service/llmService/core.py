from dataclasses import asdict, dataclass
from collections.abc import Awaitable, Callable
import json
import logging
import uuid
from typing import Optional

from constants import LlmServiceType
from model.coreModel.gtCoreChatModel import GtCoreAgentDialogContext
from service.llmService.llmRequestRules import apply_llm_request_rules
from util import configUtil, llmApiUtil

# LiteLLM custom_llm_provider 映射表
_TYPE_TO_PROVIDER = {
    LlmServiceType.OPENAI_COMPATIBLE: "openai",
    LlmServiceType.ANTHROPIC: "anthropic",
    LlmServiceType.GOOGLE: "gemini",
    LlmServiceType.DEEPSEEK: "deepseek",
}

logger = logging.getLogger(__name__)


@dataclass
class InferResult:
    ok: bool
    response: Optional[llmApiUtil.OpenAIResponse] = None
    error_message: str = ""
    error: Optional[Exception] = None
    request_id: str = ""

    @classmethod
    def success(cls, response: llmApiUtil.OpenAIResponse, request_id: str = "") -> "InferResult":
        return cls(ok=True, response=response, request_id=request_id)

    @classmethod
    def failure(cls, error: Exception, request_id: str = "") -> "InferResult":
        return cls(ok=False, error_message=str(error), error=error, request_id=request_id)

    @property
    def usage(self) -> llmApiUtil.OpenAIUsage | None:
        if self.response is None:
            return None
        return self.response.usage


async def startup() -> None:
    setting = configUtil.get_app_config().setting
    if not setting.is_llm_configured:
        logger.warning("当前未配置可用的 LLM 服务，Agent 推理功能不可用。请通过 Web Console 或手动编辑 setting.json 完成配置。")


def get_default_model_or_none() -> str | None:
    setting = configUtil.get_app_config().setting
    llm_config = setting.current_llm_service
    if llm_config is None:
        return None
    return llm_config.model


def get_default_model() -> str:
    model = get_default_model_or_none()
    if model is None:
        raise ValueError("未配置可用的 LLM 服务（llm_services 全部被禁用或为空）")
    return model


def _usage_to_log_json(usage: llmApiUtil.OpenAIUsage | None) -> str:
    if usage is None:
        return "null"
    return json.dumps(usage.model_dump(mode="json", exclude_none=False), ensure_ascii=False, default=str)


def _build_request(
    *,
    model: str,
    ctx: GtCoreAgentDialogContext,
    llm_config,
) -> tuple[llmApiUtil.OpenAIRequest, tuple[str, ...]]:
    messages: list[llmApiUtil.OpenAIMessage] = [
        llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.SYSTEM, ctx.system_prompt),
        *ctx.messages,
    ]
    request = llmApiUtil.OpenAIRequest(
        model=model,
        messages=messages,
        tools=ctx.tools,
        tool_choice=ctx.tool_choice,
        prompt_cache=ctx.prompt_cache,
        max_tokens=llm_config.reserve_output_tokens,
        temperature=llm_config.temperature,
        provider_params=llm_config.provider_params,
    )
    return apply_llm_request_rules(request)


async def infer(model: str | None, ctx: GtCoreAgentDialogContext) -> InferResult:
    """根据 GtCoreAgentDialogContext 组装请求并调用 LLM 推理接口，统一返回成功/失败结果。"""
    request_id = uuid.uuid4().hex
    resolved_model = model
    resolved_provider: str | None = None
    try:
        llm_config = configUtil.get_app_config().setting.current_llm_service
        if llm_config is None:
            raise ValueError("未配置可用的 LLM 服务（llm_services 全部被禁用或为空）")
        resolved_model = model or llm_config.model
        resolved_provider = _TYPE_TO_PROVIDER.get(llm_config.type)
        request, applied_rules = _build_request(
            model=resolved_model,
            ctx=ctx,
            llm_config=llm_config,
        )
        logger.info(
            "LLM infer start: request_id=%s, stream=%s, model=%s, provider=%s, message_count=%d, tool_count=%d, tool_choice=%s, prompt_cache=%s, applied_rules=%s",
            request_id, False, resolved_model, resolved_provider, len(request.messages), len(ctx.tools or []), request.tool_choice,
            ctx.prompt_cache, list(applied_rules),
        )
        response = await llmApiUtil.send_request_non_stream(
            request,
            llm_config.base_url,
            llm_config.api_key,
            custom_llm_provider=resolved_provider,
            extra_headers=llm_config.extra_headers,
            request_id=request_id,
        )
        logger.info(
            "LLM infer success: request_id=%s, stream=%s, upstream_request_id=%s, usage=%s",
            request_id, False, response.request_id, _usage_to_log_json(response.usage),
        )
        return InferResult.success(response, request_id=request_id)
    except Exception as e:
        logger.exception(
            "LLM infer failed: request_id=%s, stream=%s, model=%s, provider=%s",
            request_id, False, resolved_model, resolved_provider,
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
) -> InferResult:
    """流式推理：边迭代 chunk 边回调 on_progress，完成后返回与 infer() 一致的 InferResult。"""
    request_id = uuid.uuid4().hex
    resolved_model = model
    resolved_provider: str | None = None
    try:
        llm_config = configUtil.get_app_config().setting.current_llm_service
        if llm_config is None:
            raise ValueError("未配置可用的 LLM 服务（llm_services 全部被禁用或为空）")
        resolved_model = model or llm_config.model
        resolved_provider = _TYPE_TO_PROVIDER.get(llm_config.type)
        request, applied_rules = _build_request(
            model=resolved_model,
            ctx=ctx,
            llm_config=llm_config,
        )
        logger.info(
            "LLM infer start: request_id=%s, stream=%s, model=%s, provider=%s, message_count=%d, tool_count=%d, tool_choice=%s, prompt_cache=%s, applied_rules=%s",
            request_id, True, resolved_model, resolved_provider, len(request.messages), len(ctx.tools or []), request.tool_choice,
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

        response = await llmApiUtil.send_request_stream(
            request,
            llm_config.base_url,
            llm_config.api_key,
            custom_llm_provider=resolved_provider,
            extra_headers=llm_config.extra_headers,
            on_chunk=_on_chunk,
            request_id=request_id,
        )
        logger.info(
            "LLM infer success: request_id=%s, stream=%s, upstream_request_id=%s, usage=%s",
            request_id, True, response.request_id, _usage_to_log_json(response.usage),
        )
        return InferResult.success(response, request_id=request_id)
    except Exception as e:
        logger.exception(
            "LLM infer failed: request_id=%s, stream=%s, model=%s, provider=%s",
            request_id, True, resolved_model, resolved_provider,
        )
        return InferResult.failure(e, request_id=request_id)

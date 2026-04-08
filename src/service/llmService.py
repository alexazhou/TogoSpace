from dataclasses import asdict, dataclass
from collections.abc import Awaitable, Callable
import json
import logging
import uuid
from typing import Optional

from constants import LlmServiceType
from model.coreModel.gtCoreChatModel import GtCoreAgentDialogContext
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
    _ = configUtil.get_app_config().setting.current_llm_service


def get_default_model() -> str:
    llm_config = configUtil.get_app_config().setting.current_llm_service
    return llm_config.model


def _usage_to_log_json(usage: llmApiUtil.OpenAIUsage | None) -> str:
    if usage is None:
        return "null"
    return json.dumps(usage.model_dump(mode="json", exclude_none=False), ensure_ascii=False, default=str)


async def infer(model: str | None, ctx: GtCoreAgentDialogContext) -> InferResult:
    """根据 GtCoreAgentDialogContext 组装请求并调用 LLM 推理接口，统一返回成功/失败结果。"""
    request_id = uuid.uuid4().hex
    resolved_model = model
    resolved_provider: str | None = None
    try:
        llm_config = configUtil.get_app_config().setting.current_llm_service
        resolved_model = model or llm_config.model
        resolved_provider = _TYPE_TO_PROVIDER.get(llm_config.type)

        messages: list[llmApiUtil.OpenAIMessage] = [
            llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.SYSTEM, ctx.system_prompt),
            *ctx.messages,
        ]
        request = llmApiUtil.OpenAIRequest(
            model=resolved_model,
            messages=messages,
            tools=ctx.tools,
            tool_choice=ctx.tool_choice,
        )
        logger.info(
            "LLM infer start: request_id=%s, stream=%s, model=%s, provider=%s, message_count=%d, tool_count=%d, tool_choice=%s",
            request_id, False, resolved_model, resolved_provider, len(messages), len(ctx.tools or []), ctx.tool_choice,
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
        resolved_model = model or llm_config.model
        resolved_provider = _TYPE_TO_PROVIDER.get(llm_config.type)

        messages: list[llmApiUtil.OpenAIMessage] = [
            llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.SYSTEM, ctx.system_prompt),
            *ctx.messages,
        ]
        request = llmApiUtil.OpenAIRequest(
            model=resolved_model,
            messages=messages,
            tools=ctx.tools,
            tool_choice=ctx.tool_choice,
        )
        logger.info(
            "LLM infer start: request_id=%s, stream=%s, model=%s, provider=%s, message_count=%d, tool_count=%d, tool_choice=%s",
            request_id, True, resolved_model, resolved_provider, len(messages), len(ctx.tools or []), ctx.tool_choice,
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

            # token 统计：优先使用 chunk 自带 usage
            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage and getattr(chunk_usage, "completion_tokens", None) is not None:
                current_ct = chunk_usage.completion_tokens
                current_total = getattr(chunk_usage, "total_tokens", None)
            else:
                # 本地估算：每个非空 delta 算 1 token
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

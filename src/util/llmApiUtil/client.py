import json
import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import litellm
from litellm.litellm_core_utils.streaming_handler import CustomStreamWrapper
from litellm.types.utils import ModelResponse, ModelResponseStream, TextCompletionResponse
from constants import OpenaiApiRole
from .OpenAiModels import (
    OpenAIFunction,
    OpenAIFunctionParameter,
    OpenAIMessage,
    OpenAIRequest,
    OpenAIResponse,
    OpenAITool,
)


logger = logging.getLogger(__name__)
_REDACTED_HEADER_KEYS = {"authorization", "api-key", "x-api-key", "proxy-authorization"}


def _patch_responses_api_streaming() -> None:
    """Monkey-patch litellm，修复 Responses API 流式 tool_calls 丢失的问题。

    根因：部分代理的 /v1/responses SSE 只发一条 response.completed 事件
    （包含完整 output），而非标准的逐条 response.output_item.added + delta 序列。
    litellm 的 response.completed handler 只设 finish_reason="tool_calls"，不填
    delta.tool_calls，导致 stream_chunk_builder 聚合后 tool_calls 为空。

    修复策略：
    - 抑制中间的 function_call 流式事件（output_item.added / arguments.delta /
      output_item.done），避免 stream_chunk_builder 重复累加 arguments；
    - 在 response.completed 里从 output[] 提取完整 tool_calls 注入 delta。

    这样无论服务端只发 response.completed 还是发完整事件序列，结果均正确。
    """
    from litellm.completion_extras.litellm_responses_transformation.transformation import (
        OpenAiResponsesToChatCompletionStreamIterator,
    )
    from litellm.types.llms.openai import ChatCompletionToolCallFunctionChunk
    from litellm.types.utils import (
        ChatCompletionToolCallChunk,
        Delta,
        ModelResponseStream,
        StreamingChoices,
    )

    _orig = OpenAiResponsesToChatCompletionStreamIterator.translate_responses_chunk_to_openai_stream

    def _patched(parsed_chunk):  # type: ignore[no-untyped-def]
        from pydantic import BaseModel
        if isinstance(parsed_chunk, BaseModel):
            parsed_chunk = parsed_chunk.model_dump()

        event_type = parsed_chunk.get("type", "") if isinstance(parsed_chunk, dict) else ""
        if hasattr(event_type, "value"):
            event_type = event_type.value

        # 抑制中间的 function_call 流式事件；tool_calls 统一在 response.completed 注入，
        # 防止 stream_chunk_builder 将 arguments 累加两次。
        if event_type == "response.function_call_arguments.delta":
            return ModelResponseStream(
                choices=[StreamingChoices(index=0, delta=Delta(), finish_reason=None)]
            )
        if event_type in ("response.output_item.added", "response.output_item.done"):
            item = parsed_chunk.get("item", {}) if isinstance(parsed_chunk, dict) else {}
            if isinstance(item, dict) and item.get("type") == "function_call":
                return ModelResponseStream(
                    choices=[StreamingChoices(index=0, delta=Delta(), finish_reason=None)]
                )

        result = _orig(parsed_chunk)

        # 在 response.completed 里从 output[] 提取完整 tool_calls 注入 delta
        if (
            event_type == "response.completed"
            and result.choices
            and result.choices[0].finish_reason == "tool_calls"
            and not result.choices[0].delta.tool_calls
        ):
            response_data = parsed_chunk.get("response", {}) if isinstance(parsed_chunk, dict) else {}
            output_items = response_data.get("output", []) if response_data else []
            tool_calls = []
            tool_call_index = 0
            for item in output_items:
                if not isinstance(item, dict) or item.get("type") != "function_call":
                    continue
                tool_calls.append(
                    ChatCompletionToolCallChunk(
                        id=item.get("call_id"),
                        index=tool_call_index,
                        type="function",
                        function=ChatCompletionToolCallFunctionChunk(
                            name=item.get("name"),
                            arguments=item.get("arguments", "{}"),
                        ),
                    )
                )
                tool_call_index += 1
            if tool_calls:
                result.choices[0].delta.tool_calls = tool_calls  # type: ignore[assignment]

        return result

    OpenAiResponsesToChatCompletionStreamIterator.translate_responses_chunk_to_openai_stream = staticmethod(_patched)  # type: ignore[method-assign]


def init() -> None:
    """初始化 llmApiUtil。使用 litellm 后，此方法主要用于设置全局配置。"""

    # 在这里设置 litellm 的全局配置，例如

    # 关闭所有的调试信息和内置的 print 提示（解决 Provider List 等刷屏问题）
    litellm.suppress_debug_info = True

    # 确保详细模式被关闭
    litellm.set_verbose = False

    # 自动丢弃模型不支持的参数（如 GPT-5 不支持 temperature != 1）
    litellm.drop_params = True

    # 修复 Responses API 流式 tool_calls 丢失问题
    _patch_responses_api_streaming()


def _clean_base_url(url: str) -> str:
    """清理 base_url，移除末尾可能存在的 /chat/completions 路径，防止 litellm 重复拼接。"""
    if not url:
        return url
    
    base_url = url
    if base_url.endswith("/chat/completions"):
        base_url = base_url[:-len("/chat/completions")]
    elif base_url.endswith("/chat/completions/"):
        base_url = base_url[:-len("/chat/completions/")]
    
    return base_url.rstrip("/")


def _build_request_payload(request: OpenAIRequest) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]] | None]:
    model_name = request.model
    messages = [m.to_dict() for m in request.messages]
    tools: list[dict[str, Any]] | None = None
    if request.tools:
        tools = [t.model_dump(exclude_none=True) for t in request.tools]
    return model_name, messages, tools


def _sanitize_headers(headers: dict[str, str] | None) -> dict[str, str] | None:
    if headers is None:
        return None
    sanitized: dict[str, str] = {}
    for key, value in headers.items():
        if key.lower() in _REDACTED_HEADER_KEYS or "token" in key.lower():
            sanitized[key] = "***"
        else:
            sanitized[key] = value
    return sanitized


def _to_log_data(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=False)
    if isinstance(value, dict):
        return {k: _to_log_data(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_log_data(v) for v in value]
    return value


def _to_log_json(value: Any) -> str:
    return json.dumps(_to_log_data(value), ensure_ascii=False, default=str)


def _request_payload_for_log(request: OpenAIRequest, *, stream: bool) -> dict[str, Any]:
    payload = request.model_dump(mode="json", exclude_none=True)
    payload["stream"] = stream
    return payload


# LiteLLM 会在这两个位置自动注入 cache_control: ephemeral，触发 Anthropic prompt cache。
# 对不支持缓存的 provider，LiteLLM 会静默忽略此参数。
_CACHE_INJECTION_POINTS = [
    {"location": "message", "role": "system"},   # system prompt 通常最稳定，优先缓存
    {"location": "message", "index": -1},        # 最后一条消息作为第二个缓存边界
]

_AGENT_PROBE_TOOLS = [
    OpenAITool(
        function=OpenAIFunction(
            name="send_chat_msg",
            description="向聊天窗口发送消息",
            parameters=OpenAIFunctionParameter(
                type="object",
                properties={
                    "room_name": {"type": "string", "description": "要发送消息的窗口名称"},
                    "msg": {"type": "string", "description": "要发送的消息"},
                },
                required=["room_name", "msg"],
            ),
        )
    ),
    OpenAITool(
        function=OpenAIFunction(
            name="finish_chat_turn",
            description="结束本轮行动",
            parameters=OpenAIFunctionParameter(
                type="object",
                properties={},
                required=[],
            ),
        )
    ),
]


def build_agent_probe_request(
    *,
    model: str,
    provider_params: dict[str, Any] | None = None,
) -> OpenAIRequest:
    """构造一个尽量贴近真实 Agent 推理路径的最小探测请求。"""
    return OpenAIRequest(
        model=model,
        messages=[
            OpenAIMessage.text(
                OpenaiApiRole.SYSTEM,
                "你是一个团队协作 Agent。你需要通过工具完成行动，并在结束时调用 finish_chat_turn。",
            ),
            OpenAIMessage.text(
                OpenaiApiRole.USER,
                "请做一次最小响应。如果你可以调用工具，请自行决定是否调用；完成后结束本轮。",
            ),
        ],
        max_tokens=16,
        stream=True,
        tools=_AGENT_PROBE_TOOLS,
        tool_choice=None,
        prompt_cache=True,
        provider_params=provider_params or {},
    )


def _build_litellm_extra_params(request: OpenAIRequest) -> dict[str, Any]:
    extra_params: dict[str, Any] = {}
    if request.prompt_cache:
        extra_params["cache_control_injection_points"] = _CACHE_INJECTION_POINTS

    extra_params.update(request.provider_params or {})
    return extra_params


async def send_request_stream(
    request: OpenAIRequest,
    url: str,
    api_key: str,
    custom_llm_provider: str | None = None,
    extra_headers: dict[str, str] | None = None,
    on_chunk: Callable[[ModelResponseStream], Awaitable[None] | None] | None = None,
    request_id: str = "",
) -> OpenAIResponse:
    """流式请求上游模型，并在本地聚合为完整 OpenAIResponse。

    若提供 on_chunk，每收到一个 chunk 后立即回调（支持同步和异步回调）。
    """
    model_name, messages, tools = _build_request_payload(request)
    base_url = _clean_base_url(url)
    logger.info(
        "LLM upstream request start: request_id=%s, stream=%s, provider=%s, base_url=%s, extra_headers=%s, prompt_cache=%s, payload=%s",
        request_id, True, custom_llm_provider, base_url, _to_log_json(_sanitize_headers(extra_headers)),
        request.prompt_cache,
        _to_log_json(_request_payload_for_log(request, stream=True)),
    )

    try:
        extra_params = _build_litellm_extra_params(request)
        stream_resp: ModelResponse | CustomStreamWrapper = await litellm.acompletion(
            model=model_name,
            custom_llm_provider=custom_llm_provider,
            messages=messages,
            api_key=api_key,
            base_url=base_url,
            tools=tools,
            tool_choice=request.tool_choice,
            extra_headers=extra_headers,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            stream=True,
            **extra_params,
        )
        if not isinstance(stream_resp, CustomStreamWrapper):
            raise TypeError(f"期望流式响应类型 CustomStreamWrapper，实际为: {type(stream_resp).__name__}")

        chunks: list[ModelResponseStream] = []
        async for chunk in stream_resp:
            if not isinstance(chunk, ModelResponseStream):
                raise TypeError(f"期望流式 chunk 类型 ModelResponseStream，实际为: {type(chunk).__name__}")
            chunks.append(chunk)
            logger.info(
                "LLM upstream stream chunk: request_id=%s, chunk_index=%d, payload=%s",
                request_id, len(chunks), _to_log_json(chunk),
            )
            if on_chunk is not None:
                result = on_chunk(chunk)
                if inspect.isawaitable(result):
                    await result

        merged: ModelResponse | TextCompletionResponse | None = litellm.stream_chunk_builder(chunks=chunks, messages=messages)
        if merged is None:
            raise RuntimeError("流式聚合失败：未生成完整响应")
        if isinstance(merged, TextCompletionResponse):
            raise TypeError("流式聚合返回了 TextCompletionResponse；当前仅支持 ChatCompletion 的 ModelResponse")
        if not isinstance(merged, ModelResponse):
            raise TypeError(f"流式聚合返回了未知类型: {type(merged).__name__}")

        logger.info(
            "LLM upstream request success: request_id=%s, stream=%s, chunk_count=%d, payload=%s",
            request_id, True, len(chunks), _to_log_json(merged),
        )
        return OpenAIResponse.model_validate(merged.model_dump(exclude_none=False))
    except Exception:
        logger.exception("LLM upstream request failed: request_id=%s, stream=%s", request_id, True)
        raise


async def send_request_non_stream(
    request: OpenAIRequest,
    url: str,
    api_key: str,
    custom_llm_provider: str | None = None,
    extra_headers: dict[str, str] | None = None,
    request_id: str = "",
) -> OpenAIResponse:
    """非流式请求上游模型，直接返回完整 OpenAIResponse。"""
    model_name, messages, tools = _build_request_payload(request)
    base_url = _clean_base_url(url)
    logger.info(
        "LLM upstream request start: request_id=%s, stream=%s, provider=%s, base_url=%s, extra_headers=%s, prompt_cache=%s, payload=%s",
        request_id, False, custom_llm_provider, base_url, _to_log_json(_sanitize_headers(extra_headers)),
        request.prompt_cache,
        _to_log_json(_request_payload_for_log(request, stream=False)),
    )

    try:
        extra_params = _build_litellm_extra_params(request)
        response: ModelResponse | CustomStreamWrapper = await litellm.acompletion(
            model=model_name,
            custom_llm_provider=custom_llm_provider,
            messages=messages,
            api_key=api_key,
            base_url=base_url,
            tools=tools,
            tool_choice=request.tool_choice,
            extra_headers=extra_headers,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            stream=False,
            **extra_params,
        )
        if not isinstance(response, ModelResponse):
            raise TypeError(f"期望非流式响应类型 ModelResponse，实际为: {type(response).__name__}")
        logger.info(
            "LLM upstream request success: request_id=%s, stream=%s, payload=%s",
            request_id, False, _to_log_json(response),
        )
        return OpenAIResponse.model_validate(response.model_dump(exclude_none=False))
    except Exception:
        logger.exception("LLM upstream request failed: request_id=%s, stream=%s", request_id, False)
        raise

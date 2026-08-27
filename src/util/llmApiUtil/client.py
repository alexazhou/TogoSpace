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


# DeepSeek R1/V4 等 thinking mode 模型要求：所有 assistant 消息都必须包含 reasoning_content 字段。
# 即使历史消息中没有 reasoning_content（如 compact 前的消息或切换模型前的消息），
# 也必须补上空字符串，否则 API 报错 "The reasoning_content in the thinking mode must be passed back to the API."
_THINKING_MODE_MODEL_PREFIXES = (
    "deepseek-r1",
    "deepseek-reasoner",
    "deepseek-v4",
    "deepseek-pro",
)


def _is_thinking_mode_model(model: str) -> bool:
    """判断模型是否为 thinking mode 模型（需要 reasoning_content 字段）。"""
    model_lower = model.lower()
    return any(model_lower.startswith(prefix) for prefix in _THINKING_MODE_MODEL_PREFIXES)


def _build_request_payload(request: OpenAIRequest) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]] | None]:
    model_name = request.model
    messages = [m.to_dict() for m in request.messages]

    # Thinking mode 模型要求所有 assistant 消息包含 reasoning_content 字段。
    # 两种情况需要补全：
    # 1. 模型本身是 thinking mode 模型（如 deepseek-v4-pro）
    # 2. 对话中已存在带 reasoning_content 的 assistant 消息（可能是从 thinking 模型切换过来的）
    needs_padding = _is_thinking_mode_model(model_name) or any(
        m.role == OpenaiApiRole.ASSISTANT and m.reasoning_content is not None
        for m in request.messages
    )
    if needs_padding:
        for msg_dict in messages:
            if msg_dict.get("role") == "assistant" and "reasoning_content" not in msg_dict:
                msg_dict["reasoning_content"] = ""

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


def _log_raw_response(error: Exception, request_id: str, stream: bool) -> None:
    """当 LLM 请求异常时，尝试从异常链中提取并记录上游 API 的原始响应体。

    部分上游 API（如 DeepSeek）在特定错误场景下会返回非标准 JSON 响应，
    导致 SDK 的 JSON 解析失败（如 'Expecting value: line 1 column 67'）。
    此函数从异常对象中提取 httpx.Response 和 body 信息，记录到日志以便排查。

    此函数绝不抛出异常——它是诊断辅助函数，不应影响主流程的错误传播。
    """
    try:
        raw_body = None
        status_code = None
        response_url = None

        # 从异常链中寻找原始响应信息
        current = error
        while current is not None:
            # litellm BadRequestError 等异常携带 response 和 body 属性
            response_obj = getattr(current, "response", None)
            if response_obj is not None:
                try:
                    status_code = getattr(response_obj, "status_code", None)
                    response_url = str(getattr(response_obj, "url", ""))
                    # 尝试读取原始响应体
                    raw_body = getattr(response_obj, "_content", None)
                    if raw_body is None:
                        raw_body = getattr(response_obj, "text", None)
                    elif isinstance(raw_body, bytes):
                        raw_body = raw_body.decode("utf-8", errors="replace")
                except Exception:
                    pass

            # litellm 异常有 body 属性（dict 或原始内容）
            body = getattr(current, "body", None)
            if body is not None and raw_body is None:
                try:
                    if isinstance(body, (dict, list)):
                        raw_body = json.dumps(body, ensure_ascii=False, default=str)
                    elif isinstance(body, str):
                        raw_body = body
                    elif isinstance(body, bytes):
                        raw_body = body.decode("utf-8", errors="replace")
                except Exception:
                    raw_body = f"<unserializable body, type={type(body).__name__}>"

            if raw_body is not None:
                break

            current = current.__cause__ or current.__context__  # type: ignore[assignment]

        if raw_body is not None or status_code is not None:
            # 截断过长的响应体，避免日志膨胀
            if isinstance(raw_body, str) and len(raw_body) > 4096:
                raw_body = raw_body[:4096] + f"... (truncated, total {len(raw_body)} bytes)"
            logger.error(
                "LLM upstream raw response: request_id=%s, stream=%s, status_code=%s, url=%s, raw_body=%s",
                request_id, stream, status_code, response_url, raw_body,
            )
        else:
            logger.warning(
                "LLM upstream raw response unavailable: request_id=%s, stream=%s, error_type=%s",
                request_id, stream, type(error).__name__,
            )
    except Exception:
        # 诊断函数绝不能影响主流程，任何异常都静默吞掉
        logger.warning(
            "LLM upstream raw response logging failed: request_id=%s, stream=%s",
            request_id, stream,
            exc_info=True,
        )

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
            name="finish_action",
            description="结束行动",
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
    extra_params: dict[str, Any] | None = None,
) -> OpenAIRequest:
    """构造一个尽量贴近真实 Agent 推理路径的最小探测请求。"""
    return OpenAIRequest(
        model=model,
        messages=[
            OpenAIMessage.text(
                OpenaiApiRole.SYSTEM,
                "你是一个团队协作 Agent。你需要通过工具完成行动，并在结束时调用 finish_action。",
            ),
            OpenAIMessage.text(
                OpenaiApiRole.USER,
                "请做一次最小响应。如果你可以调用工具，请自行决定是否调用；完成后结束行动。",
            ),
        ],
        max_tokens=16,
        stream=True,
        tools=_AGENT_PROBE_TOOLS,
        tool_choice=None,
        prompt_cache=True,
        extra_params=extra_params or {},
    )


def _build_litellm_extra_params(request: OpenAIRequest) -> dict[str, Any]:
    extra_params: dict[str, Any] = {}
    if request.prompt_cache:
        extra_params["cache_control_injection_points"] = _CACHE_INJECTION_POINTS

    extra_params.update(request.extra_params or {})
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
    except Exception as e:
        _log_raw_response(e, request_id, stream=True)
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
    except Exception as e:
        _log_raw_response(e, request_id, stream=False)
        logger.exception("LLM upstream request failed: request_id=%s, stream=%s", request_id, False)
        raise

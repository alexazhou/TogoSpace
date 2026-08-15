"""litellm 运行时 monkeypatch 集中地。

对 litellm 的临时修复统一放这里，保持 client.py 聚焦于请求/响应的
构建与发送。每个补丁应自包含（依赖在函数体内 import），并说明根因。
"""


def patch_responses_api_streaming() -> None:
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

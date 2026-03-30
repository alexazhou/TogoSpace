import logging
from typing import Any

import litellm
from litellm.litellm_core_utils.streaming_handler import CustomStreamWrapper
from litellm.types.utils import ModelResponse, ModelResponseStream, TextCompletionResponse
from .OpenAiModels import OpenAIRequest, OpenAIResponse


logger = logging.getLogger(__name__)


def init() -> None:
    """初始化 llmApiUtil。使用 litellm 后，此方法主要用于设置全局配置。"""

    # 在这里设置 litellm 的全局配置，例如

    # 关闭所有的调试信息和内置的 print 提示（解决 Provider List 等刷屏问题）
    litellm.suppress_debug_info = True

    # 确保详细模式被关闭
    litellm.set_verbose = False


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


async def send_request_stream(
    request: OpenAIRequest,
    url: str,
    api_key: str,
    custom_llm_provider: str | None = None,
) -> OpenAIResponse:
    """流式请求上游模型，并在本地聚合为完整 OpenAIResponse。"""
    model_name, messages, tools = _build_request_payload(request)
    base_url = _clean_base_url(url)

    stream_resp: ModelResponse | CustomStreamWrapper = await litellm.acompletion(
        model=model_name,
        custom_llm_provider=custom_llm_provider,
        messages=messages,
        api_key=api_key,
        base_url=base_url,
        tools=tools,
        temperature=request.temperature,
        max_tokens=request.max_tokens,
        stream=True,
    )
    if not isinstance(stream_resp, CustomStreamWrapper):
        raise TypeError(f"期望流式响应类型 CustomStreamWrapper，实际为: {type(stream_resp).__name__}")

    chunks: list[ModelResponseStream] = []
    async for chunk in stream_resp:
        if not isinstance(chunk, ModelResponseStream):
            raise TypeError(f"期望流式 chunk 类型 ModelResponseStream，实际为: {type(chunk).__name__}")
        chunks.append(chunk)

    merged: ModelResponse | TextCompletionResponse | None = litellm.stream_chunk_builder(chunks=chunks, messages=messages)
    if merged is None:
        raise RuntimeError("流式聚合失败：未生成完整响应")
    if isinstance(merged, TextCompletionResponse):
        raise TypeError("流式聚合返回了 TextCompletionResponse；当前仅支持 ChatCompletion 的 ModelResponse")
    if not isinstance(merged, ModelResponse):
        raise TypeError(f"流式聚合返回了未知类型: {type(merged).__name__}")

    return OpenAIResponse.model_validate(merged.model_dump(exclude_none=False))


async def send_request_non_stream(
    request: OpenAIRequest,
    url: str,
    api_key: str,
    custom_llm_provider: str | None = None,
) -> OpenAIResponse:
    """非流式请求上游模型，直接返回完整 OpenAIResponse。"""
    model_name, messages, tools = _build_request_payload(request)
    base_url = _clean_base_url(url)

    response: ModelResponse | CustomStreamWrapper = await litellm.acompletion(
        model=model_name,
        custom_llm_provider=custom_llm_provider,
        messages=messages,
        api_key=api_key,
        base_url=base_url,
        tools=tools,
        temperature=request.temperature,
        max_tokens=request.max_tokens,
        stream=False,
    )
    if not isinstance(response, ModelResponse):
        raise TypeError(f"期望非流式响应类型 ModelResponse，实际为: {type(response).__name__}")
    return OpenAIResponse.model_validate(response.model_dump(exclude_none=False))

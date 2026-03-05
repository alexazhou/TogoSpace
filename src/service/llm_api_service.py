import logging
import ssl
from typing import Optional, List

import aiohttp
import certifi

from model.api_model import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ErrorResponse
)

logger = logging.getLogger(__name__)

DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

_api_key: str = ""
_session: Optional[aiohttp.ClientSession] = None


def init(api_key: str, base_url: str = DASHSCOPE_BASE_URL) -> None:
    """初始化模块：设置 api_key 并创建全局 session。须在首次调用 send_request 前调用一次。"""
    global _api_key, _session, DASHSCOPE_BASE_URL
    _api_key = api_key
    DASHSCOPE_BASE_URL = base_url
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    connector = aiohttp.TCPConnector(ssl=ssl_context)
    _session = aiohttp.ClientSession(connector=connector)
    logger.info("aiohttp.ClientSession created")


async def send_request(
    model: str,
    messages: list,
    max_tokens: int = 1024,
    temperature: float = 0.7,
    tools: Optional[List] = None,
) -> ChatCompletionResponse:
    """发送 chat completion 请求，复用模块全局 session。"""
    if _session is None:
        raise RuntimeError("api_client_service 未初始化，请先调用 init(api_key)")

    headers = {
        "Authorization": f"Bearer {_api_key}",
        "Content-Type": "application/json",
    }

    request = ChatCompletionRequest(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
        tools=tools
    )
    payload = request.model_dump(exclude_none=True)

    logger.info("=== 请求 payload ===")
    logger.info(f"Model: {model}")
    logger.info(f"Messages count: {len(messages)}")

    async with _session.post(DASHSCOPE_BASE_URL, headers=headers, json=payload) as response:
        response_data = await response.json()
        status = response.status

    logger.info("=== API 响应数据 ===")
    logger.info(f"Status: {status}")
    logger.info(f"Data: {response_data}")

    if status == 200:
        logger.info("=== API 响应成功 ===")
        return ChatCompletionResponse.model_validate(response_data)

    if 'error' in response_data:
        error_msg = response_data['error'].get('message', 'Unknown error')
        error_code = response_data['error'].get('code', str(status))
    else:
        try:
            error = ErrorResponse.model_validate(response_data)
            error_msg = error.message
            error_code = error.code
        except Exception:
            error_msg = str(response_data)
            error_code = str(status)
    raise RuntimeError(f"API 调用失败: {error_code} - {error_msg}")


async def close() -> None:
    """关闭全局 session，程序退出前调用。"""
    global _session
    if _session is not None:
        await _session.close()
        _session = None
        logger.info("aiohttp.ClientSession closed")

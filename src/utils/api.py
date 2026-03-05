import json
import logging
import os
import ssl
from typing import Optional, List

import aiohttp
import certifi

from model import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ErrorResponse
)

logger = logging.getLogger(__name__)


def _load_api_key() -> str:
    config_path = os.path.join(os.path.dirname(__file__), "../../config.json")
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)["anthropic"]["api_key"]


async def call_chat_completion(
    model: str,
    messages: list,
    max_tokens: int = 1024,
    temperature: float = 0.7,
    tools: Optional[List] = None
) -> ChatCompletionResponse:
    """无状态的 Chat Completion 调用函数"""
    api_key = _load_api_key()
    url = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

    headers = {
        "Authorization": f"Bearer {api_key}",
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

    logger.info(f"=== 请求 payload ===")
    logger.info(f"Model: {model}")
    logger.info(f"Messages count: {len(messages)}")

    ssl_context = ssl.create_default_context(cafile=certifi.where())
    connector = aiohttp.TCPConnector(ssl=ssl_context)

    async with aiohttp.ClientSession(connector=connector) as session:
        async with session.post(url, headers=headers, json=payload) as response:
            response_data = await response.json()
            status = response.status

    logger.info(f"=== API 响应数据 ===")
    logger.info(f"Status: {status}")
    logger.info(f"Data: {response_data}")

    if status == 200:
        logger.info(f"=== API 响应成功 ===")
        return ChatCompletionResponse.model_validate(response_data)
    else:
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

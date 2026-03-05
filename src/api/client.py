import aiohttp
import asyncio
import logging
import ssl

import certifi
import sys
import os

# 添加父目录到 path 以导入 model
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

from model import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ErrorResponse
)

# 创建独立的 logger
logger = logging.getLogger(__name__)


class APIClient:
    """API 客户端类"""

    def __init__(self, api_key: str, session: aiohttp.ClientSession = None):
        self.api_key = api_key
        self._session = session
        self._owns_session = session is None

    async def __aenter__(self):
        if self._owns_session:
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            self._session = aiohttp.ClientSession(connector=connector)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._owns_session and self._session:
            await self._session.close()

    async def call_chat_completion(
        self,
        model: str,
        messages: list,
        max_tokens: int = 1024,
        temperature: float = 0.7
    ) -> ChatCompletionResponse:
        """调用 Chat Completion API"""
        url = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        # 构建请求
        request = ChatCompletionRequest(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature
        )

        payload = request.model_dump(exclude_none=True)

        logger.info(f"=== 请求 payload ===")
        logger.info(f"Model: {model}")
        logger.info(f"Messages count: {len(messages)}")

        async with self._session.post(url, headers=headers, json=payload) as response:
            response_data = await response.json()

        logger.info(f"=== API 响应数据 ===")
        logger.info(f"Status: {response.status}")
        logger.info(f"Data: {response_data}")

        if response.status == 200:
            logger.info(f"=== API 响应成功 ===")
            return ChatCompletionResponse.model_validate(response_data)
        else:
            # 兼容两种错误格式
            if 'error' in response_data:
                error_msg = response_data['error'].get('message', 'Unknown error')
                error_code = response_data['error'].get('code', str(response.status))
            else:
                try:
                    error = ErrorResponse.model_validate(response_data)
                    error_msg = error.message
                    error_code = error.code
                except:
                    error_msg = str(response_data)
                    error_code = str(response.status)
            raise RuntimeError(f"API 调用失败: {error_code} - {error_msg}")

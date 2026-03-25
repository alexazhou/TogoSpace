import logging
import ssl
from typing import Optional

import aiohttp
import certifi

from .models import OpenAIRequest, OpenAIResponse, OpenAIErrorResponse


logger = logging.getLogger(__name__)

_session: Optional[aiohttp.ClientSession] = None


def init() -> None:
    global _session
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    connector = aiohttp.TCPConnector(ssl=ssl_context)
    _session = aiohttp.ClientSession(connector=connector)


async def send_request(request: OpenAIRequest, url: str, api_key: str) -> OpenAIResponse:
    """发送 chat completion 请求。"""
    if _session is None:
        raise RuntimeError("llmApiUtil 未初始化，请先调用 init()")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        **request.model_dump(exclude_none=True, exclude={"messages"}),
        "messages": [m.to_dict() for m in request.messages],
    }

    async with _session.post(url, headers=headers, json=payload) as response:
        response_data = await response.json()
        status = response.status

    if status == 200:
        return OpenAIResponse.model_validate(response_data)

    if 'error' in response_data:
        error_msg = response_data['error'].get('message', 'Unknown error')
        error_code = response_data['error'].get('code', str(status))
    else:
        try:
            error = OpenAIErrorResponse.model_validate(response_data)
            error_msg = error.message
            error_code = error.code
        except Exception:
            error_msg = str(response_data)
            error_code = str(status)
    raise RuntimeError(f"API 调用失败: {error_code} - {error_msg}")

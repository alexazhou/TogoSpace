from __future__ import annotations

import ssl
import time
from typing import Any

import aiohttp
import certifi
from jsonpath_ng.ext import parse

from constants import ThirdPartyServiceName
from util import configUtil

from .result import ThirdPartySearchResult


MIMO_SERVICE_NAME = ThirdPartyServiceName.XIAOMI_MIMO.value
MIMO_SEARCH_URL = "https://api.xiaomimimo.com/v1/chat/completions"
MIMO_SEARCH_MODEL = "mimo-v2.5"
MIMO_MAX_KEYWORD = 3
MIMO_SEARCH_LIMIT = 5

MIMO_MESSAGE_PATH = parse("$.choices[0].message")
MIMO_CITATION_PATH = parse("$.choices[0].message.annotations[?(@.type == 'url_citation')]")


def _build_search_payload(query: str) -> dict[str, Any]:
    return {
        "model": MIMO_SEARCH_MODEL,
        "messages": [
            {
                "role": "user",
                "content": query,
            }
        ],
        "tools": [
            {
                "type": "web_search",
                "max_keyword": MIMO_MAX_KEYWORD,
                "force_search": True,
                "limit": MIMO_SEARCH_LIMIT,
            }
        ],
        "tool_choice": "auto",
        "max_completion_tokens": 1024,
        "stream": False,
        "thinking": {
            "type": "disabled",
        },
    }


def _extract_response(payload: dict[str, Any]) -> tuple[str, list[dict[str, Any]]] | None:
    messages = MIMO_MESSAGE_PATH.find(payload)

    if not messages or not isinstance(messages[0].value, dict):
        return None

    message = messages[0].value
    content = message.get("content")
    sources: list[dict[str, Any]] = []
    for match in MIMO_CITATION_PATH.find(payload):
        annotation = match.value

        if not isinstance(annotation, dict):
            continue

        url = annotation.get("url")

        if not isinstance(url, str) or not url:
            continue

        source: dict[str, Any] = {"url": url}
        for key in ("title", "summary", "site_name", "publish_time", "logo_url"):
            value = annotation.get(key)

            if isinstance(value, str) and value:
                source[key] = value
        sources.append(source)
    return (content if isinstance(content, str) else ""), sources


async def _search_with_api_key(api_key: str, query: str) -> ThirdPartySearchResult:
    normalized_query = query.strip()

    if not normalized_query:
        return ThirdPartySearchResult.failure(MIMO_SERVICE_NAME, "搜索 query 不能为空")

    if not api_key.strip():
        return ThirdPartySearchResult.failure(MIMO_SERVICE_NAME, "Xiaomi MiMo API Key 未配置")

    payload = _build_search_payload(normalized_query)
    headers = {
        "api-key": api_key.strip(),
        "Content-Type": "application/json",
    }
    timeout = aiohttp.ClientTimeout(total=90)
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    start_time = time.monotonic()

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                MIMO_SEARCH_URL,
                json=payload,
                headers=headers,
                ssl=ssl_context,
            ) as response:
                duration_ms = int((time.monotonic() - start_time) * 1000)
                await response.text()

                assert response.status < 400, f"Xiaomi MiMo 搜索请求失败: HTTP {response.status}"

                data = await response.json()

    except Exception as exc:
        duration_ms = int((time.monotonic() - start_time) * 1000)
        error_message = "Xiaomi MiMo 搜索请求处理失败: " + str(exc)

        return ThirdPartySearchResult.failure(
            MIMO_SERVICE_NAME, error_message, duration_ms,
        )

    extracted = _extract_response(data)

    if extracted is None:
        return ThirdPartySearchResult.failure(
            MIMO_SERVICE_NAME, "Xiaomi MiMo 搜索响应格式无效", duration_ms,
        )

    content, sources = extracted
    return ThirdPartySearchResult(
        success=True,
        service=MIMO_SERVICE_NAME,
        content=content,
        sources=sources,
        duration_ms=duration_ms,
    )


async def search(query: str) -> ThirdPartySearchResult:
    api_key = configUtil.get_app_config().setting.third_party_services.xiaomi_mimo.api_key
    return await _search_with_api_key(api_key, query)


async def test_search(api_key: str, query: str) -> ThirdPartySearchResult:
    return await _search_with_api_key(api_key, query)

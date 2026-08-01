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


DEEPSEEK_SERVICE_NAME = ThirdPartyServiceName.DEEPSEEK.value
DEEPSEEK_SEARCH_URL = "https://api.deepseek.com/anthropic/v1/messages"
DEEPSEEK_SEARCH_MODEL = "deepseek-v4-flash"
DEEPSEEK_WEB_SEARCH_TOOL_TYPE = "web_search_20250305"
DEEPSEEK_WEB_SEARCH_TOOL_NAME = "web_search"

DEEPSEEK_CONTENT_PATH = parse("$.content")
DEEPSEEK_TEXT_PATH = parse("$.content[?(@.type == 'text')].text")
DEEPSEEK_SOURCE_PATH = parse(
    "$.content[?(@.type == 'web_search_tool_result')].content[?(@.type == 'web_search_result')]"
)


def _build_search_payload(query: str) -> dict[str, Any]:
    return {
        "model": DEEPSEEK_SEARCH_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"Perform a web search for the query: {query}",
                    }
                ],
            }
        ],
        "system": [
            {
                "type": "text",
                "text": "You are an assistant for performing a web search tool use",
            }
        ],
        "tools": [
            {
                "type": DEEPSEEK_WEB_SEARCH_TOOL_TYPE,
                "name": DEEPSEEK_WEB_SEARCH_TOOL_NAME,
                "max_uses": 8,
            }
        ],
        "tool_choice": {
            "type": "tool",
            "name": DEEPSEEK_WEB_SEARCH_TOOL_NAME,
        },
        "max_tokens": 32000,
        "output_config": {
            "effort": "high",
        },
        "stream": False,
    }


def _extract_response(payload: dict[str, Any]) -> tuple[str, list[dict[str, Any]]] | None:
    contents = DEEPSEEK_CONTENT_PATH.find(payload)

    if len(contents) == 0 or not isinstance(contents[0].value, list):
        return None

    text = "\n".join(
        match.value
        for match in DEEPSEEK_TEXT_PATH.find(payload)
        if isinstance(match.value, str)
    )

    sources: list[dict[str, Any]] = []
    for match in DEEPSEEK_SOURCE_PATH.find(payload):
        item = match.value

        if not isinstance(item, dict):
            continue

        url = item.get("url")

        if not isinstance(url, str) or not url:
            continue

        source: dict[str, Any] = {"url": url}
        title = item.get("title")

        if isinstance(title, str) and title:
            source["title"] = title
        sources.append(source)

    return text, sources


async def _search_with_api_key(api_key: str, query: str) -> ThirdPartySearchResult:
    normalized_query = query.strip()

    if not normalized_query:
        return ThirdPartySearchResult.failure(DEEPSEEK_SERVICE_NAME, "搜索 query 不能为空")

    if not api_key.strip():
        return ThirdPartySearchResult.failure(DEEPSEEK_SERVICE_NAME, "DeepSeek API Key 未配置")

    payload = _build_search_payload(normalized_query)
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key.strip(),
        "anthropic-version": "2023-06-01",
    }
    timeout = aiohttp.ClientTimeout(total=90)
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    start_time = time.monotonic()

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                DEEPSEEK_SEARCH_URL,
                json=payload,
                headers=headers,
                ssl=ssl_context,
            ) as response:
                duration_ms = int((time.monotonic() - start_time) * 1000)
                await response.text()

                assert response.status < 400, f"DeepSeek 搜索请求失败: HTTP {response.status}"

                data = await response.json()

    except Exception as exc:
        duration_ms = int((time.monotonic() - start_time) * 1000)
        error_message = "DeepSeek 搜索请求处理失败: " + str(exc)

        return ThirdPartySearchResult.failure(
            DEEPSEEK_SERVICE_NAME, error_message, duration_ms,
        )

    extracted = _extract_response(data)

    if extracted is None:
        return ThirdPartySearchResult.failure(
            DEEPSEEK_SERVICE_NAME, "DeepSeek 搜索响应格式无效", duration_ms,
        )

    content, sources = extracted
    return ThirdPartySearchResult(
        success=True,
        service=DEEPSEEK_SERVICE_NAME,
        content=content,
        sources=sources,
        duration_ms=duration_ms,
    )


async def search(query: str) -> ThirdPartySearchResult:
    config = configUtil.get_app_config().setting.third_party_services.deepseek

    if not config.enabled:
        return ThirdPartySearchResult.failure(DEEPSEEK_SERVICE_NAME, "DeepSeek 搜索服务未启用")

    return await _search_with_api_key(config.api_key, query)


async def test_search(api_key: str, query: str) -> ThirdPartySearchResult:
    return await _search_with_api_key(api_key, query)

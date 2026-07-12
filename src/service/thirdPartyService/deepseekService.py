from __future__ import annotations

import time
from typing import Any
import ssl

import aiohttp
import certifi

from util import configUtil

DEEPSEEK_SERVICE_NAME = "deepseek"
DEEPSEEK_SEARCH_URL = "https://api.deepseek.com/anthropic/v1/messages"
DEEPSEEK_SEARCH_MODEL = "deepseek-v4-flash"
DEEPSEEK_WEB_SEARCH_TOOL_TYPE = "web_search_20250305"
DEEPSEEK_WEB_SEARCH_TOOL_NAME = "web_search"
DEFAULT_SEARCH_QUERY = "小米 今天 新闻"


def _failure(
    *,
    query: str,
    message: str,
    error_type: str = "DeepSeekSearchError",
    duration_ms: int | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "success": False,
        "service": DEEPSEEK_SERVICE_NAME,
        "query": query,
        "message": message,
        "error_type": error_type,
    }
    if duration_ms is not None:
        result["duration_ms"] = duration_ms
    return result


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


def _extract_text_from_anthropic_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""

    texts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text" and isinstance(item.get("text"), str):
            texts.append(item["text"])
    return "\n".join(texts)


def _extract_response(payload: dict[str, Any]) -> tuple[str, str, list[Any] | None, dict[str, Any] | None]:
    if isinstance(payload.get("choices"), list) and payload["choices"]:
        choice = payload["choices"][0]
        message = choice.get("message", {}) if isinstance(choice, dict) else {}
        if not isinstance(message, dict):
            return "", "", None, payload.get("usage") if isinstance(payload.get("usage"), dict) else None
        content = _extract_text_from_anthropic_content(message.get("content"))
        thinking = str(message.get("thinking") or message.get("reasoning_content") or "")
        tool_use = message.get("tool_use") or message.get("tool_calls")
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else None
        return content, thinking, tool_use if isinstance(tool_use, list) else None, usage

    content = _extract_text_from_anthropic_content(payload.get("content"))
    thinking = str(payload.get("thinking") or "")
    tool_use = payload.get("tool_use")
    if tool_use is None and isinstance(payload.get("content"), list):
        tool_items = [
            item for item in payload["content"]
            if isinstance(item, dict) and item.get("type") in ("tool_use", "server_tool_use")
        ]
        tool_use = tool_items or None
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else None
    return content, thinking, tool_use if isinstance(tool_use, list) else None, usage


async def _search_with_api_key(api_key: str, query: str) -> dict[str, Any]:
    normalized_query = query.strip()
    if not normalized_query:
        return _failure(query=query, message="搜索 query 不能为空", error_type="ValidationError")
    if not api_key.strip():
        return _failure(query=normalized_query, message="DeepSeek API Key 未配置", error_type="ValidationError")

    payload = _build_search_payload(normalized_query)
    timeout = aiohttp.ClientTimeout(total=90)
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key.strip(),
        "anthropic-version": "2023-06-01",
    }

    start_time = time.monotonic()
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(DEEPSEEK_SEARCH_URL, json=payload, headers=headers, ssl=ssl_context) as response:
                duration_ms = int((time.monotonic() - start_time) * 1000)
                raw_text = await response.text()
                if response.status >= 400:
                    return _failure(
                        query=normalized_query,
                        message=f"DeepSeek 搜索请求失败: HTTP {response.status} {raw_text[:500]}",
                        error_type="HttpError",
                        duration_ms=duration_ms,
                    )
                try:
                    data = await response.json()
                except Exception:
                    return _failure(
                        query=normalized_query,
                        message=f"DeepSeek 搜索响应不是合法 JSON: {raw_text[:500]}",
                        error_type="JsonDecodeError",
                        duration_ms=duration_ms,
                    )
    except Exception as e:
        duration_ms = int((time.monotonic() - start_time) * 1000)
        return _failure(
            query=normalized_query,
            message=str(e),
            error_type=type(e).__name__,
            duration_ms=duration_ms,
        )

    content, thinking, tool_use, usage = _extract_response(data)
    return {
        "success": True,
        "service": DEEPSEEK_SERVICE_NAME,
        "query": normalized_query,
        "content": content,
        "thinking": thinking,
        "tool_use": tool_use or [],
        "usage": usage,
        "duration_ms": duration_ms,
    }


async def search(query: str) -> dict[str, Any]:
    config = configUtil.get_app_config().setting.third_party_services.deepseek
    normalized_query = query.strip()
    if not normalized_query:
        return _failure(query=query, message="搜索 query 不能为空", error_type="ValidationError")
    if not config.enabled:
        return _failure(query=normalized_query, message="DeepSeek 搜索服务未启用", error_type="ServiceDisabled")
    return await _search_with_api_key(config.api_key, normalized_query)


async def test_search(api_key: str, query: str = DEFAULT_SEARCH_QUERY) -> dict[str, Any]:
    return await _search_with_api_key(api_key, query)

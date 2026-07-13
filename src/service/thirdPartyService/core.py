from __future__ import annotations

from typing import Any

from . import deepseekService


async def search(service_name: str, query: str) -> dict[str, Any]:
    normalized_name = service_name.strip().lower()
    if normalized_name == deepseekService.DEEPSEEK_SERVICE_NAME:
        return await deepseekService.search(query)

    return {
        "success": False,
        "service": normalized_name or service_name,
        "query": query,
        "message": f"不支持的三方搜索服务: {service_name}",
        "error_type": "UnsupportedService",
    }

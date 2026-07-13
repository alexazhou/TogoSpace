from __future__ import annotations

from typing import Any

from util import configUtil

from . import ThirdPartyService
from . import deepseekService


async def search(service_name: ThirdPartyService, query: str) -> dict[str, Any]:
    if service_name == ThirdPartyService.DEEPSEEK:
        if not configUtil.get_app_config().setting.third_party_services.deepseek.enabled:
            return _disabled_result(service_name, query)
        return await deepseekService.search(query)

    return {
        "success": False,
        "service": service_name.value,
        "query": query,
        "message": f"不支持的三方搜索服务: {service_name}",
        "error_type": "UnsupportedService",
    }


def _disabled_result(service_name: ThirdPartyService, query: str) -> dict[str, Any]:
    return {
        "success": False,
        "service": service_name.value,
        "query": query,
        "message": f"{service_name} search service is not enabled. Please enable it in settings first.",
        "error_type": "ServiceDisabled",
    }

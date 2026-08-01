from __future__ import annotations

from typing import Any

from constants import ThirdPartyServiceName
from util import configUtil

from . import deepseekService, xiaomiMimoService
from .result import ThirdPartySearchResult


def get_default_search_service() -> ThirdPartyServiceName:
    """读取配置中的默认搜索服务。"""
    return configUtil.get_app_config().setting.third_party_services.default_service.search


async def search(service_name: ThirdPartyServiceName | str, query: str) -> ThirdPartySearchResult:
    normalized_service = ThirdPartyServiceName.value_of(service_name)

    if normalized_service is None:
        return ThirdPartySearchResult.failure(str(service_name), f"不支持的三方搜索服务: {service_name}")

    if normalized_service == ThirdPartyServiceName.DEEPSEEK:
        return await deepseekService.search(query)

    if normalized_service == ThirdPartyServiceName.XIAOMI_MIMO:
        return await xiaomiMimoService.search(query)

    return ThirdPartySearchResult.failure(
        normalized_service.value, f"不支持的三方搜索服务: {normalized_service.value}",
    )

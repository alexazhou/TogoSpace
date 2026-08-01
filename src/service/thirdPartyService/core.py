from __future__ import annotations

from constants import ThirdPartyServiceName
from util import configUtil

from . import deepseekService, xiaomiMimoService
from .result import ThirdPartySearchResult


def get_default_search_service() -> ThirdPartyServiceName:
    """读取配置中的默认搜索服务。"""
    return configUtil.get_app_config().setting.third_party_services.default_service.search


async def search(service_name: ThirdPartyServiceName, query: str) -> ThirdPartySearchResult:
    if service_name == ThirdPartyServiceName.DEEPSEEK:
        services = configUtil.get_app_config().setting.third_party_services

        if not services.deepseek.enabled:
            return ThirdPartySearchResult.failure(
                ThirdPartyServiceName.DEEPSEEK.value,
                "DeepSeek 搜索服务未启用，请在后台配置三方服务后重试",
            )

        return await deepseekService.search(query)

    if service_name == ThirdPartyServiceName.XIAOMI_MIMO:
        services = configUtil.get_app_config().setting.third_party_services

        if not services.xiaomi_mimo.enabled:
            return ThirdPartySearchResult.failure(
                ThirdPartyServiceName.XIAOMI_MIMO.value,
                "Xiaomi MiMo 搜索服务未启用，请在后台配置三方服务后重试",
            )

        return await xiaomiMimoService.search(query)

    return ThirdPartySearchResult.failure(
        str(service_name), f"不支持的三方搜索服务: {service_name}",
    )

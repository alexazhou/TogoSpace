import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from constants import ThirdPartyServiceName
from service import thirdPartyService
from service.thirdPartyService import deepseekService, xiaomiMimoService
from util import configUtil
from util.configTypes import (
    DeepSeekThirdPartyServiceConfig,
    DefaultServiceConfig,
    ThirdPartyServicesConfig,
    XiaomiMiMoThirdPartyServiceConfig,
)


def test_third_party_services_config_defaults() -> None:
    config = ThirdPartyServicesConfig()

    assert config.default_service.search == ThirdPartyServiceName.DEEPSEEK
    assert config.deepseek.enabled is False
    assert config.deepseek.api_key == ""
    assert config.xiaomi_mimo.enabled is False
    assert config.xiaomi_mimo.api_key == ""


def test_deepseek_search_payload_matches_server_search_shape() -> None:
    payload = deepseekService._build_search_payload("小米 今天 新闻")

    assert payload["model"] == "deepseek-v4-flash"
    assert payload["messages"][0]["content"][0]["text"] == "Perform a web search for the query: 小米 今天 新闻"
    assert payload["tools"] == [
        {
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": 8,
        }
    ]
    assert payload["tool_choice"] == {"type": "tool", "name": "web_search"}
    assert payload["stream"] is False


def test_mimo_search_payload_matches_server_search_shape() -> None:
    payload = xiaomiMimoService._build_search_payload("小米 今天 新闻")

    assert payload["model"] == "mimo-v2.5"
    assert payload["messages"] == [{"role": "user", "content": "小米 今天 新闻"}]
    assert payload["tools"] == [{
        "type": "web_search",
        "max_keyword": 3,
        "force_search": True,
        "limit": 5,
    }]
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["stream"] is False


def test_deepseek_extracts_text_and_sources_from_recorded_response_shape() -> None:
    extracted = deepseekService._extract_response({
        "content": [
            {"type": "thinking", "thinking": "ignored"},
            {
                "type": "web_search_tool_result",
                "content": [
                    {
                        "type": "web_search_result",
                        "title": "来源标题",
                        "url": "https://example.com/deepseek",
                        "encrypted_content": "ignored",
                        "page_age": "ignored",
                    }
                ],
            },
            {"type": "text", "text": "搜索结果摘要"},
        ],
    })

    assert extracted == (
        "搜索结果摘要",
        [{"url": "https://example.com/deepseek", "title": "来源标题"}],
    )


def test_mimo_extracts_content_and_annotations_as_sources() -> None:
    extracted = xiaomiMimoService._extract_response({
        "choices": [{
            "message": {
                "content": "搜索结果摘要",
                "annotations": [{
                    "type": "url_citation",
                    "url": "https://example.com/mimo",
                    "title": "来源标题",
                    "summary": "来源摘要",
                    "site_name": "示例站点",
                }],
            },
        }],
    })

    assert extracted == (
        "搜索结果摘要",
        [{
            "url": "https://example.com/mimo",
            "title": "来源标题",
            "summary": "来源摘要",
            "site_name": "示例站点",
        }],
    )


@pytest.mark.asyncio
async def test_third_party_search_dispatches_deepseek(monkeypatch) -> None:
    search_mock = AsyncMock(return_value={"success": True, "service": "deepseek"})
    monkeypatch.setattr(deepseekService, "search", search_mock)

    result = await thirdPartyService.search("deepseek", "小米 今天 新闻")

    assert result["success"] is True
    search_mock.assert_awaited_once_with("小米 今天 新闻")


@pytest.mark.asyncio
async def test_third_party_search_dispatches_mimo(monkeypatch) -> None:
    search_mock = AsyncMock(return_value={"success": True, "service": "xiaomi_mimo"})
    monkeypatch.setattr(xiaomiMimoService, "search", search_mock)

    result = await thirdPartyService.search(ThirdPartyServiceName.XIAOMI_MIMO, "小米 今天 新闻")

    assert result["success"] is True
    search_mock.assert_awaited_once_with("小米 今天 新闻")


@pytest.mark.asyncio
async def test_third_party_search_rejects_unknown_service() -> None:
    result = await thirdPartyService.search("unknown", "query")

    assert result.model_dump(mode="json") == {
        "success": False,
        "service": "unknown",
        "duration_ms": 0,
        "content": None,
        "sources": None,
        "error_message": "不支持的三方搜索服务: unknown",
    }


@pytest.mark.asyncio
async def test_deepseek_search_requires_enabled_service(monkeypatch) -> None:
    app_config = SimpleNamespace(
        setting=SimpleNamespace(
            third_party_services=ThirdPartyServicesConfig(
                deepseek=DeepSeekThirdPartyServiceConfig(enabled=False, api_key="sk-test"),
            ),
        ),
    )
    monkeypatch.setattr(configUtil, "get_app_config", lambda: app_config)

    result = await deepseekService.search("query")

    assert result.success is False
    assert result.error_message == "DeepSeek 搜索服务未启用"
    assert "query" not in result.model_dump(mode="json")


@pytest.mark.asyncio
async def test_mimo_search_requires_enabled_service(monkeypatch) -> None:
    app_config = SimpleNamespace(
        setting=SimpleNamespace(
            third_party_services=ThirdPartyServicesConfig(
                xiaomi_mimo=XiaomiMiMoThirdPartyServiceConfig(enabled=False, api_key="sk-test"),
            ),
        ),
    )
    monkeypatch.setattr(configUtil, "get_app_config", lambda: app_config)

    result = await xiaomiMimoService.search("query")

    assert result.success is False
    assert result.error_message == "Xiaomi MiMo 搜索服务未启用"
    assert "query" not in result.model_dump(mode="json")


def test_update_setting_persists_third_party_services(tmp_path) -> None:
    setting_path = tmp_path / "setting.json"
    setting_path.write_text(json.dumps({
        "version": "v2",
        "workspace_root": "/tmp/workspaces",
    }), encoding="utf-8")

    configUtil.load(str(tmp_path), force_reload=True)
    configUtil.update_setting(
        lambda setting: setattr(
            setting,
            "third_party_services",
            ThirdPartyServicesConfig(
                default_service=DefaultServiceConfig(search=ThirdPartyServiceName.XIAOMI_MIMO),
                deepseek=DeepSeekThirdPartyServiceConfig(enabled=True, api_key="sk-deepseek"),
                xiaomi_mimo=XiaomiMiMoThirdPartyServiceConfig(enabled=True, api_key="sk-mimo"),
            ),
        )
    )

    raw = json.loads(setting_path.read_text(encoding="utf-8"))
    assert raw["third_party_services"] == {
        "default_service": {"search": "xiaomi_mimo"},
        "deepseek": {"enabled": True, "api_key": "sk-deepseek"},
        "xiaomi_mimo": {"enabled": True, "api_key": "sk-mimo"},
    }


@pytest.mark.asyncio
async def test_deepseek_search_uses_certifi_and_returns_unified_result(monkeypatch) -> None:
    import certifi
    import ssl

    original_create_context = ssl.create_default_context
    mock_cafile = None

    def mock_create_default_context(*args, **kwargs):
        nonlocal mock_cafile
        mock_cafile = kwargs.get("cafile")
        return original_create_context(*args, **kwargs)

    monkeypatch.setattr(ssl, "create_default_context", mock_create_default_context)

    class MockResponse:
        status = 200

        async def text(self):
            return ""

        async def json(self):
            return {
                "content": [
                    {"type": "text", "text": "ok"},
                    {
                        "type": "web_search_tool_result",
                        "content": [{
                            "type": "web_search_result",
                            "url": "https://example.com",
                            "title": "Example",
                        }],
                    },
                ],
            }

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    class MockSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def post(self, *args, **kwargs):
            return MockResponse()

    monkeypatch.setattr("aiohttp.ClientSession", MockSession)

    result = await deepseekService.test_search("sk-test", "query")

    assert mock_cafile == certifi.where()
    assert result.model_dump(mode="json") == {
        "success": True,
        "service": "deepseek",
        "duration_ms": result.duration_ms,
        "content": "ok",
        "sources": [{"url": "https://example.com", "title": "Example"}],
        "error_message": None,
    }
    assert result.duration_ms >= 0


@pytest.mark.asyncio
async def test_mimo_search_uses_expected_url_header_and_unified_result(monkeypatch) -> None:
    captured: dict = {}

    class MockResponse:
        status = 200

        async def text(self):
            return ""

        async def json(self):
            return {
                "choices": [{
                    "message": {
                        "content": "ok",
                        "annotations": [{
                            "type": "url_citation",
                            "url": "https://example.com/mimo",
                            "title": "Example",
                        }],
                    },
                }],
            }

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    class MockSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def post(self, url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return MockResponse()

    monkeypatch.setattr("aiohttp.ClientSession", MockSession)

    result = await xiaomiMimoService.test_search("sk-mimo", "小米 今天 新闻")

    assert captured["url"] == xiaomiMimoService.MIMO_SEARCH_URL
    assert captured["headers"] == {
        "api-key": "sk-mimo",
        "Content-Type": "application/json",
    }
    assert captured["json"] == xiaomiMimoService._build_search_payload("小米 今天 新闻")
    assert result.success is True
    assert result.sources == [{"url": "https://example.com/mimo", "title": "Example"}]
    assert set(result.model_dump(mode="json")) == {
        "success", "service", "duration_ms", "content", "sources", "error_message",
    }

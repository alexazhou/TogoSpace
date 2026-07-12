import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from service import thirdPartyService
from service.thirdPartyService import deepseekService
from util import configUtil
from util.configTypes import DeepSeekThirdPartyServiceConfig, ThirdPartyServicesConfig


def test_third_party_services_config_defaults() -> None:
    config = ThirdPartyServicesConfig()

    assert config.deepseek.enabled is False
    assert config.deepseek.api_key == ""


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


def test_deepseek_extracts_recorded_search_response_shape() -> None:
    content, thinking, tool_use, usage = deepseekService._extract_response({
        "choices": [
            {
                "message": {
                    "content": "搜索结果摘要",
                    "thinking": "正在搜索",
                    "tool_use": [{"input": {"query": "小米 今天 新闻"}}],
                }
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    })

    assert content == "搜索结果摘要"
    assert thinking == "正在搜索"
    assert tool_use == [{"input": {"query": "小米 今天 新闻"}}]
    assert usage == {"prompt_tokens": 10, "completion_tokens": 5}


@pytest.mark.asyncio
async def test_third_party_search_dispatches_deepseek(monkeypatch) -> None:
    search_mock = AsyncMock(return_value={"success": True, "service": "deepseek"})
    monkeypatch.setattr(deepseekService, "search", search_mock)

    result = await thirdPartyService.search("deepseek", "小米 今天 新闻")

    assert result["success"] is True
    search_mock.assert_awaited_once_with("小米 今天 新闻")


@pytest.mark.asyncio
async def test_third_party_search_rejects_unknown_service() -> None:
    result = await thirdPartyService.search("unknown", "query")

    assert result["success"] is False
    assert result["error_type"] == "UnsupportedService"


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

    assert result["success"] is False
    assert result["error_type"] == "ServiceDisabled"


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
                deepseek=DeepSeekThirdPartyServiceConfig(enabled=True, api_key="sk-deepseek"),
            ),
        )
    )

    raw = json.loads(setting_path.read_text(encoding="utf-8"))
    assert raw["third_party_services"]["deepseek"] == {
        "enabled": True,
        "api_key": "sk-deepseek",
    }

@pytest.mark.asyncio
async def test_deepseek_search_uses_certifi_for_ssl(monkeypatch) -> None:
    import ssl
    import certifi
    
    original_create_context = ssl.create_default_context
    mock_cafile = None
    
    def mock_create_default_context(*args, **kwargs):
        nonlocal mock_cafile
        mock_cafile = kwargs.get("cafile")
        return original_create_context(*args, **kwargs)
        
    monkeypatch.setattr(ssl, "create_default_context", mock_create_default_context)
    
    class MockResponse:
        status = 200
        async def text(self): return ""
        async def json(self): return {"choices": [{"message": {"content": "ok", "thinking": ""}}]}
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        
    class MockSession:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def post(self, *args, **kwargs):
            return MockResponse()
            
    monkeypatch.setattr("aiohttp.ClientSession", MockSession)
    
    await deepseekService.test_search("sk-test", "query")
    
    assert mock_cafile == certifi.where(), "Should use certifi for SSL verification to prevent cert errors on macOS"

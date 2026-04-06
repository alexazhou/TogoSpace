from unittest.mock import AsyncMock

import pytest

from model.coreModel.gtCoreChatModel import GtCoreAgentDialogContext
from service import llmService
from util import configUtil, llmApiUtil
from util.configTypes import AppConfig, SettingConfig


@pytest.mark.asyncio
async def test_infer_passes_default_openclaw_headers(monkeypatch):
    captured: dict[str, object] = {}

    async def _fake_send_request_non_stream(request, url, api_key, custom_llm_provider=None, extra_headers=None):
        captured["request"] = request
        captured["url"] = url
        captured["api_key"] = api_key
        captured["custom_llm_provider"] = custom_llm_provider
        captured["extra_headers"] = extra_headers
        return object()

    monkeypatch.setattr(configUtil, "get_app_config", lambda: AppConfig(setting=SettingConfig(
        default_llm_server="svc",
        llm_services=[
            {
                "name": "svc",
                "enable": True,
                "base_url": "http://localhost/v1/chat/completions",
                "api_key": "key-123",
                "type": "openai-compatible",
            }
        ],
    )))
    monkeypatch.setattr(llmService.llmApiUtil, "send_request_non_stream", _fake_send_request_non_stream)

    ctx = GtCoreAgentDialogContext(
        system_prompt="system prompt",
        messages=[llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiLLMApiRole.USER, "hello")],
    )

    result = await llmService.infer(None, ctx)

    assert result.ok is True
    assert captured["url"] == "http://localhost/v1/chat/completions"
    assert captured["api_key"] == "key-123"
    assert captured["custom_llm_provider"] == "openai"
    assert captured["extra_headers"] == {"User-Agent": "openclaw"}


@pytest.mark.asyncio
async def test_infer_passes_configured_headers_without_default_merge(monkeypatch):
    fake_send_request_non_stream = AsyncMock(return_value=object())

    monkeypatch.setattr(configUtil, "get_app_config", lambda: AppConfig(setting=SettingConfig(
        default_llm_server="svc",
        llm_services=[
            {
                "name": "svc",
                "enable": True,
                "base_url": "http://localhost/v1/chat/completions",
                "api_key": "key-123",
                "type": "openai-compatible",
                "extra_headers": {
                    "X-Client-Name": "openclaw",
                },
            }
        ],
    )))
    monkeypatch.setattr(llmService.llmApiUtil, "send_request_non_stream", fake_send_request_non_stream)

    ctx = GtCoreAgentDialogContext(
        system_prompt="system prompt",
        messages=[llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiLLMApiRole.USER, "hello")],
    )

    result = await llmService.infer(None, ctx)

    assert result.ok is True
    fake_send_request_non_stream.assert_awaited_once()
    assert fake_send_request_non_stream.await_args.kwargs["extra_headers"] == {"X-Client-Name": "openclaw"}

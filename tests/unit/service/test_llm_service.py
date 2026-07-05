from unittest.mock import AsyncMock, call

import pytest

from constants import InferRequestStateType
from model.coreModel.gtCoreChatModel import GtCoreAgentDialogContext
from service import llmService
from util import configUtil, llmApiUtil
from util.configTypes import AppConfig, SettingConfig


def _build_response(content: str = "ok") -> llmApiUtil.OpenAIResponse:
    return llmApiUtil.OpenAIResponse.model_validate({
        "id": "resp_123",
        "object": "chat.completion",
        "created": 1710000000,
        "model": "demo-model",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        },
    })


@pytest.mark.asyncio
async def test_infer_passes_default_opencode_headers(monkeypatch):
    captured: dict[str, object] = {}

    async def _fake_send_request_non_stream(request, url, api_key, custom_llm_provider=None, extra_headers=None, request_id=""):
        captured["request"] = request
        captured["url"] = url
        captured["api_key"] = api_key
        captured["custom_llm_provider"] = custom_llm_provider
        captured["extra_headers"] = extra_headers
        captured["request_id"] = request_id
        return _build_response()

    monkeypatch.setattr(configUtil, "get_app_config", lambda: AppConfig(setting=SettingConfig(
        default_models={"primary": "configured-model@svc"},
        llm_providers=[
            {
                "name": "svc",
                "type": "openai",
                "urls": {"openai": "http://localhost/v1/chat/completions"},
                "api_key": "key-123",
                "models": [{"name": "mock-model", "protocol": "openai"}, {"name": "configured-model", "protocol": "openai"}]
            }
        ],
    )))
    monkeypatch.setattr(llmService.llmApiUtil, "send_request_non_stream", _fake_send_request_non_stream)

    ctx = GtCoreAgentDialogContext(
        system_prompt="system prompt",
        messages=[llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hello")],
        tool_choice="none",
    )

    result = await llmService.infer(None, ctx)

    assert result.ok is True
    assert captured["url"] == "http://localhost/v1/chat/completions"
    assert captured["api_key"] == "key-123"
    assert captured["custom_llm_provider"] == "openai"
    assert captured["extra_headers"] is None or captured["extra_headers"] == {}
    assert captured["request"].tool_choice == "none"
    assert captured["request"].prompt_cache is True
    assert isinstance(captured["request_id"], str)
    assert len(captured["request_id"]) == 32
    assert result.request_id == captured["request_id"]


@pytest.mark.asyncio
async def test_infer_passes_configured_headers_without_default_merge(monkeypatch):
    fake_send_request_non_stream = AsyncMock(return_value=_build_response())

    monkeypatch.setattr(configUtil, "get_app_config", lambda: AppConfig(setting=SettingConfig(
        default_models={"primary": "configured-model@svc"},
        llm_providers=[
            {
                "name": "svc",
                "type": "openai",
                "urls": {"openai": "http://localhost/v1/chat/completions"},
                "api_key": "key-123",
                "models": [
                    {"name": "mock-model", "protocol": "openai"},
                    {"name": "configured-model", "protocol": "openai", "extra_headers": {"X-Client-Name": "openclaw"}},
                ]
            }
        ],
    )))
    monkeypatch.setattr(llmService.llmApiUtil, "send_request_non_stream", fake_send_request_non_stream)

    ctx = GtCoreAgentDialogContext(
        system_prompt="system prompt",
        messages=[llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hello")],
    )

    result = await llmService.infer(None, ctx)

    assert result.ok is True
    fake_send_request_non_stream.assert_awaited_once()
    assert fake_send_request_non_stream.await_args.kwargs["extra_headers"] == {"X-Client-Name": "openclaw"}
    assert isinstance(fake_send_request_non_stream.await_args.kwargs["request_id"], str)
    assert len(fake_send_request_non_stream.await_args.kwargs["request_id"]) == 32
    assert result.request_id == fake_send_request_non_stream.await_args.kwargs["request_id"]


@pytest.mark.asyncio
async def test_infer_stream_passes_request_id(monkeypatch):
    fake_send_request_stream = AsyncMock(return_value=_build_response("stream-ok"))

    monkeypatch.setattr(configUtil, "get_app_config", lambda: AppConfig(setting=SettingConfig(
        default_models={"primary": "configured-model@svc"},
        llm_providers=[
            {
                "name": "svc",
                "type": "openai",
                "urls": {"openai": "http://localhost/v1/chat/completions"},
                "api_key": "key-123",
                "models": [{"name": "mock-model", "protocol": "openai"}, {"name": "configured-model", "protocol": "openai"}]
            }
        ],
    )))
    monkeypatch.setattr(llmService.llmApiUtil, "send_request_stream", fake_send_request_stream)

    ctx = GtCoreAgentDialogContext(
        system_prompt="system prompt",
        messages=[llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hello")],
        tool_choice="none",
    )

    result = await llmService.infer_stream(None, ctx)

    assert result.ok is True
    fake_send_request_stream.assert_awaited_once()
    assert fake_send_request_stream.await_args.kwargs["request"].tool_choice == "none"
    assert fake_send_request_stream.await_args.kwargs["request"].prompt_cache is True
    assert isinstance(fake_send_request_stream.await_args.kwargs["request_id"], str)
    assert len(fake_send_request_stream.await_args.kwargs["request_id"]) == 32
    assert result.request_id == fake_send_request_stream.await_args.kwargs["request_id"]


@pytest.mark.asyncio
async def test_infer_stream_strips_required_tool_choice_when_reasoning_effort_enabled(monkeypatch):
    fake_send_request_stream = AsyncMock(return_value=_build_response("stream-ok"))

    monkeypatch.setattr(configUtil, "get_app_config", lambda: AppConfig(setting=SettingConfig(
        default_models={"primary": "deepseek-v4-pro@svc"},
            llm_providers=[
                {
                    "name": "svc",
                    "type": "openai",
                    "urls": {"openai": "http://localhost/v1/chat/completions"},
                    "api_key": "key-123",
                    "models": [{
                        "name": "deepseek-v4-pro",
                        "protocol": "openai",
                        "provider_params": {"reasoning_effort": "high"},
                    }]
                }
            ],
    )))
    monkeypatch.setattr(llmService.llmApiUtil, "send_request_stream", fake_send_request_stream)

    ctx = GtCoreAgentDialogContext(
        system_prompt="system prompt",
        messages=[llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hello")],
        tool_choice="required",
    )

    result = await llmService.infer_stream(None, ctx)

    assert result.ok is True
    fake_send_request_stream.assert_awaited_once()
    request = fake_send_request_stream.await_args.kwargs["request"]
    assert request.tool_choice is None
    assert request.provider_params["reasoning_effort"] == "high"


@pytest.mark.asyncio
async def test_infer_uses_context_prompt_cache_policy_when_provided(monkeypatch):
    fake_send_request_non_stream = AsyncMock(return_value=_build_response())

    monkeypatch.setattr(configUtil, "get_app_config", lambda: AppConfig(setting=SettingConfig(
        default_models={"primary": "configured-model@svc"},
        llm_providers=[
            {
                "name": "svc",
                "type": "openai",
                "urls": {"openai": "http://localhost/v1/chat/completions"},
                "api_key": "key-123",
                "models": [{"name": "mock-model", "protocol": "openai"}, {"name": "configured-model", "protocol": "openai"}]
            }
        ],
    )))
    monkeypatch.setattr(llmService.llmApiUtil, "send_request_non_stream", fake_send_request_non_stream)

    ctx = GtCoreAgentDialogContext(
        system_prompt="system prompt",
        messages=[llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hello")],
        prompt_cache=False,
    )

    result = await llmService.infer(None, ctx)

    assert result.ok is True
    fake_send_request_non_stream.assert_awaited_once()
    request = fake_send_request_non_stream.await_args.kwargs["request"]
    assert request.prompt_cache is False


@pytest.mark.asyncio
async def test_infer_uses_config_model_when_agent_model_is_none(monkeypatch):
    """Agent model 为空时，推理使用配置中的 model。"""
    fake_send_request_non_stream = AsyncMock(return_value=_build_response())

    monkeypatch.setattr(configUtil, "get_app_config", lambda: AppConfig(setting=SettingConfig(
        default_models={"primary": "configured-model@svc"},
        llm_providers=[
            {
                "name": "svc",
                "type": "openai",
                "urls": {"openai": "http://localhost/v1/chat/completions"},
                "api_key": "key-123",
                "models": [{"name": "configured-model", "protocol": "openai"}, {"name": "mock-model", "protocol": "openai"}, {"name": "agent-specific-model", "protocol": "openai"}]
            }
        ],
    )))
    monkeypatch.setattr(llmService.llmApiUtil, "send_request_non_stream", fake_send_request_non_stream)

    ctx = GtCoreAgentDialogContext(
        system_prompt="system prompt",
        messages=[llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hello")],
    )

    result = await llmService.infer(None, ctx)  # model 参数为 None

    assert result.ok is True
    fake_send_request_non_stream.assert_awaited_once()
    request = fake_send_request_non_stream.await_args.kwargs["request"]
    assert request.model == "configured-model"


@pytest.mark.asyncio
async def test_infer_uses_agent_model_when_provided(monkeypatch):
    """Agent model 有值时，推理使用 Agent 的 model，不使用配置中的 model。"""
    fake_send_request_non_stream = AsyncMock(return_value=_build_response())

    monkeypatch.setattr(configUtil, "get_app_config", lambda: AppConfig(setting=SettingConfig(
        default_models={"primary": "configured-model@svc"},
        llm_providers=[
            {
                "name": "svc",
                "type": "openai",
                "urls": {"openai": "http://localhost/v1/chat/completions"},
                "api_key": "key-123",
                "models": [{"name": "configured-model", "protocol": "openai"}, {"name": "mock-model", "protocol": "openai"}, {"name": "agent-specific-model", "protocol": "openai"}]
            }
        ],
    )))
    monkeypatch.setattr(llmService.llmApiUtil, "send_request_non_stream", fake_send_request_non_stream)

    ctx = GtCoreAgentDialogContext(
        system_prompt="system prompt",
        messages=[llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hello")],
    )

    result = await llmService.infer("agent-specific-model@svc", ctx)  # model 参数有值

    assert result.ok is True
    fake_send_request_non_stream.assert_awaited_once()
    request = fake_send_request_non_stream.await_args.kwargs["request"]
    assert request.model == "agent-specific-model"


@pytest.mark.asyncio
async def test_infer_stream_uses_config_model_when_agent_model_is_none(monkeypatch):
    """Agent model 为空时，流式推理使用配置中的 model。"""
    fake_send_request_stream = AsyncMock(return_value=_build_response("stream-ok"))

    monkeypatch.setattr(configUtil, "get_app_config", lambda: AppConfig(setting=SettingConfig(
        default_models={"primary": "configured-model@svc"},
        llm_providers=[
            {
                "name": "svc",
                "type": "openai",
                "urls": {"openai": "http://localhost/v1/chat/completions"},
                "api_key": "key-123",
                "models": [{"name": "configured-model", "protocol": "openai"}, {"name": "mock-model", "protocol": "openai"}, {"name": "agent-specific-model", "protocol": "openai"}]
            }
        ],
    )))
    monkeypatch.setattr(llmService.llmApiUtil, "send_request_stream", fake_send_request_stream)

    ctx = GtCoreAgentDialogContext(
        system_prompt="system prompt",
        messages=[llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hello")],
    )

    result = await llmService.infer_stream(None, ctx)  # model 参数为 None

    assert result.ok is True
    fake_send_request_stream.assert_awaited_once()
    request = fake_send_request_stream.await_args.kwargs["request"]
    assert request.model == "configured-model"


@pytest.mark.asyncio
async def test_infer_passes_provider_params(monkeypatch):
    fake_send_request_non_stream = AsyncMock(return_value=_build_response())

    monkeypatch.setattr(configUtil, "get_app_config", lambda: AppConfig(setting=SettingConfig(
        default_models={"primary": "mock-model@svc"},
            llm_providers=[
                {
                    "name": "svc",
                    "type": "openai",
                    "urls": {"openai": "http://localhost/v1/chat/completions"},
                    "api_key": "key-123",
                    "models": [{
                        "name": "mock-model",
                        "protocol": "openai",
                        "provider_params": {
                            "reasoning_effort": "high",
                            "parallel_tool_calls": False,
                        },
                    }]
                }
            ],
    )))
    monkeypatch.setattr(llmService.llmApiUtil, "send_request_non_stream", fake_send_request_non_stream)

    ctx = GtCoreAgentDialogContext(
        system_prompt="system prompt",
        messages=[llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hello")],
    )

    result = await llmService.infer(None, ctx)

    assert result.ok is True
    fake_send_request_non_stream.assert_awaited_once()
    request = fake_send_request_non_stream.await_args.kwargs["request"]
    assert request.provider_params == {
        "reasoning_effort": "high",
        "parallel_tool_calls": False,
    }


@pytest.mark.asyncio
async def test_infer_retries_with_exponential_backoff_until_success(monkeypatch):
    attempts = {"count": 0}
    status_events: list[llmService.InferRequestStatusEvent] = []
    sleep_mock = AsyncMock()

    async def _fake_send_request_non_stream(request, url, api_key, custom_llm_provider=None, extra_headers=None, request_id=""):
        attempts["count"] += 1
        if attempts["count"] < 4:
            raise RuntimeError(f"temporary failure {attempts['count']}")
        return _build_response("retry-ok")

    monkeypatch.setattr(configUtil, "get_app_config", lambda: AppConfig(setting=SettingConfig(
        default_models={"primary": "configured-model@svc"},
        llm_providers=[
            {
                "name": "svc",
                "type": "openai",
                "urls": {"openai": "http://localhost/v1/chat/completions"},
                "api_key": "key-123",
                "models": [{"name": "mock-model", "protocol": "openai"}, {"name": "configured-model", "protocol": "openai"}]
            }
        ],
    )))
    monkeypatch.setattr(llmService.llmApiUtil, "send_request_non_stream", _fake_send_request_non_stream)
    monkeypatch.setattr(llmService.asyncio, "sleep", sleep_mock)

    ctx = GtCoreAgentDialogContext(
        system_prompt="system prompt",
        messages=[llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hello")],
    )

    async def _on_status_event(event: llmService.InferRequestStatusEvent) -> None:
        status_events.append(event)

    result = await llmService.infer(None, ctx, on_status_event=_on_status_event)

    assert result.ok is True
    assert result.response is not None
    assert result.response.choices[0].message.content == "retry-ok"
    assert attempts["count"] == 4
    assert sleep_mock.await_args_list == [call(2), call(4), call(8)]
    assert [(event.state, event.attempt, event.retry_delay_seconds) for event in status_events] == [
        (InferRequestStateType.RETRY_SCHEDULED, 1, 2),
        (InferRequestStateType.RETRYING, 2, None),
        (InferRequestStateType.RETRY_SCHEDULED, 2, 4),
        (InferRequestStateType.RETRYING, 3, None),
        (InferRequestStateType.RETRY_SCHEDULED, 3, 8),
        (InferRequestStateType.RETRYING, 4, None),
    ]


@pytest.mark.asyncio
async def test_infer_stream_retries_up_to_limit_then_returns_failure(monkeypatch):
    status_events: list[llmService.InferRequestStatusEvent] = []
    sleep_mock = AsyncMock()
    fake_send_request_stream = AsyncMock(side_effect=RuntimeError("stream temporary failure"))

    monkeypatch.setattr(configUtil, "get_app_config", lambda: AppConfig(setting=SettingConfig(
        default_models={"primary": "configured-model@svc"},
        llm_providers=[
            {
                "name": "svc",
                "type": "openai",
                "urls": {"openai": "http://localhost/v1/chat/completions"},
                "api_key": "key-123",
                "models": [{"name": "mock-model", "protocol": "openai"}, {"name": "configured-model", "protocol": "openai"}]
            }
        ],
    )))
    monkeypatch.setattr(llmService.llmApiUtil, "send_request_stream", fake_send_request_stream)
    monkeypatch.setattr(llmService.asyncio, "sleep", sleep_mock)

    ctx = GtCoreAgentDialogContext(
        system_prompt="system prompt",
        messages=[llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hello")],
    )

    async def _on_status_event(event: llmService.InferRequestStatusEvent) -> None:
        status_events.append(event)

    result = await llmService.infer_stream(None, ctx, on_status_event=_on_status_event)

    assert result.ok is False
    assert result.response is None
    assert isinstance(result.error, RuntimeError)
    assert str(result.error) == "stream temporary failure"
    assert fake_send_request_stream.await_count == 8
    assert sleep_mock.await_args_list == [
        call(2),
        call(4),
        call(8),
        call(16),
        call(32),
        call(32),
        call(32),
    ]
    assert status_events[0].state == InferRequestStateType.RETRY_SCHEDULED
    assert status_events[0].attempt == 1
    assert status_events[0].retry_delay_seconds == 2
    assert status_events[-1].state == InferRequestStateType.RETRYING
    assert status_events[-1].attempt == 8


# ─── classify_llm_error ──────────────────────────────────

from constants import LlmErrorCategory
from service.llmService.llmErrorClassifier import classify_llm_error, RETRYABLE_CATEGORIES
from litellm.exceptions import (
    AuthenticationError, ContentPolicyViolationError, ContextWindowExceededError,
    InternalServerError, RateLimitError, APIConnectionError, Timeout,
    BadRequestError, InvalidRequestError, PermissionDeniedError, ServiceUnavailableError,
)


def test_classify_unknown_error_is_retryable():
    assert classify_llm_error(RuntimeError("temporary failure")) == LlmErrorCategory.UNKNOWN
    assert LlmErrorCategory.UNKNOWN in RETRYABLE_CATEGORIES


def test_classify_context_window_keyword():
    assert classify_llm_error(RuntimeError("context_length_exceeded")) == LlmErrorCategory.CONTEXT_WINDOW
    assert classify_llm_error(RuntimeError("input is too long")) == LlmErrorCategory.CONTEXT_WINDOW
    assert classify_llm_error(RuntimeError("Input too long: 217074 input tokens, limit is 202752")) == LlmErrorCategory.CONTEXT_WINDOW
    for kw in ("maximum context length", "prompt is too long", "exceeds the context window",
               "too many tokens", "context window", "max_tokens", "token limit"):
        result = classify_llm_error(RuntimeError(kw))
        assert result == LlmErrorCategory.CONTEXT_WINDOW, f"keyword '{kw}' should be CONTEXT_WINDOW"


def test_classify_context_window_exceeded_type():
    error = ContextWindowExceededError("too long", "model", 400)
    assert classify_llm_error(error) == LlmErrorCategory.CONTEXT_WINDOW


def test_classify_auth_error():
    error = AuthenticationError("invalid key", "model", 401)
    assert classify_llm_error(error) == LlmErrorCategory.AUTH_ERROR
    assert LlmErrorCategory.AUTH_ERROR not in RETRYABLE_CATEGORIES


def test_classify_permission_denied():
    from unittest.mock import MagicMock
    error = PermissionDeniedError("forbidden", "provider", "model", response=MagicMock())
    assert classify_llm_error(error) == LlmErrorCategory.AUTH_ERROR


def test_classify_content_policy():
    error = ContentPolicyViolationError("policy", "model", 400)
    assert classify_llm_error(error) == LlmErrorCategory.CONTENT_POLICY
    assert LlmErrorCategory.CONTENT_POLICY not in RETRYABLE_CATEGORIES


def test_classify_rate_limited():
    error = RateLimitError("rate limit", "model", 429)
    assert classify_llm_error(error) == LlmErrorCategory.RATE_LIMITED
    assert LlmErrorCategory.RATE_LIMITED in RETRYABLE_CATEGORIES


def test_classify_server_error():
    assert classify_llm_error(InternalServerError("500", "model", 500)) == LlmErrorCategory.SERVER_ERROR
    assert classify_llm_error(ServiceUnavailableError("503", "model", 503)) == LlmErrorCategory.SERVER_ERROR
    assert LlmErrorCategory.SERVER_ERROR in RETRYABLE_CATEGORIES


def test_classify_network_error():
    assert classify_llm_error(APIConnectionError("conn", "model", 503)) == LlmErrorCategory.NETWORK_ERROR
    assert classify_llm_error(Timeout("timeout", "model", 408)) == LlmErrorCategory.NETWORK_ERROR
    assert LlmErrorCategory.NETWORK_ERROR in RETRYABLE_CATEGORIES


def test_classify_bad_request_with_context_window_keyword():
    error = BadRequestError("context_length_exceeded blah", "model", 400)
    assert classify_llm_error(error) == LlmErrorCategory.CONTEXT_WINDOW


def test_classify_bad_request_generic():
    error = BadRequestError("unsupported parameter", "model", 400)
    assert classify_llm_error(error) == LlmErrorCategory.INVALID_REQUEST
    assert LlmErrorCategory.INVALID_REQUEST not in RETRYABLE_CATEGORIES


def test_non_retryable_categories():
    non_retryable = {
        LlmErrorCategory.CONTEXT_WINDOW,
        LlmErrorCategory.AUTH_ERROR,
        LlmErrorCategory.INVALID_REQUEST,
        LlmErrorCategory.CONTENT_POLICY,
    }
    assert non_retryable.isdisjoint(RETRYABLE_CATEGORIES)


# ─── infer 不重试不可恢复的错误 ──────────────────────────────


@pytest.mark.asyncio
async def test_infer_no_retry_on_non_retryable_error(monkeypatch):
    """infer 遇到不可重试的错误（如上下文超长），只尝试 1 次，不重试。"""
    attempts = {"count": 0}

    async def _fake_send_request_non_stream(request, url, api_key, custom_llm_provider=None, extra_headers=None, request_id=""):
        attempts["count"] += 1
        raise RuntimeError("Input too long: 217074 input tokens, limit is 202752 for this model")

    monkeypatch.setattr(configUtil, "get_app_config", lambda: AppConfig(setting=SettingConfig(
        default_models={"primary": "configured-model@svc"},
        llm_providers=[
            {
                "name": "svc",
                "type": "openai",
                "urls": {"openai": "http://localhost/v1/chat/completions"},
                "api_key": "key-123",
                "models": [{"name": "mock-model", "protocol": "openai"}, {"name": "configured-model", "protocol": "openai"}]
            }
        ],
    )))
    monkeypatch.setattr(llmService.llmApiUtil, "send_request_non_stream", _fake_send_request_non_stream)

    ctx = GtCoreAgentDialogContext(
        system_prompt="system prompt",
        messages=[llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hello")],
    )

    result = await llmService.infer(None, ctx)

    assert result.ok is False
    assert attempts["count"] == 1, f"非可重试错误不应重试，但实际尝试了 {attempts['count']} 次"

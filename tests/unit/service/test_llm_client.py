from util.llmApiUtil import OpenAIMessage, OpenAIRequest, OpenAIUsage, OpenaiApiRole
from util.llmApiUtil import client as llm_client


def test_cache_injection_points_cover_system_and_last_message():
    assert llm_client._CACHE_INJECTION_POINTS == [
        {"location": "message", "role": "system"},
        {"location": "message", "index": -1},
    ]


# ===== _is_thinking_mode_model =====


def test_is_thinking_mode_model_deepseek_r1():
    assert llm_client._is_thinking_mode_model("deepseek-r1") is True


def test_is_thinking_mode_model_deepseek_v4_pro():
    assert llm_client._is_thinking_mode_model("deepseek-v4-pro") is True


def test_is_thinking_mode_model_deepseek_pro():
    assert llm_client._is_thinking_mode_model("deepseek-pro") is True


def test_is_thinking_mode_model_non_thinking():
    assert llm_client._is_thinking_mode_model("gpt-4o") is False
    assert llm_client._is_thinking_mode_model("claude-3-opus") is False
    assert llm_client._is_thinking_mode_model("deepseek-chat") is False


def test_is_thinking_mode_model_case_insensitive():
    assert llm_client._is_thinking_mode_model("DeepSeek-V4-Pro") is True
    assert llm_client._is_thinking_mode_model("DEEPSEEK-R1") is True


# ===== _build_request_payload reasoning_content padding =====


def test_build_request_payload_pads_reasoning_content_for_thinking_mode_model():
    """thinking mode 模型应补填所有 assistant 消息的 reasoning_content。"""
    request = OpenAIRequest(
        model="deepseek-v4-pro",
        messages=[
            OpenAIMessage.text(OpenaiApiRole.USER, "hello"),
            OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "hi"),
        ],
    )
    _, messages, _ = llm_client._build_request_payload(request)
    assistant_msgs = [m for m in messages if m["role"] == "assistant"]
    assert len(assistant_msgs) == 1
    assert assistant_msgs[0]["reasoning_content"] == ""


def test_build_request_payload_pads_reasoning_content_when_existing_has_it():
    """非 thinking mode 模型但对话中已有 reasoning_content 时也应补填。"""
    request = OpenAIRequest(
        model="gpt-4o",
        messages=[
            OpenAIMessage.text(OpenaiApiRole.USER, "hello"),
            OpenAIMessage(
                role=OpenaiApiRole.ASSISTANT,
                content="thinking...",
                reasoning_content="I need to analyze this",
            ),
            OpenAIMessage.text(OpenaiApiRole.USER, "continue"),
            OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "here is my answer"),
        ],
    )
    _, messages, _ = llm_client._build_request_payload(request)
    assistant_msgs = [m for m in messages if m["role"] == "assistant"]
    assert len(assistant_msgs) == 2
    assert assistant_msgs[0]["reasoning_content"] == "I need to analyze this"
    assert assistant_msgs[1]["reasoning_content"] == ""


def test_build_request_payload_no_padding_for_non_thinking_model():
    """非 thinking mode 模型且对话中没有 reasoning_content 时不补填。"""
    request = OpenAIRequest(
        model="gpt-4o",
        messages=[
            OpenAIMessage.text(OpenaiApiRole.USER, "hello"),
            OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "hi"),
        ],
    )
    _, messages, _ = llm_client._build_request_payload(request)
    assistant_msgs = [m for m in messages if m["role"] == "assistant"]
    assert len(assistant_msgs) == 1
    assert "reasoning_content" not in assistant_msgs[0]


def test_build_request_payload_does_not_overwrite_existing_reasoning_content():
    """已有 reasoning_content 的消息不应被覆盖。"""
    request = OpenAIRequest(
        model="deepseek-v4-pro",
        messages=[
            OpenAIMessage.text(OpenaiApiRole.USER, "hello"),
            OpenAIMessage(
                role=OpenaiApiRole.ASSISTANT,
                content="answer",
                reasoning_content="my reasoning",
            ),
        ],
    )
    _, messages, _ = llm_client._build_request_payload(request)
    assistant_msgs = [m for m in messages if m["role"] == "assistant"]
    assert assistant_msgs[0]["reasoning_content"] == "my reasoning"


def test_build_request_payload_mixed_assistant_messages():
    """混合场景：部分 assistant 有 reasoning_content，部分没有。"""
    request = OpenAIRequest(
        model="deepseek-r1",
        messages=[
            OpenAIMessage.text(OpenaiApiRole.USER, "hello"),
            OpenAIMessage(
                role=OpenaiApiRole.ASSISTANT,
                content="first answer",
                reasoning_content="first reasoning",
            ),
            OpenAIMessage.text(OpenaiApiRole.USER, "continue"),
            OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "second answer"),
        ],
    )
    _, messages, _ = llm_client._build_request_payload(request)
    assistant_msgs = [m for m in messages if m["role"] == "assistant"]
    assert len(assistant_msgs) == 2
    assert assistant_msgs[0]["reasoning_content"] == "first reasoning"
    assert assistant_msgs[1]["reasoning_content"] == ""


def test_build_request_payload_does_not_pad_non_assistant_messages():
    """只补填 assistant 消息，不影响 user/tool 消息。"""
    request = OpenAIRequest(
        model="deepseek-v4-pro",
        messages=[
            OpenAIMessage.text(OpenaiApiRole.USER, "hello"),
            OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "hi"),
            OpenAIMessage.tool_result("call_1", '{"result": "ok"}'),
        ],
    )
    _, messages, _ = llm_client._build_request_payload(request)
    tool_msgs = [m for m in messages if m["role"] == "tool"]
    assert len(tool_msgs) == 1
    assert "reasoning_content" not in tool_msgs[0]


def test_openai_usage_normalizes_legacy_cache_fields_into_prompt_cache_usage():
    usage = OpenAIUsage.model_validate({
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
        "prompt_tokens_details": {
            "cached_tokens": 75,
            "cache_creation_tokens": 30,
        },
        "cache_creation_input_tokens": 30,
        "cache_read_input_tokens": 75,
    })

    assert usage.prompt_cache_usage is not None
    assert usage.prompt_cache_usage.cached_tokens == 75
    assert usage.prompt_cache_usage.cache_write_tokens == 30


def test_openai_usage_keeps_none_distinct_from_zero_for_cached_tokens():
    usage = OpenAIUsage.model_validate({
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
        "cache_creation_input_tokens": 30,
        "cache_read_input_tokens": 0,
    })

    assert usage.prompt_cache_usage is not None
    assert usage.prompt_cache_usage.cached_tokens == 0
    assert usage.prompt_cache_usage.cache_write_tokens == 30


def test_openai_usage_normalizes_anthropic_cache_read_tokens_into_cached_tokens():
    usage = OpenAIUsage.model_validate({
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
        "cache_creation_input_tokens": 30,
        "cache_read_input_tokens": 55,
    })

    assert usage.prompt_cache_usage is not None
    assert usage.prompt_cache_usage.cached_tokens == 55
    assert usage.prompt_cache_usage.cache_write_tokens == 30

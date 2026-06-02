import pytest
from service.llmService.llmRequestRules import apply_llm_request_rules
from util import llmApiUtil


THINKING_PARAMS = {"thinking": {"type": "enabled"}}


def _make_assistant_tool_call_msg(reasoning_content=None):
    return llmApiUtil.OpenAIMessage(
        role=llmApiUtil.OpenaiApiRole.ASSISTANT,
        content=None,
        reasoning_content=reasoning_content,
        tool_calls=[
            llmApiUtil.OpenAIToolCall(
                id="call_1",
                type="function",
                function={"name": "get_time", "arguments": "{}"},
            )
        ],
    )


def test_apply_llm_request_rules_strips_required_tool_choice_for_reasoning():
    request = llmApiUtil.OpenAIRequest(
        model="deepseek-v4-pro",
        messages=[llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hello")],
        tool_choice="required",
        provider_params={"reasoning_effort": "high"},
    )

    next_request, applied_rules = apply_llm_request_rules(request)

    assert next_request.tool_choice is None
    assert applied_rules == ("StripRequiredToolChoiceForReasoningRule",)


def test_apply_llm_request_rules_keeps_non_required_tool_choice():
    request = llmApiUtil.OpenAIRequest(
        model="deepseek-v4-pro",
        messages=[llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hello")],
        tool_choice="none",
        provider_params={"reasoning_effort": "high"},
    )

    next_request, applied_rules = apply_llm_request_rules(request)

    assert next_request.tool_choice == "none"
    assert applied_rules == ()


# ===== FillMissingReasoningContentRule =====


def test_fill_missing_reasoning_content_fills_empty_string_when_thinking_enabled():
    """切换模型场景：历史中有非思考模型生成的 assistant tool_call（无 reasoning_content），
    开启思考模式后应自动补填 reasoning_content=""。"""
    msg_no_rc = _make_assistant_tool_call_msg(reasoning_content=None)
    request = llmApiUtil.OpenAIRequest(
        model="deepseek-v4-pro",
        messages=[
            llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hello"),
            msg_no_rc,
        ],
        provider_params=THINKING_PARAMS,
    )

    next_request, applied_rules = apply_llm_request_rules(request)

    assert "FillMissingReasoningContentRule" in applied_rules
    assistant_msgs = [m for m in next_request.messages if m.role == llmApiUtil.OpenaiApiRole.ASSISTANT]
    assert len(assistant_msgs) == 1
    assert assistant_msgs[0].reasoning_content == ""


def test_fill_missing_reasoning_content_preserves_existing_reasoning_content():
    """已有 reasoning_content 的消息不应被修改。"""
    msg_with_rc = _make_assistant_tool_call_msg(reasoning_content="I need to think...")
    request = llmApiUtil.OpenAIRequest(
        model="deepseek-v4-pro",
        messages=[
            llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hello"),
            msg_with_rc,
        ],
        provider_params=THINKING_PARAMS,
    )

    next_request, applied_rules = apply_llm_request_rules(request)

    assert "FillMissingReasoningContentRule" not in applied_rules
    assistant_msgs = [m for m in next_request.messages if m.role == llmApiUtil.OpenaiApiRole.ASSISTANT]
    assert assistant_msgs[0].reasoning_content == "I need to think..."


def test_fill_missing_reasoning_content_not_triggered_without_thinking():
    """未开启思考模式时规则不触发。"""
    msg_no_rc = _make_assistant_tool_call_msg(reasoning_content=None)
    request = llmApiUtil.OpenAIRequest(
        model="gpt-4o",
        messages=[
            llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hello"),
            msg_no_rc,
        ],
        provider_params={},
    )

    next_request, applied_rules = apply_llm_request_rules(request)

    assert "FillMissingReasoningContentRule" not in applied_rules
    assistant_msgs = [m for m in next_request.messages if m.role == llmApiUtil.OpenaiApiRole.ASSISTANT]
    assert assistant_msgs[0].reasoning_content is None


def test_fill_missing_reasoning_content_mixed_messages():
    """混合场景：有的消息有 reasoning_content，有的没有，只补填缺失的。"""
    msg_with_rc = _make_assistant_tool_call_msg(reasoning_content="thinking...")
    msg_no_rc = _make_assistant_tool_call_msg(reasoning_content=None)
    request = llmApiUtil.OpenAIRequest(
        model="deepseek-v4-pro",
        messages=[
            llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "first"),
            msg_with_rc,
            llmApiUtil.OpenAIMessage.tool_result("call_1", '{"result": "ok"}'),
            llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "second"),
            msg_no_rc,
        ],
        provider_params=THINKING_PARAMS,
    )

    next_request, applied_rules = apply_llm_request_rules(request)

    assert "FillMissingReasoningContentRule" in applied_rules
    assistant_msgs = [m for m in next_request.messages if m.role == llmApiUtil.OpenaiApiRole.ASSISTANT]
    assert assistant_msgs[0].reasoning_content == "thinking..."
    assert assistant_msgs[1].reasoning_content == ""


def test_fill_missing_reasoning_content_thinking_disabled():
    """thinking.type == "disabled" 时规则不触发。"""
    msg_no_rc = _make_assistant_tool_call_msg(reasoning_content=None)
    request = llmApiUtil.OpenAIRequest(
        model="deepseek-v4-pro",
        messages=[
            llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hello"),
            msg_no_rc,
        ],
        provider_params={"thinking": {"type": "disabled"}},
    )

    next_request, applied_rules = apply_llm_request_rules(request)

    assert "FillMissingReasoningContentRule" not in applied_rules


# ===== StripRequiredToolChoiceForReasoningRule 边界场景 =====


def test_strip_tool_choice_not_triggered_when_reasoning_effort_empty_string():
    """reasoning_effort 为空字符串时不触发 StripRequiredToolChoiceForReasoningRule。"""
    request = llmApiUtil.OpenAIRequest(
        model="deepseek-v4-pro",
        messages=[llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hello")],
        tool_choice="required",
        provider_params={"reasoning_effort": ""},
    )

    next_request, applied_rules = apply_llm_request_rules(request)

    assert next_request.tool_choice == "required"
    assert applied_rules == ()


def test_strip_tool_choice_not_triggered_when_tool_choice_is_none():
    """tool_choice 为 None 时不触发 StripRequiredToolChoiceForReasoningRule。"""
    request = llmApiUtil.OpenAIRequest(
        model="deepseek-v4-pro",
        messages=[llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hello")],
        tool_choice=None,
        provider_params={"reasoning_effort": "high"},
    )

    next_request, applied_rules = apply_llm_request_rules(request)

    assert next_request.tool_choice is None
    assert applied_rules == ()


def test_strip_tool_choice_not_triggered_when_provider_params_empty():
    """provider_params 为空字典时不触发 StripRequiredToolChoiceForReasoningRule。"""
    request = llmApiUtil.OpenAIRequest(
        model="deepseek-v4-pro",
        messages=[llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hello")],
        tool_choice="required",
        provider_params={},
    )

    next_request, applied_rules = apply_llm_request_rules(request)

    assert next_request.tool_choice == "required"
    assert applied_rules == ()


# ===== FillMissingReasoningContentRule 边界场景 =====


def test_fill_missing_reasoning_content_not_triggered_for_plain_assistant_message():
    """纯文本 assistant 消息（无 tool_calls）的 reasoning_content=None 不应被补填。"""
    plain_assistant_msg = llmApiUtil.OpenAIMessage(
        role=llmApiUtil.OpenaiApiRole.ASSISTANT,
        content="I am a plain response",
        reasoning_content=None,
        tool_calls=None,
    )
    request = llmApiUtil.OpenAIRequest(
        model="deepseek-v4-pro",
        messages=[
            llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hello"),
            plain_assistant_msg,
        ],
        provider_params=THINKING_PARAMS,
    )

    next_request, applied_rules = apply_llm_request_rules(request)

    assert "FillMissingReasoningContentRule" not in applied_rules
    assistant_msgs = [m for m in next_request.messages if m.role == llmApiUtil.OpenaiApiRole.ASSISTANT]
    assert assistant_msgs[0].reasoning_content is None


def test_fill_missing_reasoning_content_not_triggered_when_provider_params_empty():
    """provider_params 为空字典时不触发 FillMissingReasoningContentRule。"""
    msg_no_rc = _make_assistant_tool_call_msg(reasoning_content=None)
    request = llmApiUtil.OpenAIRequest(
        model="deepseek-v4-pro",
        messages=[
            llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hello"),
            msg_no_rc,
        ],
        provider_params={},
    )

    next_request, applied_rules = apply_llm_request_rules(request)

    assert "FillMissingReasoningContentRule" not in applied_rules
    assistant_msgs = [m for m in next_request.messages if m.role == llmApiUtil.OpenaiApiRole.ASSISTANT]
    assert assistant_msgs[0].reasoning_content is None


def test_fill_missing_reasoning_content_not_triggered_when_thinking_is_string():
    """thinking 为非 dict 值（如字符串）时不触发规则。"""
    msg_no_rc = _make_assistant_tool_call_msg(reasoning_content=None)
    request = llmApiUtil.OpenAIRequest(
        model="deepseek-v4-pro",
        messages=[
            llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hello"),
            msg_no_rc,
        ],
        provider_params={"thinking": "enabled"},
    )

    next_request, applied_rules = apply_llm_request_rules(request)

    assert "FillMissingReasoningContentRule" not in applied_rules


# ===== 两条规则同时触发的组合场景 =====


def test_both_rules_triggered_simultaneously():
    """reasoning_effort + tool_choice="required" + thinking enabled + 缺失 reasoning_content，
    两条规则应同时触发。"""
    msg_no_rc = _make_assistant_tool_call_msg(reasoning_content=None)
    request = llmApiUtil.OpenAIRequest(
        model="deepseek-v4-pro",
        messages=[
            llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hello"),
            msg_no_rc,
        ],
        tool_choice="required",
        provider_params={
            "reasoning_effort": "high",
            "thinking": {"type": "enabled"},
        },
    )

    next_request, applied_rules = apply_llm_request_rules(request)

    assert next_request.tool_choice is None
    assert "StripRequiredToolChoiceForReasoningRule" in applied_rules
    assert "FillMissingReasoningContentRule" in applied_rules
    assistant_msgs = [m for m in next_request.messages if m.role == llmApiUtil.OpenaiApiRole.ASSISTANT]
    assert assistant_msgs[0].reasoning_content == ""


def test_no_rules_triggered_when_no_conditions_match():
    """无任何规则匹配时，请求原样返回。"""
    request = llmApiUtil.OpenAIRequest(
        model="gpt-4o",
        messages=[llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hello")],
        tool_choice="auto",
        provider_params={},
    )

    next_request, applied_rules = apply_llm_request_rules(request)

    assert next_request.tool_choice == "auto"
    assert next_request.messages == request.messages
    assert applied_rules == ()

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

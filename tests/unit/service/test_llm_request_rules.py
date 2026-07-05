import pytest
from service.llmService.llmRequestRules import (
    _AUTO_ENABLE_THINKING_MODELS,
    _is_thinking_enabled,
    _model_in_list,
    apply_llm_request_rules,
)
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


# ===== _model_in_list =====


def test_model_in_list_deepseek_r1():
    assert _model_in_list("deepseek-r1", _AUTO_ENABLE_THINKING_MODELS) is True


def test_model_in_list_deepseek_v4_pro():
    assert _model_in_list("deepseek-v4-pro", _AUTO_ENABLE_THINKING_MODELS) is True


def test_model_in_list_deepseek_pro():
    assert _model_in_list("deepseek-pro", _AUTO_ENABLE_THINKING_MODELS) is True


def test_model_in_list_deepseek_reasoner():
    assert _model_in_list("deepseek-reasoner", _AUTO_ENABLE_THINKING_MODELS) is True


def test_model_in_list_non_thinking():
    assert _model_in_list("gpt-4o", _AUTO_ENABLE_THINKING_MODELS) is False
    assert _model_in_list("claude-3-opus", _AUTO_ENABLE_THINKING_MODELS) is False
    assert _model_in_list("deepseek-chat", _AUTO_ENABLE_THINKING_MODELS) is False


def test_model_in_list_case_insensitive():
    assert _model_in_list("DeepSeek-V4-Pro", _AUTO_ENABLE_THINKING_MODELS) is True
    assert _model_in_list("DEEPSEEK-R1", _AUTO_ENABLE_THINKING_MODELS) is True


# ===== _is_thinking_enabled =====


def test_is_thinking_enabled_by_thinking_type_enabled():
    request = llmApiUtil.OpenAIRequest(
        model="gpt-4o",
        messages=[llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hi")],
        extra_params={"thinking": {"type": "enabled"}},
    )
    assert _is_thinking_enabled(request, _AUTO_ENABLE_THINKING_MODELS) is True


def test_is_thinking_enabled_by_thinking_type_disabled():
    request = llmApiUtil.OpenAIRequest(
        model="deepseek-v4-pro",
        messages=[llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hi")],
        extra_params={"thinking": {"type": "disabled"}},
    )
    assert _is_thinking_enabled(request, _AUTO_ENABLE_THINKING_MODELS) is False


def test_is_thinking_enabled_by_reasoning_effort():
    request = llmApiUtil.OpenAIRequest(
        model="gpt-4o",
        messages=[llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hi")],
        extra_params={"reasoning_effort": "high"},
    )
    assert _is_thinking_enabled(request, _AUTO_ENABLE_THINKING_MODELS) is True


def test_is_thinking_enabled_by_model_name():
    request = llmApiUtil.OpenAIRequest(
        model="deepseek-v4-pro",
        messages=[llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hi")],
        extra_params={},
    )
    assert _is_thinking_enabled(request, _AUTO_ENABLE_THINKING_MODELS) is True


def test_is_thinking_enabled_disabled_overrides_model_name():
    """thinking.type == "disabled" 应覆盖模型名称隐式启用。"""
    request = llmApiUtil.OpenAIRequest(
        model="deepseek-v4-pro",
        messages=[llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hi")],
        extra_params={"thinking": {"type": "disabled"}},
    )
    assert _is_thinking_enabled(request, _AUTO_ENABLE_THINKING_MODELS) is False


def test_is_thinking_enabled_thinking_type_overrides_reasoning_effort():
    """thinking.type == "enabled" 优先级高于 reasoning_effort。"""
    request = llmApiUtil.OpenAIRequest(
        model="gpt-4o",
        messages=[llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hi")],
        extra_params={"thinking": {"type": "enabled"}, "reasoning_effort": "high"},
    )
    assert _is_thinking_enabled(request, _AUTO_ENABLE_THINKING_MODELS) is True


def test_is_thinking_enabled_no_params_no_thinking_model():
    request = llmApiUtil.OpenAIRequest(
        model="gpt-4o",
        messages=[llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hi")],
        extra_params={},
    )
    assert _is_thinking_enabled(request, _AUTO_ENABLE_THINKING_MODELS) is False


def test_is_thinking_enabled_with_empty_extra_params():
    request = llmApiUtil.OpenAIRequest(
        model="gpt-4o",
        messages=[llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hi")],
        extra_params={},
    )
    assert _is_thinking_enabled(request, _AUTO_ENABLE_THINKING_MODELS) is False


# ===== StripRequiredToolChoiceForReasoningRule =====


def test_apply_llm_request_rules_strips_required_tool_choice_for_reasoning():
    request = llmApiUtil.OpenAIRequest(
        model="deepseek-v4-pro",
        messages=[llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hello")],
        tool_choice="required",
        extra_params={"reasoning_effort": "high"},
    )

    next_request, applied_rules = apply_llm_request_rules(request)

    assert next_request.tool_choice is None
    assert applied_rules == ("StripRequiredToolChoiceForReasoningRule",)


def test_apply_llm_request_rules_strips_required_tool_choice_by_model_name():
    """thinking mode 模型名称隐式启用时，也应剥离 tool_choice="required"。"""
    request = llmApiUtil.OpenAIRequest(
        model="deepseek-r1",
        messages=[llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hello")],
        tool_choice="required",
        extra_params={},
    )

    next_request, applied_rules = apply_llm_request_rules(request)

    assert next_request.tool_choice is None
    assert applied_rules == ("StripRequiredToolChoiceForReasoningRule",)


def test_apply_llm_request_rules_strips_required_tool_choice_by_thinking_enabled():
    """thinking.type == "enabled" 时，也应剥离 tool_choice="required"。"""
    request = llmApiUtil.OpenAIRequest(
        model="gpt-4o",
        messages=[llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hello")],
        tool_choice="required",
        extra_params={"thinking": {"type": "enabled"}},
    )

    next_request, applied_rules = apply_llm_request_rules(request)

    assert next_request.tool_choice is None
    assert applied_rules == ("StripRequiredToolChoiceForReasoningRule",)


def test_apply_llm_request_rules_keeps_required_tool_choice_when_thinking_disabled():
    """thinking.type == "disabled" 时，即使模型是 thinking mode，也不剥离 tool_choice。"""
    request = llmApiUtil.OpenAIRequest(
        model="deepseek-v4-pro",
        messages=[llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hello")],
        tool_choice="required",
        extra_params={"thinking": {"type": "disabled"}},
    )

    next_request, applied_rules = apply_llm_request_rules(request)

    assert next_request.tool_choice == "required"
    assert applied_rules == ()


def test_apply_llm_request_rules_keeps_non_required_tool_choice():
    request = llmApiUtil.OpenAIRequest(
        model="deepseek-v4-pro",
        messages=[llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hello")],
        tool_choice="none",
        extra_params={"reasoning_effort": "high"},
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
        extra_params=THINKING_PARAMS,
    )

    next_request, applied_rules = apply_llm_request_rules(request)

    assert "FillMissingReasoningContentRule" in applied_rules
    assistant_msgs = [m for m in next_request.messages if m.role == llmApiUtil.OpenaiApiRole.ASSISTANT]
    assert len(assistant_msgs) == 1
    assert assistant_msgs[0].reasoning_content == ""


def test_fill_missing_reasoning_content_triggered_by_model_name():
    """thinking mode 模型名称隐式启用时，也应补填 reasoning_content。"""
    msg_no_rc = _make_assistant_tool_call_msg(reasoning_content=None)
    request = llmApiUtil.OpenAIRequest(
        model="deepseek-r1",
        messages=[
            llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hello"),
            msg_no_rc,
        ],
        extra_params={},
    )

    next_request, applied_rules = apply_llm_request_rules(request)

    assert "FillMissingReasoningContentRule" in applied_rules
    assistant_msgs = [m for m in next_request.messages if m.role == llmApiUtil.OpenaiApiRole.ASSISTANT]
    assert assistant_msgs[0].reasoning_content == ""


def test_fill_missing_reasoning_content_not_triggered_when_thinking_disabled():
    """thinking.type == "disabled" 时，即使模型是 thinking mode，也不触发。"""
    msg_no_rc = _make_assistant_tool_call_msg(reasoning_content=None)
    request = llmApiUtil.OpenAIRequest(
        model="deepseek-v4-pro",
        messages=[
            llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hello"),
            msg_no_rc,
        ],
        extra_params={"thinking": {"type": "disabled"}},
    )

    next_request, applied_rules = apply_llm_request_rules(request)

    assert "FillMissingReasoningContentRule" not in applied_rules
    assistant_msgs = [m for m in next_request.messages if m.role == llmApiUtil.OpenaiApiRole.ASSISTANT]
    assert assistant_msgs[0].reasoning_content is None


def test_fill_missing_reasoning_content_preserves_existing_reasoning_content():
    """已有 reasoning_content 的消息不应被修改。"""
    msg_with_rc = _make_assistant_tool_call_msg(reasoning_content="I need to think...")
    request = llmApiUtil.OpenAIRequest(
        model="deepseek-v4-pro",
        messages=[
            llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hello"),
            msg_with_rc,
        ],
        extra_params=THINKING_PARAMS,
    )

    next_request, applied_rules = apply_llm_request_rules(request)

    assert "FillMissingReasoningContentRule" not in applied_rules
    assistant_msgs = [m for m in next_request.messages if m.role == llmApiUtil.OpenaiApiRole.ASSISTANT]
    assert assistant_msgs[0].reasoning_content == "I need to think..."


def test_fill_missing_reasoning_content_not_triggered_without_thinking():
    """非 thinking mode 模型且无 thinking 参数时规则不触发。"""
    msg_no_rc = _make_assistant_tool_call_msg(reasoning_content=None)
    request = llmApiUtil.OpenAIRequest(
        model="gpt-4o",
        messages=[
            llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hello"),
            msg_no_rc,
        ],
        extra_params={},
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
        extra_params=THINKING_PARAMS,
    )

    next_request, applied_rules = apply_llm_request_rules(request)

    assert "FillMissingReasoningContentRule" in applied_rules
    assistant_msgs = [m for m in next_request.messages if m.role == llmApiUtil.OpenaiApiRole.ASSISTANT]
    assert assistant_msgs[0].reasoning_content == "thinking..."
    assert assistant_msgs[1].reasoning_content == ""


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
        extra_params=THINKING_PARAMS,
    )

    next_request, applied_rules = apply_llm_request_rules(request)

    assert "FillMissingReasoningContentRule" not in applied_rules
    assistant_msgs = [m for m in next_request.messages if m.role == llmApiUtil.OpenaiApiRole.ASSISTANT]
    assert assistant_msgs[0].reasoning_content is None


def test_fill_missing_reasoning_content_not_triggered_when_extra_params_empty():
    """extra_params 为空字典时不触发 FillMissingReasoningContentRule。"""
    msg_no_rc = _make_assistant_tool_call_msg(reasoning_content=None)
    request = llmApiUtil.OpenAIRequest(
        model="gpt-4o",
        messages=[
            llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hello"),
            msg_no_rc,
        ],
        extra_params={},
    )

    next_request, applied_rules = apply_llm_request_rules(request)

    assert "FillMissingReasoningContentRule" not in applied_rules


def test_fill_missing_reasoning_content_not_triggered_when_thinking_is_string():
    """thinking 为非 dict 值（如字符串）且非 thinking mode 模型时不触发规则。"""
    msg_no_rc = _make_assistant_tool_call_msg(reasoning_content=None)
    request = llmApiUtil.OpenAIRequest(
        model="gpt-4o",
        messages=[
            llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hello"),
            msg_no_rc,
        ],
        extra_params={"thinking": "enabled"},
    )

    next_request, applied_rules = apply_llm_request_rules(request)

    assert "FillMissingReasoningContentRule" not in applied_rules


# ===== StripRequiredToolChoiceForReasoningRule 边界场景 =====


def test_strip_tool_choice_not_triggered_when_reasoning_effort_empty_string():
    """reasoning_effort 为空字符串且非 thinking mode 模型时不触发。"""
    request = llmApiUtil.OpenAIRequest(
        model="gpt-4o",
        messages=[llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hello")],
        tool_choice="required",
        extra_params={"reasoning_effort": ""},
    )

    next_request, applied_rules = apply_llm_request_rules(request)

    assert next_request.tool_choice == "required"
    assert applied_rules == ()


def test_strip_tool_choice_not_triggered_when_tool_choice_is_none():
    """tool_choice 为 None 时不触发。"""
    request = llmApiUtil.OpenAIRequest(
        model="deepseek-v4-pro",
        messages=[llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hello")],
        tool_choice=None,
        extra_params={"reasoning_effort": "high"},
    )

    next_request, applied_rules = apply_llm_request_rules(request)

    assert next_request.tool_choice is None
    assert applied_rules == ()


def test_strip_tool_choice_not_triggered_when_extra_params_empty():
    """extra_params 为空字典且模型非 thinking mode 时不触发。"""
    request = llmApiUtil.OpenAIRequest(
        model="gpt-4o",
        messages=[llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hello")],
        tool_choice="required",
        extra_params={},
    )

    next_request, applied_rules = apply_llm_request_rules(request)

    assert next_request.tool_choice == "required"
    assert applied_rules == ()


# ===== 组合场景 =====


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
        extra_params={
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


def test_both_rules_triggered_by_model_name():
    """thinking mode 模型名称 + tool_choice="required" + 缺失 reasoning_content，
    两条规则应同时触发。"""
    msg_no_rc = _make_assistant_tool_call_msg(reasoning_content=None)
    request = llmApiUtil.OpenAIRequest(
        model="deepseek-r1",
        messages=[
            llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hello"),
            msg_no_rc,
        ],
        tool_choice="required",
        extra_params={},
    )

    next_request, applied_rules = apply_llm_request_rules(request)

    assert next_request.tool_choice is None
    assert "StripRequiredToolChoiceForReasoningRule" in applied_rules
    assert "FillMissingReasoningContentRule" in applied_rules


def test_no_rules_triggered_when_no_conditions_match():
    """无任何规则匹配时，请求原样返回。"""
    request = llmApiUtil.OpenAIRequest(
        model="gpt-4o",
        messages=[llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hello")],
        tool_choice="auto",
        extra_params={},
    )

    next_request, applied_rules = apply_llm_request_rules(request)

    assert next_request.tool_choice == "auto"
    assert next_request.messages == request.messages
    assert applied_rules == ()
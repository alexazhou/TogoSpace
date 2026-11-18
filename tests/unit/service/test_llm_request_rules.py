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

# ===== #305：上游默认开思考、但模型名不带推理标识（主模型 deepseek-flash）=====
#
# 背景：主模型与视觉默认模型都是 `deepseek-flash`（assets/config_template.json），
# 上游在缺省参数下就跑在思考模式；但它的名字不含 r1 / reasoner / v4 / pro 等前缀，
# 旧的「纯名单」判定会把它当非思考模型，于是两条护栏同时失效：
#   ① agentTurnRunner 重试分支下发的 tool_choice="required" 原样上线
#      ⇒ 400 `Thinking mode does not support this tool_choice`；
#   ② FillMissingReasoningContentRule 不触发 ⇒ 历史 assistant tool_call 缺
#      reasoning_content ⇒ 另一条 400。
# 判据分两层：兜底名单要覆盖它；模型配置能显式声明，且配置优先于名单。

_FLASH = "deepseek-flash"


def _single_user_request(model: str, tool_choice=None, extra_params=None):
    return llmApiUtil.OpenAIRequest(
        model=model,
        messages=[llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hello")],
        tool_choice=tool_choice,
        extra_params={} if extra_params is None else extra_params,
    )


def test_deepseek_flash_is_recognized_as_thinking_model():
    """主模型名必须在兜底名单里命中，否则下面两条护栏都会静默失效。"""
    assert _model_in_list(_FLASH, _AUTO_ENABLE_THINKING_MODELS) is True


def test_deepseek_flash_strips_required_tool_choice():
    """主症状：deepseek-flash + `tool_choice="required"` + 未显式传 thinking 参数
    ⇒ 必须剥掉 required（上游默认开思考，带 required 直接 400）。"""
    next_request, applied_rules = apply_llm_request_rules(
        _single_user_request(_FLASH, tool_choice="required")
    )

    assert next_request.tool_choice is None
    assert "StripRequiredToolChoiceForReasoningRule" in applied_rules


def test_deepseek_flash_fills_missing_reasoning_content():
    """第二条 400 路径：同一模型也要补 `reasoning_content`。"""
    request = llmApiUtil.OpenAIRequest(
        model=_FLASH,
        messages=[
            llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "hello"),
            _make_assistant_tool_call_msg(reasoning_content=None),
        ],
        extra_params={},
    )

    next_request, applied_rules = apply_llm_request_rules(request)

    assert "FillMissingReasoningContentRule" in applied_rules
    assistant_msgs = [m for m in next_request.messages if m.role == llmApiUtil.OpenaiApiRole.ASSISTANT]
    assert assistant_msgs[0].reasoning_content == ""


def test_thinking_default_true_covers_model_outside_name_list():
    """承接配置变更：模型配置声明 `thinking_default: true` ⇒ 名单外的新模型也按思考模式
    处理，不必回来改 `_AUTO_ENABLE_THINKING_MODELS`。"""
    next_request, applied_rules = apply_llm_request_rules(
        _single_user_request("vendor-brand-new-reasoner", tool_choice="required"),
        thinking_default=True,
    )

    assert next_request.tool_choice is None
    assert "StripRequiredToolChoiceForReasoningRule" in applied_rules


def test_thinking_default_false_overrides_name_list():
    """配置声明 `thinking_default: false` ⇒ 即使模型名命中兜底名单也不剥 required
    （配置优先于名单，名单只是兜底默认）。"""
    next_request, applied_rules = apply_llm_request_rules(
        _single_user_request("deepseek-r1", tool_choice="required"),
        thinking_default=False,
    )

    assert next_request.tool_choice == "required"
    assert applied_rules == ()


def test_thinking_default_none_keeps_existing_name_list_behavior():
    """未声明（None）⇒ 与 #305 之前完全一致：名单命中就剥（不改变现网语义）。"""
    next_request, _ = apply_llm_request_rules(
        _single_user_request("deepseek-r1", tool_choice="required"),
        thinking_default=None,
    )

    assert next_request.tool_choice is None


def test_explicit_thinking_disabled_overrides_thinking_default_true():
    """请求里显式 `thinking.type="disabled"` 优先于配置声明：用户显式选择最优先，
    否则「明明关了思考却还被剥 required」会削弱强制工具能力。"""
    next_request, applied_rules = apply_llm_request_rules(
        _single_user_request(
            _FLASH,
            tool_choice="required",
            extra_params={"thinking": {"type": "disabled"}},
        ),
        thinking_default=True,
    )

    assert next_request.tool_choice == "required"
    assert applied_rules == ()


def test_build_request_passes_model_config_thinking_default(monkeypatch):
    """接缝判据：`_build_request` 必须把模型配置里的 `thinking_default` 交给规则链。
    否则「配置里能声明」只是空话——读了但没接上，症状与没修一样。"""
    from types import SimpleNamespace

    from service.llmService import core

    captured: dict = {}

    def _fake_apply(request, thinking_default=None):
        captured["model"] = request.model
        captured["thinking_default"] = thinking_default
        captured["tool_choice"] = request.tool_choice
        return request, ()

    monkeypatch.setattr(core, "apply_llm_request_rules", _fake_apply)
    monkeypatch.setattr(
        core.configUtil,
        "get_app_config",
        lambda: SimpleNamespace(
            setting=SimpleNamespace(
                context_config=SimpleNamespace(reserve_output_tokens=1024),
            )
        ),
    )

    ctx = SimpleNamespace(
        system_prompt="sys", messages=[], tools=None,
        tool_choice="required", prompt_cache=False,
    )
    model_config = SimpleNamespace(
        name=_FLASH, input=[], temperature=None, extra_params={},
        thinking_default=True,
        context_config=SimpleNamespace(reserve_output_tokens=1024),
    )

    core._build_request(ctx=ctx, model_config=model_config)

    assert captured["model"] == _FLASH
    assert captured["thinking_default"] is True
    assert captured["tool_choice"] == "required"

from __future__ import annotations

import logging

from util import jsonUtil, llmApiUtil

logger = logging.getLogger(__name__)

# 这些模型的服务商在默认缺省参数下会自动开启 thinking 模式。
#
# 这只是**兜底默认**：权威来源是模型配置的 `LlmModelConfig.thinking_default`
# （见 `_is_thinking_enabled` 的优先级 4）。配置能显式声明/否决，名单只在未声明时兜底，
# 所以新增同类模型优先改配置，不要只靠往这里追加名字。
#
# `deepseek-flash`：上游缺省即开思考（带上 `tool_choice="required"` 直接 400
# `Thinking mode does not support this tool_choice`），但名字不含任何推理标识，
# 靠名字无法与不思考的 `deepseek-chat` 区分——这正是它需要元数据而不是命名的原因。
_AUTO_ENABLE_THINKING_MODELS: tuple[str, ...] = (
    "deepseek-r1",
    "deepseek-reasoner",
    "deepseek-v4",
    "deepseek-pro",
    "deepseek-flash",
    "mimo-v2.5",
    "momo-v2.5-pro",
)


class LlmRequestRule:
    def check_match(
        self,
        request: llmApiUtil.OpenAIRequest,
        *,
        thinking_default: bool | None = None,
    ) -> bool:
        raise NotImplementedError

    def apply(self, request: llmApiUtil.OpenAIRequest) -> llmApiUtil.OpenAIRequest:
        raise NotImplementedError


def _model_in_list(model: str, prefixes: tuple[str, ...]) -> bool:
    """判断 model 名称是否匹配前缀列表中的任意一个（大小写不敏感）。"""
    model_lower = model.lower()
    return any(name in model_lower for name in prefixes)


def _is_thinking_enabled(
    request: llmApiUtil.OpenAIRequest,
    model_prefixes: tuple[str, ...],
    thinking_default: bool | None = None,
) -> bool:
    """判断当前请求是否开启了思考模式。

    触发方式（优先级从高到低）：
    1. extra_params 中 thinking.type == "enabled" / "adaptive" → 开启
    2. extra_params 中 thinking.type == "disabled" → 显式关闭，不触发
    3. extra_params 中设置了 reasoning_effort → 开启
    4. 模型配置 `thinking_default`（`LlmModelConfig`）已声明 → 按声明；`False` 也生效，
       即配置可以否决第 5 条的名单默认
    5. 模型名称匹配 model_prefixes → 开启（未声明时的兜底默认）

    第 4 条是「承接配置变更」的接缝：模型是否上游默认开思考属于**部署事实**，
    不是能靠名字推断的属性（`deepseek-flash` 开思考、`deepseek-chat` 不开，命名上看不出），
    所以把它放在模型配置里，新增同类模型改配置即可，不必回来改代码名单。
    """
    thinking = (request.extra_params or {}).get("thinking") or {}
    if isinstance(thinking, dict):
        thinking_type = thinking.get("type")
        if thinking_type in ("enabled", "adaptive"):
            return True
        if thinking_type == "disabled":
            return False
    reasoning_effort = (request.extra_params or {}).get("reasoning_effort")
    if reasoning_effort not in (None, ""):
        return True
    if thinking_default is not None:
        return thinking_default
    if _model_in_list(request.model, model_prefixes):
        return True
    return False


class StripRequiredToolChoiceForReasoningRule(LlmRequestRule):
    """开启思考模式时，不能强制使用工具，否则 deepseek-v4-pro 等模型会报错。"""

    def check_match(
        self,
        request: llmApiUtil.OpenAIRequest,
        *,
        thinking_default: bool | None = None,
    ) -> bool:
        return request.tool_choice == "required" and _is_thinking_enabled(
            request, _AUTO_ENABLE_THINKING_MODELS, thinking_default,
        )

    def apply(self, request: llmApiUtil.OpenAIRequest) -> llmApiUtil.OpenAIRequest:
        return request.model_copy(update={"tool_choice": None})


class FillMissingReasoningContentRule(LlmRequestRule):
    """开启思考模式时，历史中由非思考模型生成的 assistant tool_call 消息缺少
    reasoning_content 字段，DeepSeek 等模型会报 400 错误。
    对这类消息补填空字符串，使其满足 API 要求。
    """

    def check_match(
        self,
        request: llmApiUtil.OpenAIRequest,
        *,
        thinking_default: bool | None = None,
    ) -> bool:
        if not _is_thinking_enabled(request, _AUTO_ENABLE_THINKING_MODELS, thinking_default):
            return False
        return any(
            msg.role == llmApiUtil.OpenaiApiRole.ASSISTANT
            and msg.tool_calls is not None and len(msg.tool_calls) > 0
            and msg.reasoning_content is None
            for msg in request.messages
        )

    def apply(self, request: llmApiUtil.OpenAIRequest) -> llmApiUtil.OpenAIRequest:
        new_messages = []
        for msg in request.messages:
            if (
                msg.role == llmApiUtil.OpenaiApiRole.ASSISTANT
                and msg.tool_calls is not None and len(msg.tool_calls) > 0
                and msg.reasoning_content is None
            ):
                msg = msg.model_copy(update={"reasoning_content": ""})
            new_messages.append(msg)
        return request.model_copy(update={"messages": new_messages})


class RepairToolArgumentsRule(LlmRequestRule):
    """修复历史消息中 tool_call 的无效 JSON arguments。

    LLM 返回的 tool_calls 中 arguments 可能包含无效 JSON，
    在发送给下一轮 LLM 前需要修复，否则会导致解析错误。
    """

    def check_match(
        self,
        request: llmApiUtil.OpenAIRequest,
        *,
        thinking_default: bool | None = None,   # 与思考模式无关，仅为统一规则签名
    ) -> bool:
        return any(
            msg.tool_calls is not None
            and any(
                not jsonUtil.is_valid_json(tc.function.get("arguments", "{}"))
                for tc in msg.tool_calls
            )
            for msg in request.messages
        )

    def apply(self, request: llmApiUtil.OpenAIRequest) -> llmApiUtil.OpenAIRequest:
        new_messages = []
        for msg in request.messages:
            if msg.tool_calls is None:
                new_messages.append(msg)
                continue
            new_tool_calls = []
            for tc in msg.tool_calls:
                args = tc.function.get("arguments", "{}")
                if not jsonUtil.is_valid_json(args):
                    logger.warning(
                        "sanitizing invalid tool_call arguments: tool_call_id=%s, function=%s",
                        tc.id, tc.function.get("name"),
                    )
                    new_func = dict(tc.function)
                    new_func["arguments"] = "{}"
                    tc = tc.model_copy(update={"function": new_func})
                new_tool_calls.append(tc)
            new_messages.append(msg.model_copy(update={"tool_calls": new_tool_calls}))
        return request.model_copy(update={"messages": new_messages})


_RULES: tuple[LlmRequestRule, ...] = (
    StripRequiredToolChoiceForReasoningRule(),
    FillMissingReasoningContentRule(),
    RepairToolArgumentsRule(),
)


def apply_llm_request_rules(
    request: llmApiUtil.OpenAIRequest,
    *,
    thinking_default: bool | None = None,
) -> tuple[llmApiUtil.OpenAIRequest, tuple[str, ...]]:
    """按规则链修正请求。

    `thinking_default` 来自模型配置（`LlmModelConfig.thinking_default`），只影响
    「是否判定为思考模式」的护栏选择，不会写进请求体、也不发给上游。
    """
    current_request = request
    applied_rules: list[str] = []

    for rule in _RULES:
        if not rule.check_match(current_request, thinking_default=thinking_default):
            continue
        logger.info(
            "llm request rule matched: rule=%s, model=%s, tool_choice=%s, extra_params=%s",
            rule.__class__.__name__,
            current_request.model,
            current_request.tool_choice,
            current_request.extra_params,
        )
        current_request = rule.apply(current_request)
        applied_rules.append(rule.__class__.__name__)

    return current_request, tuple(applied_rules)

from __future__ import annotations

import logging

from util import llmApiUtil

logger = logging.getLogger(__name__)


class LlmRequestRule:
    def check_match(self, request: llmApiUtil.OpenAIRequest) -> bool:
        raise NotImplementedError

    def apply(self, request: llmApiUtil.OpenAIRequest) -> llmApiUtil.OpenAIRequest:
        raise NotImplementedError


class StripRequiredToolChoiceForReasoningRule(LlmRequestRule):
    """开启思考模式时，不能强制使用工具，否则 deepseek-v4-pro 等模型会报错。"""

    def check_match(self, request: llmApiUtil.OpenAIRequest) -> bool:
        reasoning_effort = (request.provider_params or {}).get("reasoning_effort")
        return (
            request.tool_choice == "required"
            and reasoning_effort not in (None, "")
        )

    def apply(self, request: llmApiUtil.OpenAIRequest) -> llmApiUtil.OpenAIRequest:
        return request.model_copy(update={"tool_choice": None})


def _is_thinking_enabled(request: llmApiUtil.OpenAIRequest) -> bool:
    """判断当前请求是否开启了思考模式（thinking.type == "enabled"）。"""
    thinking = (request.provider_params or {}).get("thinking") or {}
    return isinstance(thinking, dict) and thinking.get("type") == "enabled"


class FillMissingReasoningContentRule(LlmRequestRule):
    """开启思考模式时，历史中由非思考模型生成的 assistant tool_call 消息缺少
    reasoning_content 字段，DeepSeek 等模型会报 400 错误。
    对这类消息补填空字符串，使其满足 API 要求。
    """

    def check_match(self, request: llmApiUtil.OpenAIRequest) -> bool:
        if not _is_thinking_enabled(request):
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


_RULES: tuple[LlmRequestRule, ...] = (
    StripRequiredToolChoiceForReasoningRule(),
    FillMissingReasoningContentRule(),
)


def apply_llm_request_rules(
    request: llmApiUtil.OpenAIRequest,
) -> tuple[llmApiUtil.OpenAIRequest, tuple[str, ...]]:
    current_request = request
    applied_rules: list[str] = []

    for rule in _RULES:
        if not rule.check_match(current_request):
            continue
        logger.info(
            "llm request rule matched: rule=%s, model=%s, tool_choice=%s, provider_params=%s",
            rule.__class__.__name__,
            current_request.model,
            current_request.tool_choice,
            current_request.provider_params,
        )
        current_request = rule.apply(current_request)
        applied_rules.append(rule.__class__.__name__)

    return current_request, tuple(applied_rules)

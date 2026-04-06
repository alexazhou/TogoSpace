from dataclasses import dataclass
from typing import Optional

from constants import LlmServiceType
from model.coreModel.gtCoreChatModel import GtCoreAgentDialogContext
from util import configUtil, llmApiUtil

# LiteLLM custom_llm_provider 映射表
_TYPE_TO_PROVIDER = {
    LlmServiceType.OPENAI_COMPATIBLE: "openai",
    LlmServiceType.ANTHROPIC: "anthropic",
    LlmServiceType.GOOGLE: "gemini",
    LlmServiceType.DEEPSEEK: "deepseek",
}


@dataclass
class InferResult:
    ok: bool
    response: Optional[llmApiUtil.OpenAIResponse] = None
    error_message: str = ""
    error: Optional[Exception] = None

    @classmethod
    def success(cls, response: llmApiUtil.OpenAIResponse) -> "InferResult":
        return cls(ok=True, response=response)

    @classmethod
    def failure(cls, error: Exception) -> "InferResult":
        return cls(ok=False, error_message=str(error), error=error)


async def startup() -> None:
    _ = configUtil.get_app_config().setting.current_llm_service


def get_default_model() -> str:
    llm_config = configUtil.get_app_config().setting.current_llm_service
    return llm_config.model


async def infer(model: str | None, ctx: GtCoreAgentDialogContext) -> InferResult:
    """根据 GtCoreAgentDialogContext 组装请求并调用 LLM 推理接口，统一返回成功/失败结果。"""
    try:
        llm_config = configUtil.get_app_config().setting.current_llm_service
        resolved_model = model or llm_config.model
        resolved_provider = _TYPE_TO_PROVIDER.get(llm_config.type)

        messages: list[llmApiUtil.OpenAIMessage] = [
            llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiLLMApiRole.SYSTEM, ctx.system_prompt),
            *ctx.messages,
        ]
        request = llmApiUtil.OpenAIRequest(
            model=resolved_model,
            messages=messages,
            tools=ctx.tools,
        )
        response = await llmApiUtil.send_request_non_stream(
            request,
            llm_config.base_url,
            llm_config.api_key,
            custom_llm_provider=resolved_provider,
            extra_headers=llm_config.extra_headers,
        )
        return InferResult.success(response)
    except Exception as e:
        return InferResult.failure(e)


def shutdown() -> None:
    pass

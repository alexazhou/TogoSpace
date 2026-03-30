from constants import LlmServiceType
from model.coreModel.gtCoreChatModel import GtCoreAgentDialogContext
from util import configUtil, llmApiUtil

# LiteLLM 前缀映射表
_TYPE_TO_PREFIX = {
    LlmServiceType.OPENAI_COMPATIBLE: "openai",
    LlmServiceType.ANTHROPIC: "anthropic",
    LlmServiceType.GOOGLE: "gemini",
    LlmServiceType.DEEPSEEK: "deepseek",
}


async def startup() -> None:
    _ = configUtil.get_app_config().setting.current_llm_service


def get_default_model() -> str:
    llm_config = configUtil.get_app_config().setting.current_llm_service
    return llm_config.model


async def infer(model: str | None, ctx: GtCoreAgentDialogContext) -> llmApiUtil.OpenAIResponse:
    """根据 GtCoreAgentDialogContext 组装请求并调用 LLM 推理接口。"""
    llm_config = configUtil.get_app_config().setting.current_llm_service
    resolved_model = model or llm_config.model

    # 自动补全 LiteLLM 所需的前缀
    if "/" not in resolved_model:
        prefix = _TYPE_TO_PREFIX.get(llm_config.type)
        if prefix:
            resolved_model = f"{prefix}/{resolved_model}"

    messages: list[llmApiUtil.OpenAIMessage] = [llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiLLMApiRole.SYSTEM, ctx.system_prompt), *ctx.messages]
    request = llmApiUtil.OpenAIRequest(
        model=resolved_model,
        messages=messages,
        tools=ctx.tools,
    )
    return await llmApiUtil.send_request_stream(request, llm_config.base_url, llm_config.api_key)


def shutdown() -> None:
    pass

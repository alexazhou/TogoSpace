from model.coreModel.gtCoreChatModel import GtCoreMemberDialogContext
from util import configUtil, llmApiUtil


async def startup() -> None:
    _ = configUtil.get_app_config().setting.current_llm_service


def get_default_model() -> str:
    llm_config = configUtil.get_app_config().setting.current_llm_service
    return llm_config.model


async def infer(model: str | None, ctx: GtCoreMemberDialogContext) -> llmApiUtil.OpenAIResponse:
    """根据 GtCoreMemberDialogContext 组装请求并调用 LLM 推理接口。"""
    llm_config = configUtil.get_app_config().setting.current_llm_service
    resolved_model = model or llm_config.model
    messages: list[llmApiUtil.OpenAIMessage] = [llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiLLMApiRole.SYSTEM, ctx.system_prompt), *ctx.messages]
    request = llmApiUtil.OpenAIRequest(
        model=resolved_model,
        messages=messages,
        tools=ctx.tools,
    )
    return await llmApiUtil.send_request(request, llm_config.base_url, llm_config.api_key)


def shutdown() -> None:
    pass

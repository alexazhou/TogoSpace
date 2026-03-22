from model.coreModel.gtCoreChatModel import AgentDialogContext
from util import llmApiUtil

_api_key: str = ""
_base_url: str = ""


async def startup(api_key: str, base_url: str) -> None:
    global _api_key, _base_url
    _api_key = api_key
    _base_url = base_url


async def infer(model: str, ctx: AgentDialogContext) -> llmApiUtil.LlmApiResponse:
    """根据 AgentDialogContext 组装请求并调用 LLM 推理接口。"""
    messages: list[llmApiUtil.LlmApiMessage] = [llmApiUtil.LlmApiMessage.text(llmApiUtil.OpenaiLLMApiRole.SYSTEM, ctx.system_prompt), *ctx.messages]
    request = llmApiUtil.LlmApiRequest(
        model=model,
        messages=messages,
        tools=ctx.tools,
    )
    return await llmApiUtil.send_request(request, _base_url, _api_key)


def shutdown() -> None:
    global _api_key, _base_url
    _api_key = ""
    _base_url = ""

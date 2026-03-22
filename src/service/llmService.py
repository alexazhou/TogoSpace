from model.coreModel.gtCoreChatModel import AgentDialogContext
from util import llm_api_util

_api_key: str = ""
_base_url: str = ""


async def startup(api_key: str, base_url: str) -> None:
    global _api_key, _base_url
    _api_key = api_key
    _base_url = base_url


async def infer(model: str, ctx: AgentDialogContext) -> llm_api_util.LlmApiResponse:
    """根据 AgentDialogContext 组装请求并调用 LLM 推理接口。"""
    messages: list[llm_api_util.LlmApiMessage] = [llm_api_util.LlmApiMessage.text(llm_api_util.OpenaiLLMApiRole.SYSTEM, ctx.system_prompt), *ctx.messages]
    request = llm_api_util.LlmApiRequest(
        model=model,
        messages=messages,
        tools=ctx.tools,
    )
    return await llm_api_util.send_request(request, _base_url, _api_key)


def shutdown() -> None:
    global _api_key, _base_url
    _api_key = ""
    _base_url = ""

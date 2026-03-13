import util.llm_api_util as llm_api
from model.chat_model import AgentDialogContext
from util.llm_api_util import OpenaiLLMApiRole, LlmApiMessage, LlmApiRequest, LlmApiResponse

_api_key: str = ""
_base_url: str = ""


def startup(api_key: str, base_url: str) -> None:
    global _api_key, _base_url
    _api_key = api_key
    _base_url = base_url


async def infer(model: str, ctx: AgentDialogContext) -> LlmApiResponse:
    """根据 AgentDialogContext 组装请求并调用 LLM 推理接口。"""
    messages = [LlmApiMessage.text(OpenaiLLMApiRole.SYSTEM, ctx.system_prompt), *ctx.messages]
    request = LlmApiRequest(
        model=model,
        messages=messages,
        tools=ctx.tools,
    )
    return await llm_api.send_request(request, _base_url, _api_key)

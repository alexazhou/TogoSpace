from model.coreModel.gtCoreChatModel import AgentDialogContext
from util import llmApiUtil

_api_key: str = ""
_base_url: str = ""
_default_model: str = "qwen-plus"


async def startup(api_key: str, base_url: str, model: str | None = None) -> None:
    global _api_key, _base_url, _default_model
    _api_key = api_key
    _base_url = base_url
    if model:
        _default_model = model


def get_default_model() -> str:
    return _default_model


async def infer(model: str | None, ctx: AgentDialogContext) -> llmApiUtil.OpenAIResponse:
    """根据 AgentDialogContext 组装请求并调用 LLM 推理接口。"""
    resolved_model = model or _default_model
    messages: list[llmApiUtil.OpenAIMessage] = [llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiLLMApiRole.SYSTEM, ctx.system_prompt), *ctx.messages]
    request = llmApiUtil.OpenAIRequest(
        model=resolved_model,
        messages=messages,
        tools=ctx.tools,
    )
    return await llmApiUtil.send_request(request, _base_url, _api_key)


def shutdown() -> None:
    global _api_key, _base_url, _default_model
    _api_key = ""
    _base_url = ""
    _default_model = "qwen-plus"

import logging
from typing import Optional

import litellm
from .OpenAiModels import OpenAIRequest, OpenAIResponse


logger = logging.getLogger(__name__)


def init() -> None:
    """初始化 llmApiUtil。使用 litellm 后，此方法主要用于设置全局配置。"""
    # 如果需要，可以在这里设置 litellm 的全局配置，例如：
    # litellm.set_verbose = True
    # litellm.drop_params = True
    pass


def _clean_base_url(url: str) -> str:
    """清理 base_url，移除末尾可能存在的 /chat/completions 路径，防止 litellm 重复拼接。"""
    if not url:
        return url
    
    base_url = url
    if base_url.endswith("/chat/completions"):
        base_url = base_url[:-len("/chat/completions")]
    elif base_url.endswith("/chat/completions/"):
        base_url = base_url[:-len("/chat/completions/")]
    
    return base_url.rstrip("/")


async def send_request(request: OpenAIRequest, url: str, api_key: str) -> OpenAIResponse:
    """使用 litellm 发送 chat completion 请求。"""
    
    # 构造 litellm.acompletion 的参数
    # 如果提供了 url (base_url)，且模型没有 provider 前缀，
    # 我们默认将其视为 openai 兼容接口，并添加 'openai/' 前缀以确保 litellm 正确路由。
    model_name = request.model
    if url and "/" not in model_name:
        model_name = f"openai/{model_name}"

    # 清理 url：litellm 会自动添加 /chat/completions
    base_url = _clean_base_url(url)

    messages = [m.to_dict() for m in request.messages]
    
    tools = None
    if request.tools:
        tools = [t.model_dump(exclude_none=True) for t in request.tools]

    try:
        response = await litellm.acompletion(
            model=model_name,
            messages=messages,
            api_key=api_key,
            base_url=base_url,
            tools=tools,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            stream=request.stream,
        )
        
        # litellm 返回的是 ModelResponse 对象，它支持 .json() 序列化，且格式与 OpenAI 一致
        # 我们直接使用 OpenAIResponse.model_validate 转换回我们的 Pydantic 模型
        response_dict = response.json()
        return OpenAIResponse.model_validate(response_dict)

    except Exception as e:
        logger.error(f"LiteLLM API 调用失败: {e}", exc_info=True)
        # 维持原有的错误抛出行为，虽然错误信息格式可能略有不同
        raise RuntimeError(f"API 调用失败: {e}")

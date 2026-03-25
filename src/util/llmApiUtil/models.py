from typing import List, Optional
from pydantic import BaseModel, Field

from constants import OpenaiLLMApiRole


# ========== 主要类 ==========

class OpenAIMessage(BaseModel):
    # 对应 openai 格式
    role: OpenaiLLMApiRole = Field(..., description="消息角色")
    content: Optional[str] = Field(None, description="消息内容")
    reasoning_content: Optional[str] = Field(None, description="推理内容（如 CoT 模型），仅响应侧使用")
    tool_calls: Optional[List["OpenAIToolCall"]] = Field(None, description="工具调用列表")
    tool_call_id: Optional[str] = Field(None, description="工具调用 ID（tool 角色专用）")

    @classmethod
    def text(cls, role: OpenaiLLMApiRole, content: str) -> "OpenAIMessage":
        """构造普通文本消息（system / user / assistant）。"""
        return cls(
            role=role,
            content=content,
            reasoning_content=None,
            tool_calls=None,
            tool_call_id=None,
        )

    @classmethod
    def tool_result(cls, tool_call_id: str, result: str) -> "OpenAIMessage":
        """构造工具调用结果消息。"""
        return cls(
            role=OpenaiLLMApiRole.TOOL,
            content=result,
            reasoning_content=None,
            tool_calls=None,
            tool_call_id=tool_call_id,
        )

    def to_dict(self) -> dict:
        """序列化为发送给 API 的 dict，排除 reasoning_content 和 None 字段。"""
        return self.model_dump(mode="json", exclude_none=True, exclude={"reasoning_content"})


class OpenAIRequest(BaseModel):
    model: str = Field(default="qwen-plus", description="模型名称")
    messages: List[OpenAIMessage] = Field(..., description="消息列表")
    max_tokens: Optional[int] = Field(default=1024, description="最大输出 tokens")
    temperature: Optional[float] = Field(default=0.7, description="温度参数")
    stream: Optional[bool] = Field(default=False, description="是否流式输出")
    tools: Optional[List["OpenAITool"]] = Field(None, description="工具列表")


class OpenAIResponse(BaseModel):
    id: str
    object: str
    created: int
    model: str
    choices: List["OpenAIChoice"]
    usage: "OpenAIUsage"
    system_fingerprint: Optional[str] = None

    @property
    def request_id(self) -> str:
        return self.id


# ========== 请求侧辅助类 ==========

class OpenAIToolCall(BaseModel):
    id: str
    type: str = Field(default="function")
    function: dict


class OpenAIFunctionParameter(BaseModel):
    type: str
    properties: dict
    required: List[str]


class OpenAIFunction(BaseModel):
    name: str
    description: str
    parameters: OpenAIFunctionParameter


class OpenAITool(BaseModel):
    type: str = Field(default="function", description="工具类型")
    function: OpenAIFunction


# ========== 响应侧辅助类 ==========

class OpenAIUsage(BaseModel):
    prompt_tokens: int = Field(..., description="输入 tokens 数量")
    completion_tokens: int = Field(..., description="输出 tokens 数量")
    total_tokens: int = Field(..., description="总 tokens 数量")
    prompt_tokens_details: Optional[dict] = Field(None, description="输入 tokens 详情")
    completion_tokens_details: Optional[dict] = Field(None, description="输出 tokens 详情")

    @property
    def input_tokens(self) -> int:
        return self.prompt_tokens

    @property
    def output_tokens(self) -> int:
        return self.completion_tokens


class OpenAIChoice(BaseModel):
    index: int
    message: OpenAIMessage
    finish_reason: str
    logprobs: Optional[dict] = None


class OpenAIErrorResponse(BaseModel):
    code: str
    message: str
    request_id: str

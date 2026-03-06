import json
from typing import List, Optional
from pydantic import BaseModel, Field


# ========== Pydantic 数据模型 ==========

class Message(BaseModel):
    role: str = Field(..., description="消息角色: user, assistant, system, tool")
    content: Optional[str] = Field(None, description="消息内容")
    reasoning_content: Optional[str] = Field(None, description="推理内容（如 CoT 模型）")
    tool_calls: Optional[List["ToolCall"]] = Field(None, description="工具调用列表")
    tool_call_id: Optional[str] = Field(None, description="工具调用 ID（tool 角色专用）")


class FunctionParameter(BaseModel):
    type: str
    properties: dict
    required: List[str]


class Function(BaseModel):
    name: str
    description: str
    parameters: FunctionParameter


class Tool(BaseModel):
    type: str = Field(default="function", description="工具类型")
    function: Function


class ToolCall(BaseModel):
    id: str
    type: str = Field(default="function")
    function: dict


class ChatCompletionRequest(BaseModel):
    model: str = Field(default="qwen-plus", description="模型名称")
    messages: List[dict] = Field(..., description="消息列表")
    max_tokens: Optional[int] = Field(default=1024, description="最大输出 tokens")
    temperature: Optional[float] = Field(default=0.7, description="温度参数")
    stream: Optional[bool] = Field(default=False, description="是否流式输出")
    tools: Optional[List[Tool]] = Field(None, description="工具列表")


class Usage(BaseModel):
    prompt_tokens: int = Field(..., description="输入 tokens 数量")
    completion_tokens: int = Field(..., description="输出 tokens 数量")
    total_tokens: int = Field(..., description="总 tokens 数量")
    prompt_tokens_details: Optional[dict] = Field(None, description="输入 tokens 详情")
    completion_tokens_details: Optional[dict] = Field(None, description="输出 tokens 详情")

    # 兼容旧字段名
    @property
    def input_tokens(self) -> int:
        return self.prompt_tokens

    @property
    def output_tokens(self) -> int:
        return self.completion_tokens


class Choice(BaseModel):
    index: int
    message: Message
    finish_reason: str
    logprobs: Optional[dict] = None


class ChatCompletionResponse(BaseModel):
    id: str
    object: str
    created: int
    model: str
    choices: List[Choice]
    usage: Usage
    system_fingerprint: Optional[str] = None

    # 兼容字段
    @property
    def request_id(self) -> str:
        return self.id


class ErrorResponse(BaseModel):
    code: str
    message: str
    request_id: str

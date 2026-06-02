import json
import logging
from typing import Any, List, Optional
from pydantic import BaseModel, Field, model_validator

from constants import OpenaiApiRole, ToolCategory
from util.commonUtil import first_not_none

logger = logging.getLogger(__name__)


# ========== 主要类 ==========

class OpenAIMessage(BaseModel):
    # 对应 openai 格式
    role: OpenaiApiRole = Field(..., description="消息角色")
    content: Optional[str] = Field(None, description="消息内容")
    reasoning_content: Optional[str] = Field(None, description="推理内容（如 CoT 模型），仅响应侧使用")
    tool_calls: Optional[List["OpenAIToolCall"]] = Field(None, description="工具调用列表")
    tool_call_id: Optional[str] = Field(None, description="工具调用 ID（tool 角色专用）")

    @classmethod
    def text(cls, role: OpenaiApiRole, content: str) -> "OpenAIMessage":
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
            role=OpenaiApiRole.TOOL,
            content=result,
            reasoning_content=None,
            tool_calls=None,
            tool_call_id=tool_call_id,
        )

    def to_dict(self) -> dict:
        """序列化为发送给 API 的 dict，排除 None 字段。

        注意：reasoning_content 现在也会被序列化，用于 DeepSeek/GLM 等 CoT 模型的思考链回传。
        """
        return self.model_dump(mode="json", exclude_none=True)

    def model_dump(self, **kwargs) -> dict:
        """重写 model_dump，确保嵌套的 tool_calls.function.arguments 是有效 JSON。"""
        result = super().model_dump(**kwargs)
        if "tool_calls" in result and result["tool_calls"]:
            for tc in result["tool_calls"]:
                if "function" in tc and isinstance(tc["function"], dict):
                    args = tc["function"].get("arguments", "{}")
                    if isinstance(args, str) and args:
                        try:
                            json.loads(args)
                        except (json.JSONDecodeError, ValueError):
                            logger.warning(
                                "message.tool_call arguments invalid JSON, fallback to '{}': tool_call_id=%s, function=%s, raw_args=%s",
                                tc.get("id"), tc.get("function", {}).get("name"), args[:200],
                            )
                            tc["function"]["arguments"] = "{}"
                    elif not args:
                        tc["function"]["arguments"] = "{}"
        return result


class OpenAIRequest(BaseModel):
    model: str = Field(default="qwen-plus", description="模型名称")
    messages: List[OpenAIMessage] = Field(..., description="消息列表")
    max_tokens: Optional[int] = Field(default=8192, description="最大输出 tokens")
    temperature: Optional[float] = Field(default=None, description="温度参数")
    stream: Optional[bool] = Field(default=False, description="是否流式输出")
    tools: Optional[List["OpenAITool"]] = Field(None, description="工具列表")
    tool_choice: Optional[str | dict[str, Any]] = Field(None, description="工具调用策略")
    prompt_cache: bool = Field(default=False, description="是否启用 prompt cache")
    provider_params: dict[str, Any] = Field(default_factory=dict, description="额外透传给 LiteLLM 的 provider 参数")


class PromptCacheUsage(BaseModel):
    """项目内部统一的缓存信息视图。

    约定：
    - `None` 表示上游未返回 / 当前无法确认
    - `0` 表示上游明确返回了 0
    """
    cached_tokens: Optional[int] = None
    cache_write_tokens: Optional[int] = None


class OpenAIUsage(BaseModel):
    """LLM 响应中的 token 用量统计。"""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    prompt_cache_usage: Optional[PromptCacheUsage] = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_prompt_cache_usage(cls, value):
        if not isinstance(value, dict) or value.get("prompt_cache_usage") is not None:
            return value

        ptd = value.get("prompt_tokens_details") or {}
        cached = first_not_none(ptd.get("cached_tokens"), value.get("cache_read_input_tokens"))
        write  = first_not_none(value.get("cache_creation_input_tokens"), ptd.get("cache_creation_tokens"))

        if cached is None and write is None:
            return value
        return {**value, "prompt_cache_usage": {"cached_tokens": cached, "cache_write_tokens": write}}

class OpenAIResponse(BaseModel):
    id: str
    object: str
    created: int
    model: str
    choices: List["OpenAIChoice"]
    system_fingerprint: Optional[str] = None
    usage: Optional[OpenAIUsage] = None

    @property
    def request_id(self) -> str:
        return self.id


# ========== 请求侧辅助类 ==========

class OpenAIToolCall(BaseModel):
    id: str
    type: str = Field(default="function")
    function: dict

    def verify(self) -> "OpenAIToolCall":
        """验证字段完整性，返回 self 便于链式调用。"""
        assert isinstance(self.id, str) and self.id, "tool_call.id 应为非空 str"
        assert isinstance(self.function, dict), "tool_call.function 应为 dict"
        name = self.function.get("name")
        assert isinstance(name, str) and name, "tool_call.function.name 应为非空 str"
        args = self.function.get("arguments", "{}")
        assert isinstance(args, str), "tool_call.function.arguments 应为 str"
        return self

    def _sanitize_arguments(self) -> str:
        """验证 arguments JSON 有效性，无效时降级为 '{}'。"""
        args = self.function.get("arguments", "{}")
        if isinstance(args, str) and args:
            try:
                json.loads(args)
            except (json.JSONDecodeError, ValueError):
                logger.warning(
                    "tool_call arguments invalid JSON, fallback to '{}': tool_call_id=%s, function=%s, raw_args=%s",
                    self.id, self.function.get("name"), args[:200],
                )
                return "{}"
        return args or "{}"

    @property
    def function_name(self) -> str:
        return self.function["name"]

    @property
    def function_args(self) -> str:
        return self._sanitize_arguments()

    @property
    def tool_call_id(self) -> str:
        return self.id

    def model_dump(self, **kwargs) -> dict:
        """重写 model_dump，确保 function.arguments 是有效 JSON。"""
        result = super().model_dump(**kwargs)
        if "function" in result and isinstance(result["function"], dict):
            result["function"]["arguments"] = self._sanitize_arguments()
        return result


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
    category: Optional[ToolCategory] = Field(default=None, exclude=True, description="本地工具分类，仅内部使用")


# ========== 响应侧辅助类 ==========


class OpenAIChoice(BaseModel):
    index: int
    message: OpenAIMessage
    finish_reason: str
    logprobs: Optional[dict] = None


class OpenAIErrorResponse(BaseModel):
    code: str
    message: str
    request_id: str

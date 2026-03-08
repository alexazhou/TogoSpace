from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class OpenaiLLMApiRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class TurnStatus(str, Enum):
    SUCCESS = "success"    # 本轮对话完成，停止循环
    CONTINUE = "continue"  # 继续执行 tool calls 循环
    ERROR = "error"        # 注入 error_hint 后重试


@dataclass
class TurnCheckResult:
    status: TurnStatus
    error_hint: Optional[str] = field(default=None)  # 仅 ERROR 时使用

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class OpenaiLLMApiRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class MessageBusTopic(str, Enum):
    ROOM_AGENT_TURN = "room.agent_turn"  # 轮到某 Agent 发言；payload: agent_name, room_name
    ROOM_MSG_ADDED  = "room.message_added"  # 房间新增消息；payload: room_name, sender, content, time


class RoomType(str, Enum):
    PRIVATE = "private"  # 1v1 单聊模式 (Human + Agent)
    GROUP = "group"      # 多 Agent 自治群聊模式


class SpecialAgent(str, Enum):
    OPERATOR = "Operator"  # 人类操作者虚拟身份


class RoomState(Enum):
    SCHEDULING = "scheduling"  # 房间正在调度，有事件待处理
    IDLE = "idle"              # 房间空闲，无更多事件


class TurnStatus(str, Enum):
    SUCCESS = "success"    # 本轮对话完成，停止循环
    CONTINUE = "continue"  # 继续执行 tool calls 循环
    ERROR = "error"        # 注入 error_hint 后重试


@dataclass
class TurnCheckResult:
    status: TurnStatus
    error_hint: Optional[str] = field(default=None)  # 仅 ERROR 时使用

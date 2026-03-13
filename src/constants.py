from enum import Enum


class OpenaiLLMApiRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class MessageBusTopic(str, Enum):
    ROOM_AGENT_TURN = "room.agent_turn"  # 轮到某 Agent 发言；payload: agent_name, room_name
    ROOM_MSG_ADDED  = "room.message_added"  # 房间新增消息；payload: room_name, sender, content, time
    AGENT_STATUS_CHANGED = "agent.status_changed"  # Agent 忙闲状态变更；payload: agent_name, status


class RoomType(str, Enum):
    PRIVATE = "private"  # 1v1 单聊模式 (Human + Agent)
    GROUP = "group"      # 多 Agent 自治群聊模式


class SpecialAgent(str, Enum):
    OPERATOR = "Operator"  # 人类操作者虚拟身份


class RoomState(Enum):
    SCHEDULING = "scheduling"  # 房间正在调度，有事件待处理
    IDLE = "idle"              # 房间空闲，无更多事件



from enum import Enum, auto


class EnhanceEnum(Enum):
    @classmethod
    def value_of(cls, value: str):
        for m, mm in cls.__members__.items():
            if value is not None and m.upper() == value.upper():
                return mm
        return None

    def __repr__(self):
        return '[' + self.name + ']'


class OpenaiLLMApiRole(EnhanceEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class LlmServiceType(str, Enum):
    OPENAI_COMPATIBLE = "openai-compatible"


class MessageBusTopic(EnhanceEnum):
    ROOM_AGENT_TURN = auto()      # 轮到某 Agent 发言；payload: agent_name, room_id, room_name, team_name
    ROOM_MSG_ADDED = auto()       # 房间新增消息；payload: room_id, room_key, room_name, team_name, sender, content, time
    AGENT_STATUS_CHANGED = auto() # Agent 忙闲状态变更；payload: agent_name, status(AgentStatus.name)


class RoomType(EnhanceEnum):
    PRIVATE = auto()  # 1v1 单聊模式 (Human + Agent)
    GROUP = auto()    # 多 Agent 自治群聊模式


class SpecialAgent(EnhanceEnum):
    OPERATOR = auto()  # 人类操作者虚拟身份


class RoomState(EnhanceEnum):
    INIT = auto()        # 房间初始化态：不推送事件，不持久化
    SCHEDULING = auto()  # 房间正在调度，有事件待处理
    IDLE = auto()        # 房间空闲，无更多事件


class AgentStatus(EnhanceEnum):
    ACTIVE = auto()
    IDLE = auto()

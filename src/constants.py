from enum import Enum, auto


class EnhanceEnum(Enum):
    @classmethod
    def value_of(cls, value: str):
        for m, mm in cls.__members__.items():
            if value is not None and m.upper() == value.upper():
                return mm
        return None

    @classmethod
    def _missing_(cls, value):
        """支持大小写不敏感的 value 匹配，供 Pydantic 使用。"""
        if isinstance(value, str):
            for member in cls:
                if member.value.lower() == value.lower():
                    return member
        return None

    def __repr__(self):
        return '[' + self.name + ']'


class OpenaiLLMApiRole(EnhanceEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class LlmServiceType(EnhanceEnum):
    OPENAI_COMPATIBLE = "openai-compatible"


class MessageBusTopic(EnhanceEnum):
    ROOM_MEMBER_TURN = auto()      # 轮到某成员发言；payload: member_name, room_id, room_name, team_name
    ROOM_MSG_ADDED = auto()        # 房间新增消息；payload: room_id, room_key, room_name, team_name, sender, content, time
    MEMBER_STATUS_CHANGED = auto() # 成员忙闲状态变更；payload: member_name, status(MemberStatus.name)


class RoomType(EnhanceEnum):
    PRIVATE = auto()  # 1v1 单聊模式 (Human + Agent)
    GROUP = auto()    # 多 Agent 自治群聊模式


class SpecialAgent(EnhanceEnum):
    SYSTEM = auto()    # 系统消息发送者
    OPERATOR = auto()  # 人类操作者虚拟身份


class RoomState(EnhanceEnum):
    INIT = auto()        # 房间初始化态：不推送事件，不持久化
    SCHEDULING = auto()  # 房间正在调度，有事件待处理
    IDLE = auto()        # 房间空闲，无更多事件


class MemberStatus(EnhanceEnum):
    ACTIVE = auto()
    IDLE = auto()


class EmployStatus(EnhanceEnum):
    ON_BOARD = auto()   # 在职，挂载在某部门
    OFF_BOARD = auto()  # 休闲，已从部门移除


class DriverType(EnhanceEnum):
    NATIVE = "native"           # 原生 OpenAI API 驱动
    CLAUDE_SDK = "claude_sdk"   # Claude Agent SDK 驱动
    TSP = "tsp"                 # TSP 协议驱动


class RoleTemplateType(EnhanceEnum):
    SYSTEM = "system"   # 启动时从配置导入
    USER = "user"       # 运行时由后台创建


class SystemConfigKey(EnhanceEnum):
    """系统配置项的 key 枚举。"""
    WORKING_DIRECTORY = "working_directory"  # 系统级别工作目录

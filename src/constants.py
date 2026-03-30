import re
from enum import Enum, auto


class EnhanceEnum(Enum):
    @classmethod
    def _normalize_token(cls, value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")

    @classmethod
    def value_of(cls, value: str):
        if value is None:
            return None

        if isinstance(value, cls):
            return value

        try:
            return cls(value)
        except (TypeError, ValueError):
            pass

        if isinstance(value, str):
            normalized = cls._normalize_token(value)
            for member in cls:
                if cls._normalize_token(member.name) == normalized:
                    return member
                if isinstance(member.value, str) and cls._normalize_token(member.value) == normalized:
                    return member
        return None

    @classmethod
    def _missing_(cls, value):
        """支持字符串大小写不敏感匹配。

        匹配顺序：
        1. 枚举 name（例如 "GROUP" -> RoomType.GROUP）
        2. 字符串 value（例如 "native" -> DriverType.NATIVE）
        """
        if isinstance(value, str):
            normalized = cls._normalize_token(value)
            for member in cls:
                if cls._normalize_token(member.name) == normalized:
                    return member
                if isinstance(member.value, str) and cls._normalize_token(member.value) == normalized:
                    return member
        return None

    def __repr__(self):
        return '[' + self.name + ']'


class OpenaiLLMApiRole(EnhanceEnum):
    # OpenAI 协议要求 role 使用固定小写字符串，不使用 auto()。
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class LlmServiceType(EnhanceEnum):
    # 配置文件中的 type 使用固定字符串（含连字符），不使用 auto()。
    OPENAI_COMPATIBLE = "openai-compatible"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    DEEPSEEK = "deepseek"


class MessageBusTopic(EnhanceEnum):
    ROOM_MEMBER_TURN = auto()      # 轮到某成员发言；payload: member_name, room_id, room_name, team_name
    ROOM_MSG_ADDED = auto()        # 房间新增消息；payload: room_id, room_key, room_name, team_name, sender, content, time
    MEMBER_STATUS_CHANGED = auto() # 成员忙闲状态变更；payload: member_name, status(MemberStatus.name)


class RoomType(EnhanceEnum):
    PRIVATE = auto()  # 1v1 单聊模式 (Human + Agent)
    GROUP = auto()    # 多 Agent 自治群聊模式


class SpecialAgent(EnhanceEnum):
    SYSTEM = -2    # 系统消息发送者
    OPERATOR = -1  # 人类操作者虚拟身份


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
    # 对外 API/配置约定使用固定小写字符串，不使用 auto()。
    NATIVE = "native"           # 原生 OpenAI API 驱动
    CLAUDE_SDK = "claude_sdk"   # Claude Agent SDK 驱动
    TSP = "tsp"                 # TSP 协议驱动


class RoleTemplateType(EnhanceEnum):
    # 角色模板类型是对外字段约定，保存小写字符串，不使用 auto()。
    # 保留 "system" / "user" 两个固定值。
    SYSTEM = "system"   # 启动时从配置导入
    USER = "user"       # 运行时由后台创建


class SystemConfigKey(EnhanceEnum):
    """系统配置项的 key 枚举。"""
    # DB 中 key 字段是稳定字符串，不使用 auto()。
    WORKING_DIRECTORY = "working_directory"  # 系统级别工作目录

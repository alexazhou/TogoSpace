import re
from enum import Enum, auto
from typing import Any, Optional, Union
from typing_extensions import Self


class EnhanceEnum(Enum):
    @classmethod
    def _normalize_token(cls, value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")

    @classmethod
    def value_of(cls, value: Optional[Union[int, str]]) -> Self | None:
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
    def _missing_(cls, value: Any) -> Self | None:
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

    def __repr__(self) -> str:
        return '[' + self.name + ']'


class OpenaiApiRole(EnhanceEnum):
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
    ROOM_MSG_ADDED = auto()            # 消息插入 store 时（含 pending）；payload: gt_room(GtRoom), gt_message(GtCoreRoomMessage)
    ROOM_MSG_CHANGED = auto()          # 消息状态变化（升级为 immediately 或被消费分配 seq）；payload: gt_room(GtRoom), gt_message(GtCoreRoomMessage)
    ROOM_STATUS_CHANGED = auto()       # 房间状态/发言人变更；payload: gt_room(GtRoom), state(RoomState), current_turn_agent_id(int|None), need_scheduling(bool)
    ROOM_ADDED = auto()                # 新房间创建（按需建控制房间）；payload: gt_room(GtRoom), team_id(int)
    AGENT_STATUS_CHANGED = auto()      # Agent 忙闲状态变更；payload: gt_agent(GtAgent), status(AgentStatus)
    AGENT_ACTIVITY_CHANGED = auto()    # Agent 活动记录变更；payload: event, data
    SCHEDULE_STATE_CHANGED = auto()    # 调度闸门状态变更；payload: schedule_state(str)
    TEAM_RELOADED = auto()             # Team 热更新完成；payload: team_id(int)
    TASK_CREATED = auto()              # 协作任务创建；payload: task(GtAgentTask)
    TASK_CHANGED = auto()              # 协作任务状态变更；payload: task(GtAgentTask), old_status(str)


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


class AgentStatus(EnhanceEnum):
    ACTIVE = auto()
    IDLE   = auto()
    FAILED = auto()  # 任务执行失败


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


class ToolCategory(EnhanceEnum):
    ADMIN = auto()    # 团队管理工具
    BASIC = auto()    # 群聊协作基础工具
    READ = auto()     # 通用只读查询工具
    WRITE = auto()    # 通用写入类工具
    EXECUTE = auto()  # 通用执行/控制类工具

    @classmethod
    def from_spec(cls, spec: str) -> "ToolCategory | None":
        """解析 'Category:Xxx' 格式的类别规格字符串，返回对应的 ToolCategory。"""
        prefix, sep, category_name = spec.partition(":")
        if sep != ":" or prefix.strip().lower() != "category":
            return None
        return cls.value_of(category_name)


class SystemConfigKey(EnhanceEnum):
    """系统配置项的 key 枚举。"""
    # DB 中 key 字段是稳定字符串，不使用 auto()。
    WORKING_DIRECTORY = "working_directory"  # 系统级别工作目录


class AgentHistoryTag(EnhanceEnum):
    ROOM_TURN_BEGIN = auto()
    ROOM_TURN_FINISH = auto()
    COMPACT_SUMMARY = auto()
    SELF_INTERRUPT = auto()  # 该 TOOL/INIT 条目由自中断工具产生，重启后自动标记为成功


class AgentHistoryStatus(EnhanceEnum):
    INIT = auto()
    SUCCESS = auto()
    FAILED = auto()
    CANCELLED = auto()


class AgentActivityType(EnhanceEnum):
    LLM_INFER = auto()
    TOOL_CALL = auto()
    COMPACT = auto()
    AGENT_STATE = auto()
    REASONING = auto()      # 思考内容（reasoning_content）
    CHAT_REPLY = auto()     # 直接发言（有 content 但无 tool_calls）
    MESSAGE_RECEIVED = auto()  # 收到房间消息
    TASK_RECEIVED = auto()     # 收到协作任务提醒


class AgentActivityStatus(EnhanceEnum):
    STARTED = auto()
    SUCCEEDED = auto()
    FAILED = auto()
    CANCELLED = auto()


class InferRequestStateType(EnhanceEnum):
    RETRY_SCHEDULED = auto()
    RETRYING = auto()


class TaskStatus(EnhanceEnum):
    """协作任务状态枚举。"""
    TODO        = "TODO"
    PENDING     = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    ON_HOLD     = "ON_HOLD"
    REVIEWING   = "REVIEWING"
    DONE        = "DONE"
    CANCELLED   = "CANCELLED"


class TaskPriority(EnhanceEnum):
    """协作任务优先级枚举。"""
    HIGH   = "HIGH"
    NORMAL = "NORMAL"
    LOW    = "LOW"


class AgentTaskType(EnhanceEnum):
    """Agent 任务类型枚举。"""
    ROOM_MESSAGE = auto()
    TODO_TASK = auto()  # 协作任务驱动（Agent 间结构化任务）


class AgentTaskStatus(EnhanceEnum):
    """Agent 任务状态枚举。"""
    PENDING = auto()      # 待处理
    RUNNING = auto()      # 正在处理
    COMPLETED = auto()    # 已完成
    FAILED = auto()       # 失败
    CANCELLED = auto()    # 被人工停止


class TurnStepResult(EnhanceEnum):
    """Turn 内部单步推进结果枚举。"""
    TURN_DONE = auto()                      # finish 类工具执行成功，turn 结束
    TOOL_EXECUTE_SUCCESS = auto()           # 非 finish 工具执行成功，turn 继续推进
    TOOL_EXECUTE_FAILED_FINISH = auto()     # finish 类工具执行失败
    LLM_OUTPUT_ERROR = auto()               # 模型输出格式异常（如将 tool call 写入 content 字段）
    LLM_OUTPUT_NO_ACTION = auto()           # 模型输出纯文本，无工具调用
    LLM_OUTPUT_TOOL_CALLS = auto()          # 模型生成了 tool_calls，待执行


class ScheduleState(EnhanceEnum):
    """调度闸门状态。"""
    STOPPED = auto()    # 调度未开启或已显式停止
    BLOCKED = auto()    # 前置条件不满足（如未配置 LLM）
    RUNNING = auto()    # 允许房间激活、创建 task、启动 consumer

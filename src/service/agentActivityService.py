"""agentActivityService: 活动记录的创建、更新与广播。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, fields
from datetime import datetime
from typing import Any

from constants import AgentActivityStatus, AgentActivityType, InferRequestStateType, MessageBusTopic
from dal.db import gtAgentActivityManager
from model.coreModel.agentModel import ExecEnvState
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtAgentActivity import GtAgentActivity
from service import messageBus

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = {
    AgentActivityStatus.SUCCEEDED,
    AgentActivityStatus.FAILED,
    AgentActivityStatus.CANCELLED,
}

_DEFAULT_TITLES: dict[AgentActivityType, str] = {
    AgentActivityType.AGENT_STATE: "状态变更",
    AgentActivityType.LLM_INFER: "推理",
    AgentActivityType.TOOL_CALL: "调用工具",
    AgentActivityType.COMPACT: "压缩上下文",
    AgentActivityType.REASONING: "思考",
    AgentActivityType.CHAT_REPLY: "发言",
    AgentActivityType.MESSAGE_RECEIVED: "收到消息",
    AgentActivityType.TASK_RECEIVED: "收到任务提醒",
}


@dataclass
class AgentActivityMeta:
    """活动记录 metadata 的类型化封装。

    提供多种填充方法，避免手工拼 dict。序列化时自动排除 None 值。
    """
    # 发起本次 turn 的任务房间 ID（agent 被调度执行所在的房间），
    # 与 send_chat_msg 等工具的目标房间无关。
    task_room_id: int | None = None
    model: str | None = None
    estimated_prompt_tokens: int | None = None
    # 流式进度
    current_completion_tokens: int | None = None
    current_total_tokens: int | None = None
    # 最终 usage
    final_prompt_tokens: int | None = None
    final_completion_tokens: int | None = None
    final_total_tokens: int | None = None
    # COMPACT 专用：压缩前后的上下文估算（与 LLM usage 口径不同，单独存放）
    context_tokens_before: int | None = None
    context_tokens_after: int | None = None
    # 溢出重试
    overflow_retry: bool | None = None
    error_kind: str | None = None
    # 请求重试状态
    request_state: str | None = None
    retry_attempt: int | None = None
    retry_max_attempts: int | None = None
    retry_delay_seconds: int | None = None
    last_retry_error: str | None = None
    # 工具调用
    tool_name: str | None = None
    tool_arguments: Any = None
    tool_call_id: str | None = None
    command: str | None = None
    tool_result: Any = None
    # 收到消息
    messages: list[dict] | None = None  # MESSAGE_RECEIVED: [{"sender": str, "content": str}, ...]
    # 执行环境（§8）：开始时写入，结束时 patch 为最终环境
    exec_env: str | None = None
    current_container_id: str | None = None
    container_name: str | None = None

    def apply_progress(self, progress) -> None:
        """从 InferStreamProgress 更新流式进度字段。"""
        self.current_completion_tokens = progress.current_completion_tokens
        self.current_total_tokens = progress.current_total_tokens

    def apply_usage(self, usage) -> None:
        """从 LLM usage 对象更新最终 token 用量。"""
        if usage is not None:
            self.final_prompt_tokens = usage.prompt_tokens
            self.final_completion_tokens = usage.completion_tokens
            self.final_total_tokens = usage.total_tokens

    def apply_request_status_event(self, event) -> None:
        """从 InferRequestStatusEvent 更新请求重试状态字段。"""
        self.request_state = event.state.name
        self.retry_attempt = event.attempt
        self.retry_max_attempts = event.max_attempts
        self.last_retry_error = event.error_message
        if event.state == InferRequestStateType.RETRY_SCHEDULED:
            self.retry_delay_seconds = event.retry_delay_seconds
        elif event.state == InferRequestStateType.RETRYING:
            self.retry_delay_seconds = 0

    def merge_exec_env(self, state: ExecEnvState | None) -> "AgentActivityMeta":
        """把驱动当前执行环境合并进 metadata，返回自身以便链式调用。

        `None` 表示调用方没有可用的环境快照，保持三个字段为空。
        """
        if state is None:
            return self
        self.exec_env = state.exec_env.value
        self.current_container_id = state.current_container_id
        self.container_name = state.container_name
        return self

    def to_dict(self) -> dict:
        """序列化为 dict，排除值为 None 的字段。"""
        return {f.name: getattr(self, f.name) for f in fields(self) if getattr(self, f.name) is not None}


def _broadcast(activity: GtAgentActivity) -> None:
    """向 messageBus 广播活动记录变更。"""
    messageBus.publish(
        MessageBusTopic.AGENT_ACTIVITY_CHANGED,
        activity=activity,
    )


async def add_activity(
    *,
    gt_agent: GtAgent,
    activity_type: AgentActivityType,
    status: AgentActivityStatus = AgentActivityStatus.STARTED,
    title: str | None = None,
    detail: str = "",
    error_message: str | None = None,
    metadata: AgentActivityMeta | None = None,
) -> GtAgentActivity:
    """创建一条活动记录。

    title 可省略，默认按 activity_type 取内置标题。
    结束态自动补 finished_at，duration_ms 始终为 None（仅由 update 计算）。
    """
    resolved_title = title if title is not None else _DEFAULT_TITLES.get(activity_type, activity_type.value)
    now = datetime.now()
    finished_at = now if status in _TERMINAL_STATUSES else None

    item = GtAgentActivity(
        agent_id=gt_agent.id,
        team_id=gt_agent.team_id,
        activity_type=activity_type,
        status=status,
        title=resolved_title,
        detail=detail,
        error_message=error_message,
        started_at=now,
        finished_at=finished_at,
        duration_ms=None,
        metadata=metadata.to_dict() if metadata is not None else {},
    )
    activity = await gtAgentActivityManager.create_activity(item)
    _broadcast(activity)
    return activity


async def update_activity_progress(
    activity_id: int,
    *,
    status: AgentActivityStatus | None = None,
    detail: str | None = None,
    error_message: str | None = None,
    metadata_patch: AgentActivityMeta | None = None,
    persist: bool = True,
) -> GtAgentActivity:
    """更新活动记录进度。

    - 不传 status 时，仅做进度刷新（如流式 token 更新）
    - 传入结束态时，自动补 finished_at 与 duration_ms
    - metadata_patch 采用浅合并（读取 → merge → 写回），但**执行环境三元组是整体快照**：
      patch 带 `exec_env` 时会先清掉旧的 `current_container_id` / `container_name`，
      见下面注释（空值 vs 错值）。
    - persist=False 时只更新内存对象并广播，不写数据库。用于流式 token 这类高频、
      纯观测的进度刷新：前端靠广播实时刷新即可，最终状态仍由收尾更新落库。
    - 调用约定：调用处统一写成单行，避免在业务代码里拆成多行
    """
    update_fields: dict = {}
    current: GtAgentActivity | None = None

    if detail is not None:
        update_fields["detail"] = detail
    if error_message is not None:
        update_fields["error_message"] = error_message

    # metadata 浅合并
    if metadata_patch is not None:
        patch_dict = metadata_patch.to_dict()
        if patch_dict:
            current = await GtAgentActivity.aio_get_or_none(GtAgentActivity.id == activity_id)
            if current is not None:
                merged = dict(current.metadata or {})
                # 执行环境三元组（`exec_env` + 两个容器键）是**整体快照**，不能逐键浅合并。
                # `to_dict()` 会剔除 None 字段，所以「合并到宿主」或「合并到拿不到名字的外部
                # 容器」时 patch 里根本没有那两个容器键，`update()` 碰不到旧键，旧值就留下了。
                # 最坏情形不是残留空值而是**错值**：容器 A → 外部容器 B 时 `exec_env` 仍是
                # `container`，前端会把 A 的旧名字显示成 B 的名字。所以这里先清后合。
                # 不在 `merge_exec_env` 里塞 None：`to_dict()` 照样会剔掉；塞空串更糟
                # （JSON 里像一个合法的容器名）。清除只能在合并侧表达。
                if "exec_env" in patch_dict:
                    merged.pop("current_container_id", None)
                    merged.pop("container_name", None)
                merged.update(patch_dict)
                update_fields["metadata"] = merged

    # 结束态自动补时间与耗时
    if status is not None:
        update_fields["status"] = status
        if status in _TERMINAL_STATUSES:
            now = datetime.now()
            update_fields["finished_at"] = now
            if current is None:
                current = await GtAgentActivity.aio_get_or_none(GtAgentActivity.id == activity_id)
            if current is not None:
                delta = now - current.started_at
                update_fields["duration_ms"] = int(delta.total_seconds() * 1000)

    if not persist:
        # 不写库：把本次更新应用到读出的行上，仅用于广播。行不存在与写库路径同样报错。
        if current is None:
            current = await GtAgentActivity.aio_get_or_none(GtAgentActivity.id == activity_id)
        if current is None:
            raise RuntimeError(f"update_activity_progress failed: activity_id={activity_id}")
        for field_name, value in update_fields.items():
            setattr(current, field_name, value)
        _broadcast(current)
        return current

    activity = await gtAgentActivityManager.update_activity_by_id(activity_id, **update_fields)
    _broadcast(activity)
    return activity


async def fail_started_activities(agent_id: int, error_message: str = "cancelled by user") -> list[GtAgentActivity]:
    """将某个 Agent 当前仍处于 STARTED 的活动统一标记为 FAILED。"""
    started_activities = await gtAgentActivityManager.list_agent_activities_by_status(agent_id, AgentActivityStatus.STARTED)
    updated_activities: list[GtAgentActivity] = []
    for activity in started_activities:
        updated_activities.append(await update_activity_progress(activity.id, status=AgentActivityStatus.FAILED, error_message=error_message))
    return updated_activities

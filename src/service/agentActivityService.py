"""agentActivityService: 活动记录的创建、更新与广播。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, fields
from datetime import datetime
from typing import Any

from constants import AgentActivityStatus, AgentActivityType, MessageBusTopic
from dal.db import gtAgentActivityManager
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
}


@dataclass
class AgentActivityMeta:
    """活动记录 metadata 的类型化封装。

    提供多种填充方法，避免手工拼 dict。序列化时自动排除 None 值。
    """
    room_id: int | None = None
    model: str | None = None
    estimated_prompt_tokens: int | None = None
    # 流式进度
    current_completion_tokens: int | None = None
    current_total_tokens: int | None = None
    # 最终 usage
    final_prompt_tokens: int | None = None
    final_completion_tokens: int | None = None
    final_total_tokens: int | None = None
    # 溢出重试
    overflow_retry: bool | None = None
    error_kind: str | None = None
    # 工具调用
    tool_name: str | None = None
    tool_arguments: Any = None
    tool_call_id: str | None = None
    command: str | None = None
    tool_result: Any = None

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
) -> GtAgentActivity:
    """更新活动记录进度。

    - 不传 status 时，仅做进度刷新（如流式 token 更新）
    - 传入结束态时，自动补 finished_at 与 duration_ms
    - metadata_patch 采用浅合并（读取 → merge → 写回）
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

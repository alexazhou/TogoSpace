"""协作任务管理服务。

负责任务的创建、状态流转、权限校验和查询逻辑。
"""

from __future__ import annotations

import logging
from typing import Optional

from constants import (
    EVENT_CONTENT_MAX_CHARS,
    EVENT_TRUNCATED_SUFFIX,
    SpecialAgent,
    TaskPriority,
    TaskStatus,
)
from dal.db import gtAgentManager, gtAgentTaskManager, gtAgentTaskEventManager
from model.dbModel.base import atomic
from model.dbModel.gtAgentTask import GtAgentTask

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 合法状态流转表
# ---------------------------------------------------------------------------

_VALID_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.TODO:        {TaskStatus.IN_PROGRESS, TaskStatus.PENDING, TaskStatus.ON_HOLD, TaskStatus.DONE, TaskStatus.CANCELLED},
    TaskStatus.PENDING:     {TaskStatus.TODO, TaskStatus.IN_PROGRESS, TaskStatus.ON_HOLD, TaskStatus.CANCELLED},
    TaskStatus.IN_PROGRESS: {TaskStatus.REVIEWING, TaskStatus.DONE, TaskStatus.ON_HOLD, TaskStatus.CANCELLED},
    TaskStatus.REVIEWING:   {TaskStatus.DONE, TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED},
    TaskStatus.ON_HOLD:     {TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED},
    TaskStatus.DONE:        set(),
    TaskStatus.CANCELLED:   set(),
}


def is_task_reviewer(task: GtAgentTask, caller_id: int) -> bool:
    """单一口径判定：caller 是否为该任务的验收人。

    定义 = `reviewer_id` 非空 且 `caller == reviewer_id`。
    **不再有「creator 隐式接管验收人」的兜底**。
    """
    return task.reviewer_id is not None and caller_id == task.reviewer_id


# 事件时间线摘要默认条数
EVENT_TIMELINE_LIMIT = 20


def _truncate_event_content(content):
    """按共享口径截断**单条**事件内容（`EVENT_CONTENT_MAX_CHARS` + `EVENT_TRUNCATED_SUFFIX`）。

    所有「面向 Agent 展示事件内容」的路径（`get_task` / `list_task_events`，
    以及由事件派生的 `last_note` / `current_block_reason`）统一走这里；
    唯一例外是 `get_task_event`（按事件 id 取单条，返回完整原文）。
    非字符串 / 缺省内容原样返回，便于兼容 `model_to_dict` 缺 `content` 键的场景。
    """
    if not isinstance(content, str):
        return content
    if len(content) <= EVENT_CONTENT_MAX_CHARS:
        return content
    return content[:EVENT_CONTENT_MAX_CHARS] + EVENT_TRUNCATED_SUFFIX


def last_note(events: list, *, truncate_content: bool = True) -> str:
    """「最近一次说明」= 最后一条 `to_status` 非空且 `content` 非空的流转/快照事件的 content。

    `events` 为按 id 降序（最新在前）的事件列表；评论事件（`to_status=NULL`）不计入。
    含 0016 历史快照。无则返回空串。
    `truncate_content=True`（默认）时按共享口径截断，口径与事件列表一致。
    """
    for event in events:
        if event.to_status is not None and (event.content or '') != '':
            return _truncate_event_content(event.content) if truncate_content else event.content
    return ''


def current_block_reason(
    task: GtAgentTask, events: list, *, truncate_content: bool = True
) -> Optional[str]:
    """「当前搁置原因」（PIN-4）= 仅当任务状态 ∈ ON_HOLD 时，
    取最后一条 `to_status=ON_HOLD` 流转事件的 content；否则为 None。
    `truncate_content=True`（默认）时按共享口径截断，口径与事件列表一致。
    """
    if task.status != TaskStatus.ON_HOLD:
        return None
    for event in events:
        if event.to_status == TaskStatus.ON_HOLD:
            return _truncate_event_content(event.content) if truncate_content else event.content
    return None


def _serialize_event(event, *, truncate_content: bool = True) -> dict:
    """把事件对象转为 JSON 友好的 dict（状态枚举 → 字符串）。

    `truncate_content=True`（默认）时按共享口径截断 `content`
    （面向 Agent 展示的摘要口径）；`get_task_event` 是唯一例外，
    显式传 `truncate_content=False` 以返回完整原文。
    """
    from playhouse.shortcuts import model_to_dict

    data = model_to_dict(event, recurse=False)
    if truncate_content and isinstance(data.get("content"), str):
        data["content"] = _truncate_event_content(data["content"])
    from_status = getattr(event, "from_status", None)
    to_status = getattr(event, "to_status", None)
    data["from_status"] = from_status.value if isinstance(from_status, TaskStatus) else from_status
    data["to_status"] = to_status.value if isinstance(to_status, TaskStatus) else to_status
    return data


def _publish_event_appended(team_id: int, task: GtAgentTask, event) -> None:
    """任务事件写入后统一推送（**service 层唯一出口**；dal 不碰 messageBus）。

    必须在 `atomic()` 事务块**之外**调用：回滚时任务与事件一起消失，
    不能出现「回滚了还推了一条」。

    `content` 不截断——100 字符是面向 Agent 的摘要口径，UI 时间线需要完整原文。
    """
    from service import messageBus as _mb
    from constants import MessageBusTopic as _MBT

    _mb.publish(
        _MBT.TASK_EVENT_APPENDED,
        team_id=team_id,
        task=task,
        task_event=_serialize_event(event, truncate_content=False),
    )


async def _transition_with_event(
    team_id: int,
    task: GtAgentTask,
    *,
    actor_id: int,
    from_status: TaskStatus,
    to_status: TaskStatus,
    content: str = '',
) -> GtAgentTask:
    """流转任务的唯一写入口：**同一事务**内更新主表状态 + 追加一条流转事件，事务后再推送。

    - 事件由 service 构造（actor / from / to / content 都是业务判断），DAL 只负责保存；
    - 任一写入失败即整体回滚，保证「每次成功流转必且仅有一条流转事件」（E1）；
    - 推送放在事务块外，回滚时不会出现「推了但没落库」。
    """
    async with atomic():
        task.status = to_status  # 同步内存态，保证返回体与落库一致
        await gtAgentTaskManager.save_task(task, only=[GtAgentTask.status])
        event = await gtAgentTaskEventManager.append_event(
            task.id,
            actor_id,
            from_status=from_status,
            to_status=to_status,
            content=content,
        )
    _publish_event_appended(team_id, task, event)
    return task


async def auto_progress_dependents(team_id: int, completed_task_id: int) -> list[int]:
    """依赖满足自动推进：把「所有前置依赖均已 DONE」的 `PENDING` 下游任务置为 `TODO`。

    - 系统操作：对每个候选写一条流转事件（`actor_id = SpecialAgent.SYSTEM`，**代码引用常量、勿硬写 -2**），
      `from_status = PENDING`、`to_status = TODO`，`content` 记说明文本；
    - **不触发任何唤醒 / 通知**（不进调度表、不唤醒 Agent）；仅推送 `TASK_EVENT_APPENDED`
      让前端时间线实时可见；
    - 返回被推进的任务 id 列表（无候选则空）。
    """
    dependents = await gtAgentTaskManager.list_pending_dependents(team_id, completed_task_id)
    progressed: list[int] = []
    for dep in dependents:
        upstream = await gtAgentTaskManager.get_tasks_by_ids(dep.depends_on or [])
        if all(t.status == TaskStatus.DONE for t in upstream):
            await _transition_with_event(
                team_id,
                dep,
                actor_id=SpecialAgent.SYSTEM.value,
                from_status=TaskStatus.PENDING,
                to_status=TaskStatus.TODO,
                content="前置依赖已全部完成，系统自动推进为 TODO",
            )
            progressed.append(dep.id)
            logger.info(f"依赖满足自动推进：task_id={dep.id} PENDING → TODO（trigger={completed_task_id}）")
    return progressed


async def claim_task(caller_id: int, task_id: int) -> dict:
    """认领置位（D5=(a)）：执行人开启执行回合时，系统**在同一事务**内把 `TODO → IN_PROGRESS`。

    - 仅当 `status == TODO` 且 `caller_id == assignee_id` 时置位；否则幂等跳过（返回 `action="skipped"`）；
    - 写一条系统流转事件（`actor_id = SpecialAgent.SYSTEM`），`from_status = TODO`、`to_status = IN_PROGRESS`；
    - **不触发唤醒 / 通知**；仅推送 `TASK_EVENT_APPENDED`。
    """
    task = await gtAgentTaskManager.get_task(task_id)
    if task is None or task.status != TaskStatus.TODO or task.assignee_id != caller_id:
        return {"success": True, "action": "skipped"}

    await _transition_with_event(
        task.team_id,
        task,
        actor_id=SpecialAgent.SYSTEM.value,
        from_status=TaskStatus.TODO,
        to_status=TaskStatus.IN_PROGRESS,
        content="执行人认领，系统自动置为 IN_PROGRESS",
    )
    logger.info(f"认领置位：task_id={task_id} TODO → IN_PROGRESS（assignee={caller_id}）")
    return {"success": True, "action": "claimed"}


# ---------------------------------------------------------------------------
# 公开接口
# ---------------------------------------------------------------------------

async def create_task(
    team_id: int,
    creator_id: int,
    title: str,
    assignee_id: int,
    description: str = '',
    reviewer_id: Optional[int] = None,
    priority: str = 'NORMAL',
    parent_id: Optional[int] = None,
    depends_on: Optional[list[int]] = None,
    room_id: Optional[int] = None,
) -> dict:
    """创建协作任务。

    Returns:
        dict with keys: success, task_id (on success) or message (on failure)
    """
    priority_enum = TaskPriority.value_of(priority.upper())
    if priority_enum is None:
        return {"success": False, "message": f"无效的优先级：{priority}，有效值为 HIGH / NORMAL / LOW"}

    # 执行人与验收人不能是同一人
    if reviewer_id is not None and assignee_id == reviewer_id:
        return {"success": False, "message": "验收人与执行人是同一人时无需设置验收人，请去掉 reviewer_id 参数", "error_code": "invalid_reviewer"}

    # 验证 assignee 权限
    if assignee_id != creator_id:
        from service import deptService
        subordinates = await deptService.get_sub_agent_ids(team_id, creator_id)
        if assignee_id not in subordinates:
            return {
                "success": False,
                "message": f"无权将任务分配给 agent_id={assignee_id}，只能分配给自己或直接/间接下属",
                "error_code": "assignee_not_allowed",
            }

    # 验证并解析依赖
    dep_ids: list[int] = list(depends_on or [])
    initial_status = TaskStatus.TODO
    if dep_ids:
        dep_tasks = await gtAgentTaskManager.get_tasks_by_ids(dep_ids)
        found_ids = {t.id for t in dep_tasks}
        missing = [d for d in dep_ids if d not in found_ids]
        if missing:
            return {"success": False, "message": f"依赖任务不存在：{missing}"}
        wrong_team = [t.id for t in dep_tasks if t.team_id != team_id]
        if wrong_team:
            return {"success": False, "message": f"依赖任务不属于当前团队：{wrong_team}"}
        if any(t.status != TaskStatus.DONE for t in dep_tasks):
            initial_status = TaskStatus.PENDING

    task = GtAgentTask(
        team_id=team_id,
        title=title,
        description=description,
        assignee_id=assignee_id,
        creator_id=creator_id,
        reviewer_id=reviewer_id,
        status=initial_status,
        priority=priority_enum,
        parent_id=parent_id,
        depends_on=dep_ids,
        room_id=room_id,
    )
    async with atomic():
        await gtAgentTaskManager.save_task(task, force_insert=True)
        event = await gtAgentTaskEventManager.append_event(
            task.id,
            creator_id,
            from_status=None,
            to_status=initial_status,
            content='',
        )
    logger.info(f"任务创建：task_id={task.id}, title={title!r}, assignee={assignee_id}, status={initial_status.value}")

    # 广播任务创建事件（列表刷新）+ 事件追加（时间线实时）
    from service import messageBus as _mb
    from constants import MessageBusTopic as _MBT
    _mb.publish(_MBT.TASK_CREATED, task=task)
    _publish_event_appended(team_id, task, event)

    return {"success": True, "task_id": task.id, "message": f"任务已创建，task_id={task.id}，状态={initial_status.value}"}


async def update_task(
    team_id: int,
    caller_id: int,
    task_id: int,
    new_status: Optional[str] = None,
    note: str = '',
) -> dict:
    """更新协作任务：流转状态（`new_status`）或追加评论（省略 `new_status`）。

    - `new_status` 省略、或等于当前状态 ⇒ **评论事件**（`from_status`/`to_status` 均 NULL，PIN-1）；
    - `new_status` 给定且 ≠ 当前状态 ⇒ 权限校验 + **流转事件**（`from≠to`、`content=note`）；
    - **评论权限 = 团队内成员皆可**（不限 assignee / reviewer / creator）；
    - **终态（`DONE`/`CANCELLED`）不可流转；仅允许追加评论**。

    Returns:
        dict: success / action（`transitioned` | `commented`）/ task；失败时 message（+ error_code）。
    """
    # 先校验状态字符串（非法状态不应触达 DB）
    target: Optional[TaskStatus] = None
    if new_status is not None:
        target = TaskStatus.value_of(str(new_status).upper())
        if target is None:
            return {"success": False, "message": f"无效的状态：{new_status}"}

    task = await gtAgentTaskManager.get_task(task_id)
    if task is None:
        return {"success": False, "message": f"任务不存在：task_id={task_id}"}
    if task.team_id != team_id:
        return {"success": False, "message": "任务不属于当前团队"}

    old_status = task.status

    # ---- 评论分支：省略 new_status，或 new_status == 当前状态（自环归评论，不建自环边）----
    if target is None or target == old_status:
        if not (note or '').strip():
            return {
                "success": False,
                "message": "追加评论时必须填写 note（评论内容不能为空）",
                "error_code": "empty_note",
            }
        event = await gtAgentTaskEventManager.append_event(
            task_id,
            caller_id,
            from_status=None,
            to_status=None,
            content=note,
        )
        logger.info(f"任务评论：task_id={task_id}, caller={caller_id}")
        # 评论不改主表，但要推送让已打开的时间线即时可见
        _publish_event_appended(team_id, task, event)

        from playhouse.shortcuts import model_to_dict
        return {
            "success": True,
            "action": "commented",
            "task": model_to_dict(task, recurse=False),
            "comment": _serialize_event(event),
            "message": "已追加评论（任务状态未变更）",
        }

    # ---- 流转分支：target 非空且 ≠ 当前状态 ----
    # 终态（DONE/CANCELLED）不可流转（评论分支已在上方处理）
    if old_status in (TaskStatus.DONE, TaskStatus.CANCELLED):
        return {
            "success": False,
            "message": f"任务已处于终态（{old_status.value}），不可再变更状态（仅可追加评论）",
            "error_code": "invalid_transition",
        }

    is_assignee = (caller_id == task.assignee_id)
    is_reviewer = is_task_reviewer(task, caller_id)
    is_creator  = (caller_id == task.creator_id)

    # 权限校验（按顺序命中、先匹配先裁定）
    if target == TaskStatus.REVIEWING:
        if not is_assignee:
            return {"success": False, "message": "只有执行人（assignee）才能提交验收", "error_code": "permission_denied"}
    elif target == TaskStatus.DONE and old_status == TaskStatus.REVIEWING:
        if not is_reviewer:
            return {"success": False, "message": "只有验收人（reviewer）才能通过验收", "error_code": "permission_denied"}
    elif target == TaskStatus.IN_PROGRESS and old_status == TaskStatus.REVIEWING:
        if not is_reviewer:
            return {"success": False, "message": "只有验收人（reviewer）才能打回任务", "error_code": "permission_denied"}
    elif target == TaskStatus.CANCELLED:
        if not is_reviewer and not is_creator:
            return {"success": False, "message": "只有验收人或创建人才能取消任务", "error_code": "permission_denied"}
    else:
        if not is_assignee:
            return {"success": False, "message": "只有执行人（assignee）才能更新任务状态", "error_code": "permission_denied"}

    # 状态流转合法性（终态 DONE/CANCELLED 出边为空 ⇒ 任何流转在此被拒）
    if target not in _VALID_TRANSITIONS.get(old_status, set()):
        return {
            "success": False,
            "message": f"不允许的状态流转：{old_status.value} → {target.value}",
            "error_code": "invalid_transition",
        }

    # 收紧：IN_PROGRESS → REVIEWING 仅在设有验收人时允许
    if (
        target == TaskStatus.REVIEWING
        and old_status == TaskStatus.IN_PROGRESS
        and task.reviewer_id is None
    ):
        return {
            "success": False,
            "message": "该任务未设验收人，无需提交验收，请直接完成（status=DONE）",
            "error_code": "no_reviewer",
        }

    # 依赖检查
    if old_status == TaskStatus.PENDING and target == TaskStatus.IN_PROGRESS:
        if task.depends_on:
            dep_tasks = await gtAgentTaskManager.get_tasks_by_ids(task.depends_on)
            unfinished = [t.id for t in dep_tasks if t.status != TaskStatus.DONE]
            if unfinished:
                return {
                    "success": False,
                    "message": f"存在未完成的依赖任务，无法开始：{unfinished}",
                    "error_code": "dependency_not_met",
                }

    # 设有验收人时，禁止绕过验收直接 → DONE（仅允许 REVIEWING → DONE）
    if (
        target == TaskStatus.DONE
        and old_status != TaskStatus.REVIEWING
        and task.reviewer_id is not None
    ):
        return {
            "success": False,
            "message": "该任务设有验收人，请先提交验收（status=REVIEWING），由验收人审核后完成",
            "error_code": "review_required",
        }

    # 执行更新：同一事务内更新主表状态 + 追加一条流转事件（content=note），事务后推送
    updated = await _transition_with_event(
        team_id,
        task,
        actor_id=caller_id,
        from_status=old_status,
        to_status=target,
        content=note,
    )
    logger.info(f"任务状态变更：task_id={task_id}, {old_status.value} → {target.value}, caller={caller_id}")

    # 广播任务状态变更事件（列表刷新；时间线由 TASK_EVENT_APPENDED 推送）
    from service import messageBus as _mb
    from constants import MessageBusTopic as _MBT
    _mb.publish(_MBT.TASK_CHANGED, task=updated, old_status=old_status.value)

    # 依赖满足自动推进（事务后触发；系统操作，不唤醒 / 不通知）
    if target == TaskStatus.DONE:
        try:
            await auto_progress_dependents(team_id, task_id)
        except Exception:
            logger.exception(f"依赖满足自动推进失败（不影响本次流转）：task_id={task_id}")

    from playhouse.shortcuts import model_to_dict
    return {
        "success": True,
        "action": "transitioned",
        "task": model_to_dict(updated, recurse=False),
        "message": f"任务状态已更新：{old_status.value} → {target.value}",
    }


async def get_task(
    team_id: int,
    task_id: int,
    event_limit: int = EVENT_TIMELINE_LIMIT,
    *,
    truncate_content: bool = True,
) -> dict:
    """查询单个任务详情，含依赖任务状态摘要与事件时间线摘要。

    `event_limit` 控制事件时间线返回条数（默认 `EVENT_TIMELINE_LIMIT`）；
    回执中的 `events_total`（该任务事件**总数**，跨 `event_limit`）用于判断
    是否还有更早事件——有则可继续用 `list_task_events` 按游标翻页。

    `truncate_content` 默认 True = **面向 Agent 的摘要口径**（超过
    `EVENT_CONTENT_MAX_CHARS` 截断并加省略标记，需要完整原文请用 `get_task_event`）。
    Web 接口应传 False：UI 时间线要完整原文，且与 `TASK_EVENT_APPENDED` 推送内容一致。
    """
    from playhouse.shortcuts import model_to_dict

    task = await gtAgentTaskManager.get_task(task_id)
    if task is None:
        return {"success": False, "message": f"任务不存在：task_id={task_id}"}
    if task.team_id != team_id:
        return {"success": False, "message": "任务不属于当前团队"}

    task_dict = model_to_dict(task, recurse=False)

    depends_on_details: list[dict] = []
    if task.depends_on:
        dep_tasks = await gtAgentTaskManager.get_tasks_by_ids(task.depends_on)
        dep_map = {t.id: t for t in dep_tasks}
        for dep_id in task.depends_on:
            dep = dep_map.get(dep_id)
            if dep:
                depends_on_details.append({"task_id": dep.id, "title": dep.title, "status": dep.status.value})
            else:
                depends_on_details.append({"task_id": dep_id, "title": "(已删除)", "status": "UNKNOWN"})
    task_dict["depends_on_details"] = depends_on_details

    # 挂载事件时间线摘要（按 id 降序）、事件总数与两个派生属性。
    events = await gtAgentTaskEventManager.list_events(task_id, limit=event_limit)
    task_dict["events"] = [
        _serialize_event(e, truncate_content=truncate_content) for e in events
    ]
    task_dict["events_total"] = await gtAgentTaskEventManager.count_events(task_id)
    task_dict["last_note"] = last_note(events, truncate_content=truncate_content)
    task_dict["current_block_reason"] = current_block_reason(
        task, events, truncate_content=truncate_content
    )

    return {"success": True, "task": task_dict}


async def _resolve_actor_name(actor_id: int) -> str:
    """事件 actor 的展示名：系统（`SpecialAgent.SYSTEM`）→「系统」，
    Operator（`SpecialAgent.OPERATOR`）→「Operator」，其余取该 Agent 的 `display_name`。
    """
    if actor_id == SpecialAgent.SYSTEM.value:
        return "系统"
    if actor_id == SpecialAgent.OPERATOR.value:
        return "Operator"
    agent = await gtAgentManager.get_agent_by_id(actor_id)
    return agent.display_name if agent is not None else f"Agent#{actor_id}"


async def get_task_event(team_id: int, event_id: int) -> dict:
    """按事件 id 查询单条任务事件（只读）。

    - 事件不存在 ⇒ 失败；
    - 事件所属任务不属于当前团队 ⇒ 一律拒绝（不泄露事件内容）；
    - 成功 ⇒ `event`：`_serialize_event` 全字段 + `actor_name`（actor 展示名）。
    """
    event = await gtAgentTaskEventManager.get_event(event_id)
    if event is None:
        return {"success": False, "message": f"事件不存在：event_id={event_id}"}

    task = await gtAgentTaskManager.get_task(event.task_id)
    if task is None:
        return {"success": False, "message": f"事件不存在：event_id={event_id}"}
    if task.team_id != team_id:
        return {"success": False, "message": "事件不属于当前团队"}

    event_dict = _serialize_event(event, truncate_content=False)
    event_dict["actor_name"] = await _resolve_actor_name(event.actor_id)
    return {"success": True, "event": event_dict}


async def list_task_events(
    team_id: int,
    task_id: int,
    limit: int = 50,
    before_id: Optional[int] = None,
    *,
    truncate_content: bool = True,
) -> dict:
    """读取任务事件流（按 id 游标分页，最新在前；不使用 offset）。

    `truncate_content` 默认 True = 面向 Agent 的摘要口径（超长截断，
    需要完整原文请用 `get_task_event`）；Web 接口应传 False。

    Returns:
        dict: success / events（本页，按 id 降序）/ count（本页条数）/
              total（该任务事件**总数**，跨页）/ ；失败时 message。
    """
    task = await gtAgentTaskManager.get_task(task_id)
    if task is None:
        return {"success": False, "message": f"任务不存在：task_id={task_id}"}
    if task.team_id != team_id:
        return {"success": False, "message": "任务不属于当前团队"}

    events = await gtAgentTaskEventManager.list_events(task_id, limit=limit, before_id=before_id)
    total = await gtAgentTaskEventManager.count_events(task_id)
    return {
        "success": True,
        "events": [
            _serialize_event(e, truncate_content=truncate_content) for e in events
        ],
        "count": len(events),
        "total": total,
    }


async def resolve_visible_agent_ids(team_id: int, caller_id: Optional[int]) -> Optional[list[int]]:
    """算出 `caller_id` 在「管理视角」下可见的 Agent 集合（**唯一权限入口**）。

    返回值语义（三态，调用方必须区分）：
    - `None`：不限范围（Root Leader = 根部门主管，可见全团队）
    - `list[int]` 且非空：可见范围 = 自己 + 所有直接/间接下属（部门主管）
    - `[]`：无管理视角（普通成员，或取不到调用者身份）

    层级信息全部来自 `deptService` 的部门树（不新增配置）：
    - 根部门（`parent_id is None`）的主管是 Root Leader；
    - 其余主管的可查集合 = `{self} ∪ get_sub_agent_ids(...)`（含子孙部门成员）；
    - 团队还没有部门树时退化为「只看自己」——不放开全团队，避免无组织数据时
      意外泄露，也不会误报无权限。
    """
    if caller_id is None:
        return []

    from service import deptService

    tree = await deptService.get_dept_tree(team_id)
    if tree is not None and tree.manager_id == caller_id:
        return None

    subordinates = await deptService.get_sub_agent_ids(team_id, caller_id)
    if not subordinates:
        return [caller_id] if tree is None else []
    return [caller_id, *sorted(subordinates)]


async def list_tasks(
    team_id: int,
    assignee_id: Optional[int] = None,
    reviewer_id: Optional[int] = None,
    status: Optional[str] = None,
    open_only: bool = False,
    limit: int = 20,
    scope: str = 'self',
    caller_id: Optional[int] = None,
) -> dict:
    """查询协作任务列表。

    返回 `tasks`（本页，高优先级优先）、`count`（本页条数）、
    `total`（**符合条件的任务总数**，跨页、不受 `limit` 影响）——
    `total > count` 即说明还有更多任务被 `limit` 截断。

    `scope` 只在**既没给 assignee 也没给 reviewer** 时才起作用：
    - `"self"`（默认）：不加范围过滤（保持工具层 `missing_target` 之前的历史行为，
      前端列表接口依赖它拿全团队数据）；
    - `"team"`：走 `resolve_visible_agent_ids()` 按汇报层级收窄，无管理视角时
      返回 `error_code="scope_not_permitted"`（**不静默降级为自己**，否则调用方
      会误以为拿到了全团队数据）；可见集合用 `assignee ∈ 集合 OR reviewer ∈ 集合`。
    已经传了 assignee/reviewer 时忽略 `scope`，按精确过滤查（常见路径不多绕一回合）。
    """
    from playhouse.shortcuts import model_to_dict

    if scope not in ('self', 'team'):
        return {
            "success": False,
            "message": f"无效的 scope：{scope}，有效值为 self / team",
            "error_code": "invalid_scope",
        }

    status_enum: Optional[TaskStatus] = None
    exclude_statuses: list[TaskStatus] | None = None
    if status is not None:
        status_enum = TaskStatus.value_of(status.upper())
        if status_enum is None:
            return {"success": False, "message": f"无效的状态：{status}"}
    elif open_only:
        exclude_statuses = [TaskStatus.DONE, TaskStatus.CANCELLED]

    visible_agent_ids: Optional[list[int]] = None
    if assignee_id is None and reviewer_id is None and scope == 'team':
        visible_agent_ids = await resolve_visible_agent_ids(team_id, caller_id)
        if visible_agent_ids is not None and not visible_agent_ids:
            return {
                "success": False,
                "message": "当前身份没有管理视角，无法用 scope=\"team\" 查看全团队任务；请改为指定 assignee 或 reviewer",
                "error_code": "scope_not_permitted",
            }

    # `list` 与 `count` 必须共用同一份过滤条件（`total` 与列表口径一致）
    filters: dict = dict(
        team_id=team_id,
        assignee_id=assignee_id,
        reviewer_id=reviewer_id,
        status=status_enum,
        exclude_statuses=exclude_statuses,
        agent_ids=visible_agent_ids,
    )
    tasks = await gtAgentTaskManager.list_tasks(**filters, limit=limit)
    total = await gtAgentTaskManager.count_tasks(**filters)
    return {
        "success": True,
        "tasks": [model_to_dict(t, recurse=False) for t in tasks],
        "count": len(tasks),
        "total": total,
    }

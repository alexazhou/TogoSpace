"""协作任务管理服务。

负责任务的创建、状态流转、权限校验和查询逻辑。
"""

from __future__ import annotations

import asyncio
import logging
from typing import NamedTuple, Optional, Sequence, Union

from constants import (
    ALLOWED_CLEAR_FIELDS,
    EVENT_CONTENT_MAX_CHARS,
    EVENT_TRUNCATED_SUFFIX,
    MAX_TASK_STAGES,
    EmployStatus,
    SpecialAgent,
    StageNodeType,
    StageStatus,
    TaskEventAction,
    TaskPriority,
    TaskStatus,
)
from dal.db import (
    gtAgentManager,
    gtAgentTaskManager,
    gtAgentTaskEventManager,
    gtAgentTaskStageManager,
)
from model.dbModel.base import atomic
from model.dbModel.gtAgentTask import GtAgentTask
from model.dbModel.gtAgentTaskStage import GtAgentTaskStage
import toolResult
from service import deptService

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


def can_cancel(task: GtAgentTask, caller_id: int) -> bool:
    """单一口径判定：caller 是否有权取消该任务（`update_task` 的 `→CANCELLED` 分支）。

    v3 口径（step1 §3.2.4，取代 v2 的「创建人或验收人」）：
    - **认领之前**（`PENDING` / `TODO`）：仅创建人；
    - **认领之后**（`IN_PROGRESS` / `REVIEWING` / `ON_HOLD`）：仅**当前持棒人**
      （`current_owner_id`）；`current_owner_id` 为空时退化到 `assignee_id`
      （迁移前的行 / 极端脏数据，宁可判给执行人也不判给「谁都能取消」）。
    """
    if task.status in (TaskStatus.PENDING, TaskStatus.TODO):
        return caller_id == task.creator_id
    holder = task.current_owner_id if task.current_owner_id is not None else task.assignee_id
    return caller_id == holder


# ---------------------------------------------------------------------------
# v3 单棒接力：节点链读取与状态映射
# ---------------------------------------------------------------------------

def _resolve_status_for_stage(node_type: StageNodeType) -> TaskStatus:
    """节点类型 → 顶层状态（step2 §4.1，**唯一映射口径**）。

    `EXECUTION → IN_PROGRESS`、`REVIEW → REVIEWING`。
    """
    return TaskStatus.REVIEWING if node_type == StageNodeType.REVIEW else TaskStatus.IN_PROGRESS


def _synth_stages_for(task: GtAgentTask) -> list[dict]:
    """按 v2 字段合成节点链（**只读兜底**）。

    仅用于「节点链未物化」的任务：正常路径由迁移 0022 回填、并且新建任务一律显式
    写入节点，所以走到这里说明是历史遗留/异常行。合成口径与迁移回填完全一致：
    节点 0 = `EXECUTION(assignee_id)`，`reviewer_id` 非空时补节点 1 = `REVIEW(reviewer_id)`。
    """
    chain = [{"stage_index": 0, "node_type": StageNodeType.EXECUTION, "owner_id": task.assignee_id,
              "name": "", "stage_status": StageStatus.ACTIVE}]
    if task.reviewer_id is not None:
        chain.append({"stage_index": 1, "node_type": StageNodeType.REVIEW, "owner_id": task.reviewer_id,
                      "name": "", "stage_status": StageStatus.WAITING})
    return chain


def _stage_field(stage, key: str, default=None):
    """节点字段兼容读取：ORM 对象与合成 dict 统一成同一套访问方式。"""
    if isinstance(stage, dict):
        return stage.get(key, default)
    return getattr(stage, key, default)


async def _load_stages(task: GtAgentTask) -> list:
    """读取任务节点链（按 `stage_index` 升序）；未物化时返回按 v2 字段合成的链。

    返回元素可能是 `GtAgentTaskStage` 或 `dict`（合成兜底），下游一律用
    `_stage_field()` 访问，避免两套分支散落各处。
    """
    stages = await gtAgentTaskStageManager.get_stages_by_task(task.id)
    if stages:
        return list(stages)
    logger.warning(
        "任务节点链未物化，按 v2 字段合成只读链（task_id=%s, status=%s）："
        "正常路径不会出现，若频繁出现请检查迁移 0022 是否执行",
        getattr(task, "id", None), getattr(task, "status", None),
    )
    return _synth_stages_for(task)


def _stages_payload(stages: list, names: dict[int, str] | None = None) -> list[dict]:
    """节点链 → 面向工具/前端的 dict 列表（含 `owner_name`）。"""
    names = names or {}
    payload: list[dict] = []
    for stage in stages:
        node_type = _stage_field(stage, "node_type")
        stage_status = _stage_field(stage, "stage_status")
        owner_id = _stage_field(stage, "owner_id")
        payload.append({
            "stage_index": _stage_field(stage, "stage_index"),
            "node_type": node_type.value if isinstance(node_type, StageNodeType) else node_type,
            "name": _stage_field(stage, "name", "") or "",
            "owner_id": owner_id,
            "owner_name": names.get(owner_id, f"Agent#{owner_id}") if owner_id is not None else None,
            "stage_status": stage_status.value if isinstance(stage_status, StageStatus) else stage_status,
        })
    return payload


def _effective_cursor(task: GtAgentTask, stages: list) -> int:
    """有效游标：主表 `current_stage` 为主，**并用顶层状态纠偏**。

    为什么要纠偏：`current_stage` 是 v3 新增列，迁移会把存量 `REVIEWING` 行回填成
    1，但「直接用 SQL 插入的测试夹具 / 手工修过的行」可能状态已是 `REVIEWING`
    而游标仍是 0。这种行若按游标推进，会在末节点判定上误报
    `non_final_stage_cannot_done`（明明只有 1 个审核节点却说过不去）。
    纠偏只在「状态与当前节点类型明显矛盾」时生效，正常路径一律以游标为准。
    """
    if not stages:
        return 0
    cursor = max(0, min(task.current_stage or 0, len(stages) - 1))
    node_type = _stage_field(stages[cursor], "node_type")
    if task.status == TaskStatus.REVIEWING and node_type != StageNodeType.REVIEW:
        review_indexes = [
            index for index, stage in enumerate(stages)
            if _stage_field(stage, "node_type") == StageNodeType.REVIEW
        ]
        if review_indexes:
            cursor = review_indexes[0]
    return cursor


def _holder_of(task: GtAgentTask, stages: list) -> int:
    """当前持棒人：主表 `current_owner_id` 优先，缺失时取（纠偏后的）当前节点 owner。"""
    if task.current_owner_id is not None:
        return task.current_owner_id
    index = _effective_cursor(task, stages)
    return _stage_field(stages[index], "owner_id")


def _is_holder(task: GtAgentTask, stages: list, caller_id: int) -> bool:
    """「谁拿棒谁说话」：caller 是否为当前持棒人（v2 的 assignee/reviewer 判定被它统一）。"""
    return _holder_of(task, stages) == caller_id


def _review_nodes(stages: list) -> list:
    """链上所有 `REVIEW` 节点（按序号升序）。"""
    return [s for s in stages if _stage_field(s, "node_type") == StageNodeType.REVIEW]


def _normalize_stage_specs(stages) -> tuple[list[dict], dict | None]:
    """把 `stages` 参数归一成 `[{owner_id, node_type, name}]`；不合法时返回错误回执。

    接受两种写法（step1 §5.1）：`{"owner_id": 7, "node_type": "REVIEW", "name": "组长初审"}`
    与简写整数 `7`（等价于 `REVIEW` 节点）。**整数简写默认 REVIEW** 而不是 EXECUTION：
    多级协作的常见目的是「加把关」，默认可删的执行节点等于默认可绕过审核。
    """
    if stages is None:
        return [], None
    if not isinstance(stages, (list, tuple)):
        return [], {
            "success": False,
            "message": f"stages 必须是数组（当前 {type(stages).__name__}），每项为 owner_id 整数或 {{owner_id, node_type?, name?}}",
            "error_code": "invalid_stages",
        }

    normalized: list[dict] = []
    for position, raw in enumerate(stages, start=1):
        if isinstance(raw, bool):
            return [], {
                "success": False,
                "message": f"stages 第 {position} 项非法：布尔值不是 owner_id",
                "error_code": "invalid_stages",
            }
        if isinstance(raw, int):
            normalized.append({"owner_id": raw, "node_type": StageNodeType.REVIEW, "name": ""})
            continue
        if isinstance(raw, dict):
            owner_id = raw.get("owner_id")
            if isinstance(owner_id, bool) or not isinstance(owner_id, int):
                return [], {
                    "success": False,
                    "message": f"stages 第 {position} 项缺少合法的 owner_id（收到 {owner_id!r}）",
                    "error_code": "invalid_stages",
                }
            raw_type = raw.get("node_type")
            node_type = StageNodeType.REVIEW if raw_type is None else StageNodeType.value_of(str(raw_type))
            if node_type is None:
                return [], {
                    "success": False,
                    "message": f"stages 第 {position} 项的 node_type 非法：{raw_type!r}，有效值为 EXECUTION / REVIEW",
                    "error_code": "invalid_stages",
                }
            normalized.append({"owner_id": owner_id, "node_type": node_type, "name": str(raw.get("name") or "")})
            continue
        return [], {
            "success": False,
            "message": f"stages 第 {position} 项类型非法：{type(raw).__name__}，只接受整数或对象",
            "error_code": "invalid_stages",
        }
    return normalized, None


async def _validate_stage_owners(team_id: int, creator_id: int, owner_ids: list[int]) -> dict | None:
    """校验后续节点负责人：本团队有效成员且在职（离职者接管节点 = 制造死锁）。

    创建人自己免查（`get_team_agents_by_ids` 不含调用者缓存时的边界，与
    `_handle_reviewer_change` 的 D4 口径一致）。
    """
    candidates = [oid for oid in dict.fromkeys(owner_ids) if oid != creator_id]
    if not candidates:
        return None
    agents = await gtAgentManager.get_team_agents_by_ids(team_id, candidates)
    found = {a.id: a for a in agents if a.team_id == team_id}
    for owner_id in candidates:
        agent = found.get(owner_id)
        if agent is None:
            return {
                "success": False,
                "message": f"节点负责人必须是本团队成员：agent_id={owner_id} 不存在或不属于当前团队",
                "error_code": "invalid_stage_owner",
            }
        if agent.employ_status == EmployStatus.OFF_BOARD:
            return {
                "success": False,
                "message": f"节点负责人必须是在职成员：agent_id={owner_id} 已离职",
                "error_code": "invalid_stage_owner",
            }
    return None


def _compat_reviewer_for(stages: list, cursor: int) -> int | None:
    """v2 兼容列 `reviewer_id` 的同步口径（step2 §2.3）。

    「当前处于 `REVIEW` 节点 ⇒ 该节点负责人；否则 ⇒ **最后一个** `REVIEW` 节点负责人；
    无审核节点 ⇒ None」。后两条让 v2 的「最终验收人」语义在多级链里仍然成立。
    """
    if cursor < 0 or cursor >= len(stages):
        cursor = max(0, min(cursor, len(stages) - 1)) if stages else 0
    if stages and _stage_field(stages[cursor], "node_type") == StageNodeType.REVIEW:
        return _stage_field(stages[cursor], "owner_id")
    review_owners = [_stage_field(s, "owner_id") for s in _review_nodes(stages)]
    return review_owners[-1] if review_owners else None


# 单任务写操作互斥（step2 §6.1）：按 task_id 的进程内锁，不同任务互不阻塞。
# 锁本身不保证跨进程串行，真正的原子性由「持锁 + 单事务」共同兜底。
_TASK_LOCKS: dict[int, "asyncio.Lock"] = {}


def _task_lock(task_id: int) -> "asyncio.Lock":
    lock = _TASK_LOCKS.get(task_id)
    if lock is None:
        lock = asyncio.Lock()
        _TASK_LOCKS[task_id] = lock
    return lock


def _plan_for_compat_transition(
    task: GtAgentTask, stages: list, old_status: TaskStatus, target: TaskStatus
) -> tuple[dict | None, dict | None]:
    """把 **v2 的状态流转**翻译成 v3 的节点动作（同一事务内一起落库）。

    返回 `(transition_kwargs, error)`。error 非空时不写库，直接回执。
    v2 形状的任务（单节点 / 单审核节点）在这套映射下与旧行为逐字等价：
    `TODO→IN_PROGRESS` 就是认领、`IN_PROGRESS→REVIEWING` 就是交棒给审核节点、
    `REVIEWING→DONE` 就是末节点交棒、`REVIEWING→IN_PROGRESS` 就是打回节点 0。
    """
    cursor = _effective_cursor(task, stages)
    last_index = len(stages) - 1
    owner_now = _stage_field(stages[cursor], "owner_id") if stages else task.assignee_id

    if target == TaskStatus.IN_PROGRESS and old_status in (TaskStatus.PENDING, TaskStatus.TODO):
        # 认领：游标不动，节点 0 置 ACTIVE
        return {
            "action_type": TaskEventAction.CLAIM,
            "from_stage": cursor,
            "to_stage": cursor,
            "stage_updates": {cursor: StageStatus.ACTIVE},
            "current_stage": cursor,
            "current_owner_id": owner_now,
            "reviewer_id": _compat_reviewer_for(stages, cursor),
            "sync_reviewer": True,
        }, None

    if target == TaskStatus.REVIEWING and old_status in (TaskStatus.IN_PROGRESS, TaskStatus.ON_HOLD):
        next_index = cursor + 1
        if next_index > last_index:
            return None, {
                "success": False,
                "message": "当前已是最后一个节点，无下一节点可交棒；末节点请直接完成（status=DONE）",
                "error_code": "non_final_stage_cannot_done",
            }
        next_type = _stage_field(stages[next_index], "node_type")
        if next_type != StageNodeType.REVIEW:
            return None, {
                "success": False,
                "message": (
                    f"下一节点是执行节点（{next_type.value}），交棒后状态为 IN_PROGRESS；"
                    "多节点链路请用 workflow_action=\"advance\" 交棒"
                ),
                "error_code": "invalid_transition",
            }
        return {
            "action_type": TaskEventAction.ADVANCE,
            "from_stage": cursor,
            "to_stage": next_index,
            "stage_updates": {cursor: StageStatus.APPROVED, next_index: StageStatus.ACTIVE},
            "current_stage": next_index,
            "current_owner_id": _stage_field(stages[next_index], "owner_id"),
            "reviewer_id": _compat_reviewer_for(stages, next_index),
            "sync_reviewer": True,
        }, None

    if target == TaskStatus.DONE:
        if cursor < last_index:
            return None, {
                "success": False,
                "message": (
                    f"当前在节点 {cursor}，后面还有 {last_index - cursor} 个节点未走完，"
                    "不能直接置 DONE；请用 workflow_action=\"advance\" 逐级交棒"
                ),
                "error_code": "non_final_stage_cannot_done",
            }
        updates = {index: StageStatus.APPROVED for index in range(len(stages))}
        return {
            "action_type": TaskEventAction.ADVANCE,
            "from_stage": cursor,
            "to_stage": cursor,
            "stage_updates": updates,
            "current_stage": cursor,
            "current_owner_id": _stage_field(stages[cursor], "owner_id") if stages else owner_now,
            "reviewer_id": _compat_reviewer_for(stages, cursor),
            "sync_reviewer": True,
        }, None

    if target == TaskStatus.IN_PROGRESS and old_status == TaskStatus.REVIEWING:
        # v2 的「打回」= v3 的 reject 到节点 0
        return _plan_for_reject(task, stages, cursor, 0, note_ok=True)

    if target == TaskStatus.ON_HOLD:
        return {
            "action_type": TaskEventAction.HOLD,
            "from_stage": cursor,
            "to_stage": cursor,
        }, None

    if target == TaskStatus.IN_PROGRESS and old_status == TaskStatus.ON_HOLD:
        return {
            "action_type": TaskEventAction.RESUME,
            "from_stage": cursor,
            "to_stage": cursor,
        }, None

    if target == TaskStatus.CANCELLED:
        return {
            "action_type": TaskEventAction.CANCEL,
            "from_stage": cursor,
            "to_stage": cursor,
        }, None

    # 其余组合交给上层既有的「合法流转表」判红，这里不产生节点动作
    return {}, None


def _plan_for_reject(
    task: GtAgentTask, stages: list, cursor: int, target_stage: int, *, note_ok: bool
) -> tuple[dict | None, dict | None]:
    """打回计划：目标节点 ACTIVE，区间 `[t+1 .. k]` 全部重置为 WAITING（防跳过重审）。"""
    if cursor <= 0:
        return None, {
            "success": False,
            "message": "当前在节点 0，没有前序节点可打回",
            "error_code": "invalid_reject_target",
        }
    if target_stage < 0 or target_stage >= cursor:
        return None, {
            "success": False,
            "message": f"打回目标非法：target_stage={target_stage}，有效范围 0..{cursor - 1}",
            "error_code": "invalid_reject_target",
        }
    updates: dict[int, StageStatus] = {target_stage: StageStatus.ACTIVE}
    for index in range(target_stage + 1, cursor + 1):
        updates[index] = StageStatus.WAITING
    return {
        "action_type": TaskEventAction.REJECT,
        "from_stage": cursor,
        "to_stage": target_stage,
        "stage_updates": updates,
        "current_stage": target_stage,
        "current_owner_id": _stage_field(stages[target_stage], "owner_id"),
        "reviewer_id": _compat_reviewer_for(stages, target_stage),
        "sync_reviewer": True,
    }, None




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


async def current_block_reason(
    task: GtAgentTask, *, truncate_content: bool = True
) -> Optional[str]:
    """「当前搁置原因」（PIN-4）= 仅当任务状态 ∈ ON_HOLD 时，
    取**最近一条 `to_status=ON_HOLD` 且 content 非空**的流转事件的 content；否则为 None。

    **不复用本次查询返回的 `events` 窗口**（B4）：窗口大小是展示口径，工具层
    `get_task_detail` 默认 `event_limit=3`，搁置后再产生 3 条以上事件就会把原因挤出
    窗口 ⇒ 读成 None，与「从未搁置」不可区分。故这里独立查库（见
    `gtAgentTaskEventManager.get_latest_event_with_content_by_to_status`），
    「当前搁置原因」作为状态派生值不受 limit 影响。

    `content` 非空与 `last_note` 同口径：跳过「没写说明的搁置」（裸 `ON_HOLD('')`
    不能把已有原因清空）。`truncate_content=True`（默认）时按共享口径截断。
    """
    if task.status != TaskStatus.ON_HOLD:
        return None
    event = await gtAgentTaskEventManager.get_latest_event_with_content_by_to_status(
        task.id, TaskStatus.ON_HOLD
    )
    if event is None:
        return None
    content = event.content
    return _truncate_event_content(content) if truncate_content else content


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
    # v3：动作类型同样是枚举，统一转字符串；历史事件为 None（不猜）
    action_type = getattr(event, "action_type", None)
    data["action_type"] = action_type.value if isinstance(action_type, TaskEventAction) else action_type
    for stage_key in ("from_stage", "to_stage"):
        if stage_key not in data:
            data[stage_key] = getattr(event, stage_key, None)
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


def _reviewer_label(agent_id: Optional[int], agent_name: Optional[str]) -> str:
    """验收人的可读标签：`<id>/<名>`；无验收人 ⇒ 「无」；名字查不到只给 ID。"""
    if agent_id is None:
        return "无"
    return f"{agent_id}/{agent_name}" if agent_name else str(agent_id)


async def _resolve_display_names(agent_ids: Sequence[Optional[int]]) -> dict[int, str]:
    """批量解析展示名（一次 `get_agents_by_ids`，带缓存），查不到的 ID 不在返回值里。"""
    ids = [int(i) for i in dict.fromkeys(agent_ids) if i is not None]
    if not ids:
        return {}
    agents = await gtAgentManager.get_agents_by_ids(ids)
    return {agent.id: agent.display_name for agent in agents}


async def _update_reviewer_with_event(
    team_id: int,
    task: GtAgentTask,
    *,
    actor_id: int,
    new_reviewer_id: Optional[int],
    content: str,
) -> GtAgentTask:
    """验收人变更的唯一写入口：**同一事务**内更新主表 `reviewer_id` + 追加一条事件，事务后推送。

    - 事件 `from_status` / `to_status` 均为 NULL（= 评论格），与「评论」靠 `content` 前缀区分；
      不改事件表结构、不加迁移；
    - 任一写入失败即整体回滚：不允许出现「验收人改了但时间线没记录」；
    - 推送放在事务块外（回滚时不会出现「推了但没落库」）。
    """
    stages = await _load_stages(task)
    cursor = _effective_cursor(task, stages)
    review_indexes = [
        index for index, stage in enumerate(stages)
        if _stage_field(stage, "node_type") == StageNodeType.REVIEW
    ]
    save_fields: list = [GtAgentTask.reviewer_id]

    async with atomic():
        task.reviewer_id = new_reviewer_id  # 同步内存态，保证返回体与落库一致
        if new_reviewer_id is None:
            # 撤销（#253 S-1）：节点表里的 REVIEW 节点必须一起移除，否则残留节点会让
            # 执行人后续置 DONE 时被 `non_final_stage_cannot_done` 拦住。
            # 多级任务已在 `_handle_reviewer_change` 入口拒绝，这里只会是单审核节点。
            for index in review_indexes:
                await gtAgentTaskStageManager.delete_stage_and_reindex(task.id, index)
        elif review_indexes:
            # 换人：节点 1 的负责人跟着换；若当前正停在审核节点上，持棒人也一起换
            # （否则新验收人调 DONE/advance/reject 全被拒，待办还挂在旧人名下）。
            target_index = review_indexes[0]
            await gtAgentTaskStageManager.update_stage_owner(task.id, target_index, new_reviewer_id)
            if task.status == TaskStatus.REVIEWING and cursor == target_index:
                task.current_owner_id = new_reviewer_id
                save_fields.append(GtAgentTask.current_owner_id)
        else:
            # 补设：本来没有审核节点 ⇒ 末尾追加一个 REVIEW 节点（否则提交验收过不去）
            await gtAgentTaskStageManager.insert_stages_after(
                task.id,
                len(stages) - 1,
                [{"owner_id": new_reviewer_id, "node_type": StageNodeType.REVIEW, "name": ""}],
            )
        await gtAgentTaskManager.save_task(task, only=save_fields)
        event = await gtAgentTaskEventManager.append_event(
            task.id,
            actor_id,
            from_status=None,
            to_status=None,
            content=content,
        )
    _publish_event_appended(team_id, task, event)
    return task


async def _find_dependency_cycle(
    task_id: int,
    candidate_deps: Sequence[int],
) -> Optional[list[int]]:
    """判断「把 `task_id` 的依赖替换为 `candidate_deps`」后是否成环；成环则返回环路径。

    方向：`task → candidate_deps`。因此从 candidate_deps 出发沿各自 `depends_on`
    继续走，若能回到 `task_id` 即为环。其余任务一律用**库中现值**，所以本函数判的是
    「替换之后」的图，而不是当前图（当前图合法不代表替换后合法）。

    只读查询：整个过程在写库前完成，配合调用方的「校验全通过才写」保证原子性。
    """
    if task_id in candidate_deps:
        return [task_id, task_id]

    visited: set[int] = set()
    frontier: list[tuple[int, list[int]]] = [
        (dep_id, [task_id, dep_id]) for dep_id in candidate_deps
    ]
    while frontier:
        current_id, path = frontier.pop(0)
        if current_id in visited:
            continue
        visited.add(current_id)
        rows = await gtAgentTaskManager.get_tasks_by_ids([current_id])
        if not rows:
            continue
        for next_id in (rows[0].depends_on or []):
            if next_id == task_id:
                return path + [task_id]
            if next_id not in visited:
                frontier.append((next_id, path + [next_id]))
    return None


async def _handle_depends_on_change(
    team_id: int,
    task: GtAgentTask,
    *,
    caller_id: int,
    depends_on: Sequence[int],
    note: str,
) -> dict:
    """`update_task` 的「仅依赖变更」路径。

    **三态语义**（由调用方按 `depends_on is not None` 判定是否进入本函数）：
    - 省略 = 不改（不会走到这里）；
    - `[]` = **清空依赖**；
    - `[1, 2]` = **整体替换**（不是增量追加）。

    权限：**只有任务创建人**能改依赖。执行人不能给自己解绑依赖（否则
    「依赖未满足不能开工」这条闸门可以被执行方自行拆除）；验收人同理。

    校验全部在**写库之前**完成（形态收口 → 存在性/同团队 → 环，自依赖视为「长度 1 的环」），
    任一失败即返回错误，保证「拒绝后依赖列表一字未变」（不留半改）。

    错误码（小马哥 2026-11-17 00:51 命名口径，全部 `task_depends_on_*` 前缀）：
    `task_depends_on_not_creator` / `task_depends_on_invalid_input` /
    `task_depends_on_invalid_target` / `task_depends_on_cycle`（自依赖与直接环、间接环合并）/
    `task_depends_on_terminal`。

    状态联动：
    - 依赖已全部 DONE（或已清空）且任务为 `PENDING` ⇒ `TODO`（与创建时推导一致）；
    - 存在未完成依赖且任务为 `TODO` ⇒ 回退 `PENDING`（TODO 尚未认领，回退无在途代价；
      **这是超出需求字面的一处加固**：若不回退，TODO 任务仍可被 claim 绕过依赖闸门）；
    - 任务已 `IN_PROGRESS` / `REVIEWING` ⇒ **状态不变**（回退会中断在途工作），
      但事件里如实记录「当前存在未完成依赖」。
    """
    old_status = task.status
    reason = (note or '').strip()

    if old_status in (TaskStatus.DONE, TaskStatus.CANCELLED):
        # 正常路径已被 `_update_task_impl` 的终态门拦住；这里是防御性分支（直接调用本函数时）
        return {
            "success": False,
            "message": f"任务已处于终态（{old_status.value}），不可修改依赖",
            "error_code": "task_depends_on_terminal",
        }

    if caller_id != task.creator_id:
        return {
            "success": False,
            "message": (
                f"只有任务创建人才能修改依赖（创建人 agent_id={task.creator_id}）；"
                "执行人/验收人如需调整依赖，请联系创建人"
            ),
            "error_code": "task_depends_on_not_creator",
        }

    # ---- 类型收口（必须在归一循环之前）----
    # 只靠下面的 `int(raw)` 循环收不住三种「形态错但能静默改对/改错」的输入：
    # ① `str`/`bytes` 可迭代 ⇒ `"12"` 被拆成依赖 #1、#2（**静默改错**，最危险）；
    # ② `dict` 可迭代 ⇒ 拿到 keys（`{3: True}` ⇒ 依赖 #3，属「猜对意图」的静默生效）；
    # ③ `bool` 是 `int` 子类 ⇒ `True` 会变成依赖 #1。
    # 口径：只接受 `list`/`tuple`（元素另行数值化），其余一律 `task_depends_on_invalid_input`。
    if not isinstance(depends_on, (list, tuple)):
        return {
            "success": False,
            "message": (
                "depends_on 必须是任务 ID 数组：省略=不改，[]=清空，[1, 2]=整体替换；"
                f"收到的是 {type(depends_on).__name__}"
            ),
            "error_code": "task_depends_on_invalid_input",
        }
    if any(isinstance(raw, bool) for raw in depends_on):
        return {
            "success": False,
            "message": "depends_on 的元素必须是整数任务 ID（布尔值会被误当成 0/1 号任务）",
            "error_code": "task_depends_on_invalid_input",
        }

    # ---- 归一：数值化 + 去重保序（顺序即调用方给的顺序，便于审计）----
    normalized: list[int] = []
    for raw in depends_on:
        try:
            dep_id = int(raw)
        except (TypeError, ValueError):
            return {
                "success": False,
                "message": f"无效的依赖任务 ID：{raw!r}（必须是整数任务 ID）",
                "error_code": "task_depends_on_invalid_input",
            }
        if dep_id not in normalized:
            normalized.append(dep_id)

    # ---- 存在性 + 同团队（get_tasks_by_ids 不按 team 过滤，必须自己校验）----
    if normalized:
        dep_tasks = await gtAgentTaskManager.get_tasks_by_ids(normalized)
        by_id = {t.id: t for t in dep_tasks}
        missing = [d for d in normalized if d not in by_id]
        if missing:
            return {
                "success": False,
                "message": f"依赖任务不存在（可能已删除）：{missing}",
                "error_code": "task_depends_on_invalid_target",
            }
        foreign = [d for d in normalized if by_id[d].team_id != team_id]
        if foreign:
            return {
                "success": False,
                "message": f"依赖任务必须属于同一团队：{foreign}",
                "error_code": "task_depends_on_invalid_target",
            }

    # ---- 环检测（写库前，只读）----
    cycle = await _find_dependency_cycle(task.id, normalized)
    if cycle is not None:
        # 自依赖与直接环/间接环**合并成同一个码**（小马哥 2026-11-17 00:51 命名口径），
        # 具体是哪种由 message 说清：自依赖写 `self`，其余写 `#a -> #b -> #a` 路径。
        if cycle == [task.id, task.id]:
            reason_text = f"self（任务不能依赖自己：#{task.id}）"
        else:
            reason_text = " -> ".join(f"#{x}" for x in cycle)
        return {
            "success": False,
            "message": f"依赖成环，已拒绝：{reason_text}",
            "error_code": "task_depends_on_cycle",
        }

    old_deps = list(task.depends_on or [])
    if old_deps == normalized:
        return {
            "success": True,
            "action": "depends_on_unchanged",
            "old_depends_on": old_deps,
            "new_depends_on": normalized,
            "status": old_status.value,
            "message": "依赖未变化（与当前一致），未写入事件",
        }

    # ---- 状态联动：先算目标状态，再一次性写 ----
    unfinished: list[int] = []
    if normalized:
        dep_tasks = await gtAgentTaskManager.get_tasks_by_ids(normalized)
        unfinished = [t.id for t in dep_tasks if t.status != TaskStatus.DONE]

    new_status_target: Optional[TaskStatus] = None
    if not unfinished and old_status == TaskStatus.PENDING:
        new_status_target = TaskStatus.TODO       # 依赖已就绪 / 已清空 ⇒ 可开工
    elif unfinished and old_status == TaskStatus.TODO:
        new_status_target = TaskStatus.PENDING    # 加固：TODO 未认领，回退依赖未满足

    old_label = "无" if not old_deps else "、".join(f"#{d}" for d in old_deps)
    new_label = "无" if not normalized else "、".join(f"#{d}" for d in normalized)
    content = f"依赖变更：{old_label} → {new_label}"
    if reason:
        content += f"（原由：{reason}）"
    if unfinished and old_status in (TaskStatus.IN_PROGRESS, TaskStatus.REVIEWING):
        content += f"（当前存在未完成依赖：{unfinished}，状态保持 {old_status.value}）"

    save_fields: list = [GtAgentTask.depends_on]
    async with atomic():
        task.depends_on = normalized  # 同步内存态，保证返回体与落库一致
        if new_status_target is not None:
            task.status = new_status_target
            save_fields.append(GtAgentTask.status)
        await gtAgentTaskManager.save_task(task, only=save_fields)
        event = await gtAgentTaskEventManager.append_event(
            task.id,
            caller_id,
            from_status=None,
            to_status=None,
            content=content,
        )
    # 推送放在事务块外：回滚时不会出现「推了但没落库」
    _publish_event_appended(team_id, task, event)
    logger.info(
        f"任务依赖变更：task_id={task.id}, {old_deps} → {normalized}, "
        f"status {old_status.value} → {task.status.value}, caller={caller_id}"
    )

    from playhouse.shortcuts import model_to_dict
    message = f"依赖已更新：{old_label} → {new_label}"
    if new_status_target is not None:
        message += f"；状态联动 {old_status.value} → {task.status.value}"
        if new_status_target == TaskStatus.TODO:
            message += "（依赖已全部完成/已清空）"
        else:
            message += f"（存在未完成依赖 {unfinished}，任务回到待依赖状态）"
    elif unfinished:
        message += f"；存在未完成依赖 {unfinished}，状态保持 {old_status.value} 不变"

    return {
        "success": True,
        "action": "depends_on_updated",
        "old_depends_on": old_deps,
        "new_depends_on": normalized,
        "old_status": old_status.value,
        "status": task.status.value,
        "unfinished_depends_on": unfinished,
        "task": model_to_dict(task, recurse=False),
        "message": message,
    }


async def _handle_reviewer_change(
    team_id: int,
    task: GtAgentTask,
    *,
    caller_id: int,
    target: Optional[TaskStatus],
    reviewer_id: Optional[int],
    clearing: bool,
    note: str,
) -> dict:
    """`update_task` 的「仅验收人变更」路径（D1-D7）。

    与状态流转**互斥**（D5）：同时给了「非自环的 `new_status`」⇒ 直接拒绝，
    否则执行人可以在「提交 REVIEWING」的同一次调用里把验收人换成自己人。

    `clearing` 是**归一后**的撤销意图：调用方按
    `clearing = "reviewer_id" in (clear_fields or ())` 算出，本函数只认这个布尔值，
    D1–D7 的判断逻辑与 #158 一字不差。
    """
    old_status = task.status
    reason = (note or '').strip()

    # D5：与状态流转不能在同一次调用里做（自环 `new_status` 只是评论意图，不透传到此路径）
    if target is not None and target != old_status:
        return {
            "success": False,
            "message": (
                f"修改验收人不能与状态流转（{old_status.value} → {target.value}）在同一次调用里做，"
                "请分两次调用：先改验收人，再流转状态"
            ),
            "error_code": "reviewer_change_must_be_separate",
        }

    # D3：终态一律不可改（挡住「用改验收人绕过终态收口」的口子）
    if old_status in (TaskStatus.DONE, TaskStatus.CANCELLED):
        return {
            "success": False,
            "message": f"任务已处于终态（{old_status.value}），不可修改验收人",
            "error_code": "invalid_transition",
        }

    # D1：权限裁定。执行人**不能**改（否则执行人可自选验收人，验收门失效）——这条优先于
    # 「创建人/验收人可改」，即 creator 与 assignee 同一人时也不放行。
    is_assignee = (caller_id == task.assignee_id)
    is_reviewer = is_task_reviewer(task, caller_id)
    is_creator = (caller_id == task.creator_id)
    if is_assignee:
        return {
            "success": False,
            "message": "执行人（assignee）不能修改验收人，请由创建人或当前验收人操作",
            "error_code": "permission_denied",
        }
    if not (is_creator or is_reviewer):
        return {
            "success": False,
            "message": "只有创建人或当前验收人才能修改验收人",
            "error_code": "permission_denied",
        }

    # D2：撤销（设 NULL）只能由创建人操作，且不能发生在 REVIEWING（否则「无人能通过验收」死锁）
    if clearing:
        if not is_creator:
            return {
                "success": False,
                "message": "撤销验收人只能由创建人操作；当前验收人只能把验收人交接给别人",
                "error_code": "permission_denied",
            }
        if old_status == TaskStatus.REVIEWING:
            return {
                "success": False,
                "message": "任务处于 REVIEWING，撤销验收人会导致无人能通过验收，请改为把验收人交接给别人",
                "error_code": "reviewer_required_in_reviewing",
            }

    # D4：新验收人校验
    if clearing:
        new_reviewer_id: Optional[int] = None
    else:
        try:
            new_reviewer_id = int(reviewer_id)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return {"success": False, "message": f"无效的验收人 ID：{reviewer_id}", "error_code": "invalid_reviewer"}
        if new_reviewer_id == task.assignee_id:
            return {
                "success": False,
                "message": "验收人与执行人是同一人时无需设置验收人，请换一个人",
                "error_code": "invalid_reviewer",
            }
        candidates = await gtAgentManager.get_team_agents_by_ids(team_id, [new_reviewer_id])
        candidate = next((a for a in candidates if a.id == new_reviewer_id), None)
        if candidate is None or candidate.team_id != team_id:
            return {
                "success": False,
                "message": f"验收人必须是本团队成员：agent_id={new_reviewer_id} 不存在或不属于当前团队",
                "error_code": "invalid_reviewer",
            }
        # 离职（OFF_BOARD）成员无法验收，设成验收人等于制造「无人能通过验收」的死锁（同 D2 口径）
        if candidate.employ_status == EmployStatus.OFF_BOARD:
            return {
                "success": False,
                "message": f"验收人必须是在职成员：agent_id={new_reviewer_id} 已离职",
                "error_code": "invalid_reviewer",
            }

    # S-1（#253，文档确认口径 ②）：多级任务不走 v2 的 reviewer_id 简写。
    # 节点数 > 2 或 REVIEW 节点 > 1 时，「唯一审核节点」这个前提不成立：改哪个节点
    # 无法从 v2 入参推断，硬猜等于替用户改判 ⇒ 拒绝并引导到 reassign_stage。
    change_stages = await _load_stages(task)
    if len(change_stages) > 2 or len(_review_nodes(change_stages)) > 1:
        return {
            "success": False,
            "message": (
                f"该任务是 {len(change_stages)} 节点、{len(_review_nodes(change_stages))} 个审核节点的"
                "多级链路，不能用 reviewer_id / clear_fields=[\"reviewer_id\"] 改验收人；"
                "请用 reassign_stage={\"stage_index\": <节点序号>, \"owner_id\": <成员>} 指定具体审核节点"
            ),
            "error_code": "reviewer_change_requires_reassign_stage",
        }

    # D4：与当前验收人相同 ⇒ 无变化，不写无意义事件
    if new_reviewer_id == task.reviewer_id:
        return {
            "success": True,
            "action": "reviewer_unchanged",
            "old_reviewer_id": task.reviewer_id,
            "new_reviewer_id": new_reviewer_id,
            "reviewer_name": None,
            "message": "验收人未变化（与当前一致），未写入事件",
        }

    old_reviewer_id = task.reviewer_id
    names = await _resolve_display_names([old_reviewer_id, new_reviewer_id])
    old_label = _reviewer_label(old_reviewer_id, names.get(old_reviewer_id) if old_reviewer_id else None)
    new_label = _reviewer_label(new_reviewer_id, names.get(new_reviewer_id) if new_reviewer_id else None)

    # D6：事件文案必须以「验收人变更/撤销」开头，让人在时间线上一眼能认出来
    verb = "验收人撤销" if new_reviewer_id is None else "验收人变更"
    content = f"{verb}：{old_label} → {new_label}"
    if reason:
        content += f"（原由：{reason}）"

    updated = await _update_reviewer_with_event(
        team_id,
        task,
        actor_id=caller_id,
        new_reviewer_id=new_reviewer_id,
        content=content,
    )
    logger.info(
        f"任务验收人变更：task_id={task.id}, {old_reviewer_id} → {new_reviewer_id}, caller={caller_id}"
    )

    # D7：事务外推「任务变更」（列表刷新；时间线由 _update_reviewer_with_event 内推 TASK_EVENT_APPENDED）
    from service import messageBus as _mb
    from constants import MessageBusTopic as _MBT
    _mb.publish(_MBT.TASK_CHANGED, task=updated, old_status=old_status.value)

    from playhouse.shortcuts import model_to_dict
    message = f"验收人已{'撤销' if new_reviewer_id is None else '更新'}：{old_label} → {new_label}"
    if new_reviewer_id is None:
        message += "。提醒：撤销验收人后执行人可直接把任务置为 DONE（不再需要验收），请自行承担该后果。"
    else:
        message += "。请自行用 send_chat_msg 通知新的验收人（本工具不会自动唤醒对方）。"
    if target is not None:
        message += "（本次 new_status 与当前状态相同，未写评论事件；note 已作为变更原由记入时间线。）"

    return {
        "success": True,
        "action": "reviewer_updated",
        "old_reviewer_id": old_reviewer_id,
        "new_reviewer_id": new_reviewer_id,
        "reviewer_name": names.get(new_reviewer_id) if new_reviewer_id else None,
        "task": model_to_dict(updated, recurse=False),
        "message": message,
    }


async def _transition_with_event(
    team_id: int,
    task: GtAgentTask,
    *,
    actor_id: int,
    from_status: TaskStatus,
    to_status: TaskStatus,
    content: str = '',
    action_type: TaskEventAction | None = None,
    from_stage: int | None = None,
    to_stage: int | None = None,
    stage_updates: dict[int, StageStatus] | None = None,
    current_stage: int | None = None,
    current_owner_id: int | None = None,
    reviewer_id: int | None = None,
    sync_reviewer: bool = False,
) -> GtAgentTask:
    """流转任务的唯一写入口：**同一事务**内更新主表状态（+ v3 游标/节点局部状态）
    + 追加一条流转事件，事务后再推送。

    - 事件由 service 构造（actor / from / to / content 都是业务判断），DAL 只负责保存；
    - 任一写入失败即整体回滚，保证「每次成功流转必且仅有一条流转事件」（E1）；
    - 推送放在事务块外，回滚时不会出现「推了但没落库」。

    v3 追加参数（都可省略，省略时行为与 v2 逐字一致）：
    - `action_type` / `from_stage` / `to_stage`：事件的动作类型与节点游标；
    - `stage_updates`：`{stage_index: StageStatus}`，同事务内批量改节点局部状态；
    - `current_stage` / `current_owner_id`：同事务内推进主表接力游标；
    - `reviewer_id` + `sync_reviewer`：同事务内同步 v2 兼容列 `reviewer_id`
      （撤销验收人时传 `reviewer_id=None, sync_reviewer=True`）。
    """
    # 列对象集合（peewee 字段在类型上被当成标量，`save_task(only=...)` 运行时按字段用）
    save_fields: list = [GtAgentTask.status]
    async with atomic():
        task.status = to_status  # 同步内存态，保证返回体与落库一致
        if current_stage is not None:
            task.current_stage = current_stage
            save_fields.append(GtAgentTask.current_stage)
        if current_owner_id is not None:
            task.current_owner_id = current_owner_id
            save_fields.append(GtAgentTask.current_owner_id)
        if sync_reviewer:
            task.reviewer_id = reviewer_id
            save_fields.append(GtAgentTask.reviewer_id)
        await gtAgentTaskManager.save_task(task, only=save_fields)
        if stage_updates:
            await gtAgentTaskStageManager.update_stage_statuses(task.id, stage_updates)
        event = await gtAgentTaskEventManager.append_event(
            task.id,
            actor_id,
            from_status=from_status,
            to_status=to_status,
            content=content,
            action_type=action_type,
            from_stage=from_stage,
            to_stage=to_stage,
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
                action_type=TaskEventAction.AUTO_TODO,
                from_stage=dep.current_stage or 0,
                to_stage=dep.current_stage or 0,
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

    cursor = task.current_stage or 0
    await _transition_with_event(
        task.team_id,
        task,
        actor_id=SpecialAgent.SYSTEM.value,
        from_status=TaskStatus.TODO,
        to_status=TaskStatus.IN_PROGRESS,
        content="执行人认领，系统自动置为 IN_PROGRESS",
        action_type=TaskEventAction.CLAIM,
        from_stage=cursor,
        to_stage=cursor,
        stage_updates={cursor: StageStatus.ACTIVE},
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
    stages: Optional[list] = None,
    priority: str = 'NORMAL',
    parent_id: Optional[int] = None,
    depends_on: Optional[list[int]] = None,
    room_id: Optional[int] = None,
    status: Optional[str] = None,
    note: str = '',
) -> dict:
    """创建协作任务。

    `status` 可在创建时显式指定初始状态，只接受 `ON_HOLD` 和 `TODO`：
    - `ON_HOLD`：创建即搁置，优先于依赖推导；
    - `TODO`：与省略等价，由系统按依赖自动判断（无未完成依赖为 TODO，有则为 PENDING）。
    `note` 写入创建事件；创建即搁置时即「当前搁置原因」。

    v3 节点链（step1 §5.1）：
    - 只传 `assignee_id` ⇒ 单节点 `[EXECUTION(assignee)]`，行为与 v2 完全一致；
    - 传 `reviewer_id` ⇒ `[EXECUTION(assignee), REVIEW(reviewer)]`，等价 v2 单审核任务；
    - 传 `stages` ⇒ `[EXECUTION(assignee)] + stages`，每项 `{owner_id, node_type?, name?}`
      或简写为 `owner_id` 整数（默认 `REVIEW`）。**与 `reviewer_id` 互斥**。
    节点 0 恒为 `EXECUTION(assignee_id)`，不接受由 `stages` 重新指定。

    Returns:
        dict with keys: success, task_id (on success) or message (on failure)
    """
    priority_enum = TaskPriority.value_of(priority.upper())
    if priority_enum is None:
        return toolResult.failure(f"无效的优先级：{priority}，有效值为 HIGH / NORMAL / LOW", toolResult.INVALID_TOOL_ARGS)

    if stages is not None and reviewer_id is not None:
        return {
            "success": False,
            "message": "stages 与 reviewer_id 不能同时传：单级审核用 reviewer_id，多级节点链用 stages",
            "error_code": "ambiguous_task_stages",
        }

    # 归一 stage 简写（整数 = REVIEW 节点）并校验节点数上限
    extra_stages, stage_error = _normalize_stage_specs(stages)
    if stage_error is not None:
        return stage_error
    if len(extra_stages) + 1 > MAX_TASK_STAGES:
        return {
            "success": False,
            "message": f"节点数超过上限：最多 {MAX_TASK_STAGES} 个节点（当前 {len(extra_stages) + 1} 个）",
            "error_code": "too_many_stages",
        }

    # 显式初始状态只接受 ON_HOLD / TODO：PENDING 及在途状态由系统推导，不接受显式指定，
    # 否则可以绕过「有未完成依赖时初始状态为 PENDING」的约束。
    explicit_on_hold: bool = False
    if status is not None:
        explicit_status: Optional[TaskStatus] = TaskStatus.value_of(status)
        if explicit_status not in (TaskStatus.ON_HOLD, TaskStatus.TODO):
            return {
                "success": False,
                "message": f"无效的初始状态：{status}，创建任务时只支持 TODO / ON_HOLD（省略则按依赖推导 TODO / PENDING）",
            }
        explicit_on_hold = explicit_status == TaskStatus.ON_HOLD

    # 执行人与验收人不能是同一人
    if reviewer_id is not None and assignee_id == reviewer_id:
        return {"success": False, "message": "验收人与执行人是同一人时无需设置验收人，请去掉 reviewer_id 参数", "error_code": "invalid_reviewer"}

    # 验证 assignee 权限
    if assignee_id != creator_id:
        subordinates = await deptService.get_sub_agent_ids(team_id, creator_id)
        if assignee_id not in subordinates:
            return {
                "success": False,
                "message": f"无权将任务分配给 agent_id={assignee_id}，只能分配给自己或直接/间接下属",
                "error_code": "assignee_not_allowed",
            }

    # v3：后续节点的 owner 必须是本团队有效成员（step2 §5.1）
    owner_error = await _validate_stage_owners(team_id, creator_id, [s["owner_id"] for s in extra_stages])
    if owner_error is not None:
        return owner_error

    # 组装节点链：节点 0 恒为 EXECUTION(assignee)
    chain: list[dict] = [{
        "node_type": StageNodeType.EXECUTION,
        "owner_id": assignee_id,
        "name": "",
        "stage_status": StageStatus.ACTIVE,
    }]
    for spec in extra_stages:
        chain.append({
            "node_type": spec["node_type"],
            "owner_id": spec["owner_id"],
            "name": spec["name"],
            "stage_status": StageStatus.WAITING,
        })
    if reviewer_id is not None:
        chain.append({
            "node_type": StageNodeType.REVIEW,
            "owner_id": reviewer_id,
            "name": "",
            "stage_status": StageStatus.WAITING,
        })

    # v2 兼容列 `reviewer_id` 同步口径：取**最后一个** REVIEW 节点负责人
    # （v2 里 reviewer_id 表示「最终验收人」，与多级场景下的语义最接近）；
    # 无 REVIEW 节点 ⇒ None，此时执行人可以走「无验收人」路径直接 DONE。
    review_owners = [c["owner_id"] for c in chain if c["node_type"] == StageNodeType.REVIEW]
    compat_reviewer_id = review_owners[-1] if review_owners else None

    # 验证并解析依赖
    dep_ids: list[int] = list(depends_on or [])
    initial_status = TaskStatus.ON_HOLD if explicit_on_hold else TaskStatus.TODO
    if dep_ids:
        dep_tasks = await gtAgentTaskManager.get_tasks_by_ids(dep_ids)
        found_ids = {t.id for t in dep_tasks}
        missing = [d for d in dep_ids if d not in found_ids]
        if missing:
            return toolResult.failure(f"依赖任务不存在：{missing}", toolResult.NOT_FOUND)
        wrong_team = [t.id for t in dep_tasks if t.team_id != team_id]
        if wrong_team:
            return toolResult.failure(f"依赖任务不属于当前团队：{wrong_team}", toolResult.PERMISSION_DENIED)
        if not explicit_on_hold and any(t.status != TaskStatus.DONE for t in dep_tasks):
            initial_status = TaskStatus.PENDING

    task = GtAgentTask(
        team_id=team_id,
        title=title,
        description=description,
        assignee_id=assignee_id,
        creator_id=creator_id,
        reviewer_id=compat_reviewer_id,
        status=initial_status,
        priority=priority_enum,
        parent_id=parent_id,
        depends_on=dep_ids,
        room_id=room_id,
        current_stage=0,
        current_owner_id=assignee_id,
    )
    async with atomic():
        await gtAgentTaskManager.save_task(task, force_insert=True)
        await gtAgentTaskStageManager.create_stages(task.id, chain)
        event = await gtAgentTaskEventManager.append_event(
            task.id,
            creator_id,
            from_status=None,
            to_status=initial_status,
            content=note,
            action_type=TaskEventAction.CREATED,
            from_stage=None,
            to_stage=0,
        )
    logger.info(
        f"任务创建：task_id={task.id}, title={title!r}, assignee={assignee_id}, "
        f"status={initial_status.value}, stages={len(chain)}"
    )

    # 广播任务创建事件（列表刷新）+ 事件追加（时间线实时）
    from service import messageBus as _mb
    from constants import MessageBusTopic as _MBT
    _mb.publish(_MBT.TASK_CREATED, task=task)
    _publish_event_appended(team_id, task, event)

    stage_hint = f"，节点链共 {len(chain)} 个节点" if len(chain) > 1 else ""
    return {
        "success": True,
        "task_id": task.id,
        "message": f"任务已创建，task_id={task.id}，状态={initial_status.value}{stage_hint}",
    }


async def _apply_workflow_action(
    team_id: int,
    task: GtAgentTask,
    stages: list,
    *,
    caller_id: int,
    action: str,
    target_stage: Optional[int],
    note: str,
) -> dict:
    """v3 交棒（`advance`）/ 打回（`reject`）：**仅当前持棒人**可执行。

    - `advance`：当前节点 APPROVED；有下一节点则交棒（状态按下一节点类型映射），
      已是末节点则整单 `DONE`（所有节点 APPROVED，并触发下游依赖自动推进）；
    - `reject`：`target_stage` 必须是 `0 .. current_stage - 1`（缺省 0），
      区间 `[t+1 .. k]` 全部重置为 `WAITING`（防跳过重审），`note` 必填。
    """
    from playhouse.shortcuts import model_to_dict

    old_status = task.status
    cursor = _effective_cursor(task, stages)
    last_index = len(stages) - 1

    # S-2（#253，原为待讨论、按建议方案实现）：交棒/打回必须走与 v2 状态流转同一套
    # 状态前置条件。否则 PENDING（依赖未满足）与 ON_HOLD（搁置中）都能绕过闸门直接被
    # 推进到 REVIEWING/DONE —— 依赖机制与搁置语义形同虚设。
    if old_status == TaskStatus.ON_HOLD:
        return {
            "success": False,
            "message": "任务处于搁置（ON_HOLD），请先恢复为 IN_PROGRESS 再交棒/打回",
            "error_code": "task_on_hold",
        }
    if old_status == TaskStatus.PENDING:
        if task.depends_on:
            dep_tasks = await gtAgentTaskManager.get_tasks_by_ids(task.depends_on)
            unfinished = [dep.id for dep in dep_tasks if dep.status != TaskStatus.DONE]
            if unfinished:
                return {
                    "success": False,
                    "message": f"存在未完成的依赖任务，无法交棒/打回：{unfinished}",
                    "error_code": "dependency_not_met",
                }
        # 依赖已就绪但仍是 PENDING：文档给了「可允许推进 / 要求先流转」两选一，
        # 这里取更保守的一支 —— 先流转（认领）再推进，与 v2 `claim` 语义一致。
        return {
            "success": False,
            "message": "任务仍是 PENDING（依赖已就绪但尚未认领），请先流转为 IN_PROGRESS 再交棒",
            "error_code": "task_not_started",
        }

    if not _is_holder(task, stages, caller_id):
        return {
            "success": False,
            "message": f"只有当前持棒人（agent_id={_holder_of(task, stages)}）才能交棒/打回",
            "error_code": "not_current_stage_owner",
        }

    if action == "advance":
        if cursor >= last_index:
            plan = {
                "action_type": TaskEventAction.ADVANCE,
                "from_stage": cursor,
                "to_stage": cursor,
                "stage_updates": {index: StageStatus.APPROVED for index in range(len(stages))},
                "current_stage": cursor,
                "current_owner_id": _stage_field(stages[cursor], "owner_id"),
                "reviewer_id": _compat_reviewer_for(stages, cursor),
                "sync_reviewer": True,
            }
            new_status = TaskStatus.DONE
        else:
            plan = {
                "action_type": TaskEventAction.ADVANCE,
                "from_stage": cursor,
                "to_stage": cursor + 1,
                "stage_updates": {cursor: StageStatus.APPROVED, cursor + 1: StageStatus.ACTIVE},
                "current_stage": cursor + 1,
                "current_owner_id": _stage_field(stages[cursor + 1], "owner_id"),
                "reviewer_id": _compat_reviewer_for(stages, cursor + 1),
                "sync_reviewer": True,
            }
            new_status = _resolve_status_for_stage(_stage_field(stages[cursor + 1], "node_type"))
    elif action == "reject":
        if not (note or '').strip():
            return {
                "success": False,
                "message": "打回（reject）必须填写 note 说明原因，便于被打回的人知道要改什么",
                "error_code": "reject_note_required",
            }
        plan, error = _plan_for_reject(task, stages, cursor, 0 if target_stage is None else target_stage, note_ok=True)
        if error is not None:
            return error
        new_status = _resolve_status_for_stage(_stage_field(stages[plan["to_stage"]], "node_type"))
    else:  # pragma: no cover - 调用方已校验
        return {"success": False, "message": f"不支持的 workflow_action：{action}", "error_code": "invalid_workflow_action"}

    updated = await _transition_with_event(
        team_id,
        task,
        actor_id=caller_id,
        from_status=old_status,
        to_status=new_status,
        content=note,
        **plan,
    )
    logger.info(
        f"任务{'交棒' if action == 'advance' else '打回'}：task_id={task.id}, "
        f"stage {plan['from_stage']} → {plan['to_stage']}, caller={caller_id}"
    )

    from service import messageBus as _mb
    from constants import MessageBusTopic as _MBT
    _mb.publish(_MBT.TASK_CHANGED, task=updated, old_status=old_status.value)

    if new_status == TaskStatus.DONE:
        try:
            await auto_progress_dependents(team_id, task.id)
        except Exception:
            logger.exception(f"依赖满足自动推进失败（不影响本次流转）：task_id={task.id}")

    names = await _resolve_display_names(
        [_stage_field(s, "owner_id") for s in stages]
    )
    if new_status == TaskStatus.DONE:
        message = f"任务已完成（末节点交棒）：{old_status.value} → DONE"
    elif action == "advance":
        message = (
            f"已交棒：节点 {plan['from_stage']} → {plan['to_stage']}，"
            f"当前持棒人 agent_id={plan['current_owner_id']}，状态={new_status.value}"
        )
    else:
        message = (
            f"已打回：节点 {plan['from_stage']} → {plan['to_stage']}，"
            f"当前持棒人 agent_id={plan['current_owner_id']}，状态={new_status.value}"
            "（后续节点已重置为 WAITING，必须逐级重新推进）"
        )
    return {
        "success": True,
        "action": "advanced" if action == "advance" else "rejected",
        "task": model_to_dict(updated, recurse=False),
        "stages": _stages_payload(stages, names),
        "message": message,
    }


async def _apply_stage_update(
    team_id: int,
    task: GtAgentTask,
    stages: list,
    *,
    caller_id: int,
    append_stages: Optional[list],
    remove_stage_index: Optional[int],
    reassign_stage: Optional[dict],
    note: str,
) -> dict:
    """v3 节点链调整：`append_stages` / `remove_stage_index` / `reassign_stage`。

    权限（step1 §3.2.5 + §3.3）：
    - 认领前（`PENDING`/`TODO`）：**创建人**可自由增删改未开始的节点（节点 0 除外）；
    - 认领后：**当前持棒人**可增删**未开始的 `EXECUTION`** 节点；`REVIEW` 节点
      **任何情况都不可删除**（防绕过红线），只允许后置加签与换人。
    """
    from playhouse.shortcuts import model_to_dict

    cursor = _effective_cursor(task, stages)
    last_index = len(stages) - 1
    claimed = task.status not in (TaskStatus.PENDING, TaskStatus.TODO)
    is_creator = caller_id == task.creator_id
    is_holder = _is_holder(task, stages, caller_id)
    if claimed and not is_holder:
        return {
            "success": False,
            "message": f"只有当前持棒人（agent_id={_holder_of(task, stages)}）才能调整节点链",
            "error_code": "not_current_stage_owner",
        }
    if not claimed and not is_creator:
        return {
            "success": False,
            "message": "任务尚未认领，只有创建人可以增删改节点",
            "error_code": "permission_denied",
        }

    if append_stages is not None:
        specs, error = _normalize_stage_specs(append_stages)
        if error is not None:
            return error
        if not specs:
            return {"success": False, "message": "append_stages 不能为空数组", "error_code": "invalid_stages"}
        if len(stages) + len(specs) > MAX_TASK_STAGES:
            return {
                "success": False,
                "message": f"节点数超过上限：最多 {MAX_TASK_STAGES} 个（当前 {len(stages)} + 新增 {len(specs)}）",
                "error_code": "too_many_stages",
            }
        owner_error = await _validate_stage_owners(team_id, task.creator_id, [s["owner_id"] for s in specs])
        if owner_error is not None:
            return owner_error
        # S-3（#253，原为待讨论、按建议方案实现）：多步写库必须在**同一事务**内。
        # gtAgentTaskStageManager 的既有约定是「本模块不开事务，假定调用方已在事务内」，
        # 这三条分支此前都没包 atomic ⇒ 某步失败会留下「节点表已改、主表快照没改、
        # 事件没记」的部分写入。推送仍放在事务块外（回滚时不该有推送）。
        async with atomic():
            new_stages = await gtAgentTaskStageManager.insert_stages_after(task.id, last_index, specs)
            # S-4（#253，确认口径 ①）：后置加签可能新增 REVIEW 节点，v2 兼容列必须同步，
            # 否则执行人按 v2 习惯提交 REVIEWING 会被 `no_reviewer` 误拒。
            task.reviewer_id = _compat_reviewer_for(new_stages, cursor)
            await gtAgentTaskManager.save_task(task, only=[GtAgentTask.reviewer_id])
            action_type = TaskEventAction.STAGE_ADD
            event = await gtAgentTaskEventManager.append_event(
                task.id, caller_id,
                from_status=task.status, to_status=task.status,
                content=note or f"追加 {len(specs)} 个节点",
                action_type=action_type, from_stage=cursor, to_stage=last_index + 1,
            )
        _publish_event_appended(team_id, task, event)
        names = await _resolve_display_names([_stage_field(s, "owner_id") for s in new_stages])
        return {
            "success": True,
            "action": "stage_added",
            "task": model_to_dict(task, recurse=False),
            "stages": _stages_payload(new_stages, names),
            "message": f"已追加 {len(specs)} 个节点，当前共 {len(new_stages)} 个节点",
        }

    if remove_stage_index is not None:
        if not isinstance(remove_stage_index, int) or isinstance(remove_stage_index, bool):
            return {"success": False, "message": "remove_stage_index 必须是整数", "error_code": "invalid_stages"}
        if remove_stage_index < 0 or remove_stage_index > last_index:
            return {
                "success": False,
                "message": f"remove_stage_index 越界：{remove_stage_index}，有效范围 0..{last_index}",
                "error_code": "invalid_stages",
            }
        if remove_stage_index <= cursor:
            return {
                "success": False,
                "message": (
                    f"不能删除已过或当前节点（stage_index={remove_stage_index}，当前游标 {cursor}）；"
                    "执行人换人请用 reassign_stage"
                ),
                "error_code": "cannot_remove_active_or_past_stage",
            }
        target_node_type = _stage_field(stages[remove_stage_index], "node_type")
        # REVIEW 节点红线（step1 §3.2.5）：**认领之后任何人均不可删除**。
        # 认领之前创建人仍可自由编排（step1 §3.3 权限矩阵：「删除 REVIEW 节点」一行
        # 只有「仅认领前允许」这一格是放行的），否则建错流程就永久无法纠正。
        if target_node_type == StageNodeType.REVIEW and claimed:
            return {
                "success": False,
                "message": (
                    f"不允许删除审核节点（stage_index={remove_stage_index}）：任务已认领，"
                    "删除 REVIEW 节点等于绕过审批，这是红线。需要更少审核请换人（reassign_stage），不能删。"
                ),
                "error_code": "cannot_remove_review_stage",
            }
        if not claimed and remove_stage_index == 0:
            return {
                "success": False,
                "message": "节点 0 是任务主执行人，不能整节点删除；要换人请用 reassign_stage",
                "error_code": "cannot_remove_active_or_past_stage",
            }
        async with atomic():
            await gtAgentTaskStageManager.delete_stage_and_reindex(task.id, remove_stage_index)
            remaining = await gtAgentTaskStageManager.get_stages_by_task(task.id)
            reviewer_id = _compat_reviewer_for(remaining, cursor)
            task.reviewer_id = reviewer_id
            await gtAgentTaskManager.save_task(task, only=[GtAgentTask.reviewer_id])
            event = await gtAgentTaskEventManager.append_event(
                task.id, caller_id,
                from_status=task.status, to_status=task.status,
                content=note or f"删除节点 {remove_stage_index}",
                action_type=TaskEventAction.STAGE_REMOVE,
                from_stage=remove_stage_index, to_stage=remove_stage_index,
            )
        _publish_event_appended(team_id, task, event)
        names = await _resolve_display_names([_stage_field(s, "owner_id") for s in remaining])
        return {
            "success": True,
            "action": "stage_removed",
            "task": model_to_dict(task, recurse=False),
            "stages": _stages_payload(remaining, names),
            "message": f"已删除节点 {remove_stage_index}，当前共 {len(remaining)} 个节点",
        }

    # reassign_stage
    if not isinstance(reassign_stage, dict):
        return {"success": False, "message": "reassign_stage 必须是 {stage_index, owner_id} 对象", "error_code": "invalid_stages"}
    raw_index = reassign_stage.get("stage_index")
    raw_owner = reassign_stage.get("owner_id")
    if isinstance(raw_index, bool) or not isinstance(raw_index, int):
        return {"success": False, "message": f"reassign_stage.stage_index 非法：{raw_index!r}", "error_code": "invalid_stages"}
    if isinstance(raw_owner, bool) or not isinstance(raw_owner, int):
        return {"success": False, "message": f"reassign_stage.owner_id 非法：{raw_owner!r}", "error_code": "invalid_stages"}
    if raw_index < 0 or raw_index > last_index:
        return {
            "success": False,
            "message": f"reassign_stage.stage_index 越界：{raw_index}，有效范围 0..{last_index}",
            "error_code": "invalid_stages",
        }
    if raw_index < cursor:
        return {
            "success": False,
            "message": f"不能更换已完成节点的负责人（stage_index={raw_index} 已 APPROVED）",
            "error_code": "cannot_reassign_past_stage",
        }
    owner_error = await _validate_stage_owners(team_id, task.creator_id, [raw_owner])
    if owner_error is not None:
        return owner_error
    old_owner = _stage_field(stages[raw_index], "owner_id")
    async with atomic():
        await gtAgentTaskStageManager.update_stage_owner(task.id, raw_index, raw_owner)
        stages_after = await gtAgentTaskStageManager.get_stages_by_task(task.id)
        if raw_index == cursor:
            task.current_owner_id = raw_owner
            await gtAgentTaskManager.save_task(task, only=[GtAgentTask.current_owner_id])
        reviewer_id = _compat_reviewer_for(stages_after, cursor)
        task.reviewer_id = reviewer_id
        # S-4（#253，确认口径 ②）：节点 0 是任务主执行人，换人必须同步 v2 兼容列
        # `assignee_id`，否则新负责人 claim_task 被拒、按 assignee 的查询仍归属旧人。
        if raw_index == 0:
            task.assignee_id = raw_owner
            await gtAgentTaskManager.save_task(
                task, only=[GtAgentTask.reviewer_id, GtAgentTask.assignee_id]
            )
        else:
            await gtAgentTaskManager.save_task(task, only=[GtAgentTask.reviewer_id])
        event = await gtAgentTaskEventManager.append_event(
            task.id, caller_id,
            from_status=task.status, to_status=task.status,
            content=note or f"节点 {raw_index} 换人：{old_owner} → {raw_owner}",
            action_type=TaskEventAction.STAGE_REASSIGN,
            from_stage=raw_index, to_stage=raw_index,
        )
    _publish_event_appended(team_id, task, event)
    names = await _resolve_display_names([_stage_field(s, "owner_id") for s in stages_after] + [old_owner])
    message = f"节点 {raw_index} 负责人已更换：{old_owner} → {raw_owner}"
    if raw_index == cursor:
        message += "（该节点是当前持棒节点，控制权已同时移交）"
    return {
        "success": True,
        "action": "stage_reassigned",
        "task": model_to_dict(task, recurse=False),
        "stages": _stages_payload(stages_after, names),
        "message": message,
    }


async def update_task(
    team_id: int,
    caller_id: int,
    task_id: int,
    new_status: Optional[str] = None,
    note: str = '',
    reviewer_id: Optional[int] = None,
    clear_fields: Optional[Sequence[str]] = None,
    workflow_action: Optional[str] = None,
    target_stage: Optional[int] = None,
    append_stages: Optional[list] = None,
    remove_stage_index: Optional[int] = None,
    reassign_stage: Optional[dict] = None,
    depends_on: Optional[Sequence[int]] = None,
) -> dict:
    """更新协作任务：流转状态 / 交棒打回 / 调整节点链 / 追加评论 / 修改验收人 / 修改依赖。

    **意图互斥**：`new_status`（状态流转，非自环）、`workflow_action`、
    `append_stages` / `remove_stage_index` / `reassign_stage`、验收人变更、
    依赖变更，这几类意图**只能出现一类**，同时给多个返回 `error_code="ambiguous_task_update"`
    （避免「一次调用里既交棒又换人」这种无法审计的原子动作）。

    `depends_on` 的三态（**调用方必须区分**）：
    - 省略（`None`）= 不改依赖；
    - `[]` = **清空依赖**（显式意图，不是「未提供」）；
    - `[1, 2]` = 整体替换为新依赖列表（不是增量追加）。

    v3 新参数（step1 §5.2）：
    - `workflow_action="advance"`：交棒给下一节点（末节点则整单 DONE）；
    - `workflow_action="reject"` + `target_stage=t`：打回任意前序节点（`note` 必填）；
    - `append_stages=[...]`：后置加签（追加节点）；
    - `remove_stage_index=i`：删除未开始的 `EXECUTION` 节点（`REVIEW` 节点禁删）；
    - `reassign_stage={"stage_index": i, "owner_id": x}`：节点换人。

    单任务写操作按 `task_id` 加进程内互斥锁（step2 §6.1），保证同一任务不会被
    并发双推；锁内再走单事务，跨进程由数据库事务兜底。
    """
    lock = _task_lock(task_id)
    async with lock:
        return await _update_task_impl(
            team_id,
            caller_id,
            task_id,
            new_status=new_status,
            note=note,
            reviewer_id=reviewer_id,
            clear_fields=clear_fields,
            workflow_action=workflow_action,
            target_stage=target_stage,
            append_stages=append_stages,
            remove_stage_index=remove_stage_index,
            reassign_stage=reassign_stage,
            depends_on=depends_on,
        )


async def _update_task_impl(
    team_id: int,
    caller_id: int,
    task_id: int,
    *,
    new_status: Optional[str] = None,
    note: str = '',
    reviewer_id: Optional[int] = None,
    clear_fields: Optional[Sequence[str]] = None,
    workflow_action: Optional[str] = None,
    target_stage: Optional[int] = None,
    append_stages: Optional[list] = None,
    remove_stage_index: Optional[int] = None,
    reassign_stage: Optional[dict] = None,
    depends_on: Optional[Sequence[int]] = None,
) -> dict:
    """`update_task` 的实现体（已持有任务锁）。"""
    # 先校验状态字符串（非法状态不应触达 DB）

    target: Optional[TaskStatus] = None
    if new_status is not None:
        target = TaskStatus.value_of(str(new_status).upper())
        if target is None:
            return {"success": False, "message": f"无效的状态：{new_status}"}

    # ---- clear_fields 类型收口 + 归一 + 白名单校验 ----
    # ① 类型收口必须在归一之前：否则非可迭代值抛未捕获 `TypeError`
    #    （`5` ⇒ "'int' object is not iterable"），绕过 `TogoException` 通道，
    #    模型拿到的是 Python 堆栈措辞而不是错误码。
    # ② 更危险的是 `dict`：可迭代 ⇒ 拿到 keys ⇒ 若 key 恰为 "reviewer_id" 会**静默撤销**，
    #    属"输入形态错但意图被猜对"的静默取一边，正是 draft §4.3.2 要避免的。
    # ③ `bytes` 也在这里挡掉：它会逐字节迭代成 114/101/118…（把 ASCII 码当字段名报给模型）。
    # 口径：接受 `str`（裸字符串兼容，同 `list_tasks.status`）或元素全为 `str` 的
    # `list`/`tuple`；其余一律 `invalid_clear_field`（复用同一码，不新增）。
    if isinstance(clear_fields, str):
        clear_field_names = [clear_fields]
    elif isinstance(clear_fields, (list, tuple)) and all(isinstance(f, str) for f in clear_fields):
        clear_field_names = list(clear_fields)
    elif clear_fields is None:
        clear_field_names = []
    else:
        return {
            "success": False,
            "message": (
                f"clear_fields 必须是字段名数组（当前可用："
                f"{' / '.join(sorted(ALLOWED_CLEAR_FIELDS))}），收到的是 {type(clear_fields).__name__}"
            ),
            "error_code": "invalid_clear_field",
        }
    # 去重保序（预防性：白名单目前只有 1 个成员，扩到多成员时才可能"同一字段处理两次"）
    clear_field_names = list(dict.fromkeys(clear_field_names))
    invalid_fields = [f for f in clear_field_names if f not in ALLOWED_CLEAR_FIELDS]
    if invalid_fields:
        return {
            "success": False,
            "message": (
                f"不支持清除的字段：{'、'.join(str(f) for f in invalid_fields)}；"
                f"当前可清除的字段只有 {' / '.join(sorted(ALLOWED_CLEAR_FIELDS))}"
            ),
            "error_code": "invalid_clear_field",
        }
    # 空数组归一成「未提供」：上面 `list(... or ())` 已把 None / [] 都变成空列表，
    # 所以这里只按「集合是否含 reviewer_id」判断 —— 不存在"空数组当成功"的分支。
    # ⚠️ 判定必须用 `in`（等价 `bool()`），**不能**写成 `clear_fields is not None`：
    # 那样 `clear_fields=[]` 会静默进撤销分支，破坏「空数组=未提供」。
    clearing = "reviewer_id" in clear_field_names

    # 两个参数都省略 ⇒ 老路径（流转 / 评论），行为完全不变
    wants_reviewer_change = reviewer_id is not None or clearing

    if reviewer_id is not None and clearing:
        return {
            "success": False,
            "message": (
                "reviewer_id 与 clear_fields 不能同时给：改人用 reviewer_id，"
                '撤销用 clear_fields=["reviewer_id"]'
            ),
            "error_code": "ambiguous_reviewer_change",
        }

    task = await gtAgentTaskManager.get_task(task_id)
    if task is None:
        return toolResult.failure(f"任务不存在：task_id={task_id}", toolResult.NOT_FOUND)
    if task.team_id != team_id:
        return toolResult.failure("任务不属于当前团队", toolResult.PERMISSION_DENIED)

    old_status = task.status

    # ---- v3 意图归一与互斥（step2 §4.2「非法动作组合」）----
    # 四类意图只能出现一类：状态流转（非自环 new_status）、workflow_action、
    # 节点链调整、验收人变更。同时给多个时一律拒绝：一个调用里既交棒又换人，
    # 时间线上无法审计是哪一步生效。
    flow_action = (workflow_action or '').strip().lower() or None
    if flow_action is not None and flow_action not in ('advance', 'reject'):
        return {
            "success": False,
            "message": f"无效的 workflow_action：{workflow_action}，有效值为 advance / reject",
            "error_code": "invalid_workflow_action",
        }
    has_node_op = any(x is not None for x in (append_stages, remove_stage_index, reassign_stage))
    transition_intent = target is not None and target != old_status
    # 依赖变更：`depends_on is not None` 才算意图 —— **不能**写 `if depends_on`，
    # 那样「显式传 [] 清空依赖」会被当成「未提供」而静默不生效。
    has_depends_change = depends_on is not None
    intents = {
        "状态流转 new_status": transition_intent,
        "workflow_action": flow_action is not None,
        "节点链调整": has_node_op,
        "验收人变更": wants_reviewer_change,
        "依赖变更 depends_on": has_depends_change,
    }
    given = [name for name, present in intents.items() if present]
    # 例外：**只有**「状态流转 + 验收人变更」这一对时，沿用 v2 的专用错误码与文案
    # （`reviewer_change_must_be_separate`，历史用例与文档都按它对齐），
    # 由 `_handle_reviewer_change` 的 D5 分支给出，比笼统的 ambiguous_task_update 更可解释。
    only_reviewer_and_transition = set(given) == {"状态流转 new_status", "验收人变更"}
    if len(given) > 1 and not only_reviewer_and_transition:
        return {
            "success": False,
            "message": (
                f"一次调用只能做一件事，当前同时给了：{'、'.join(given)}；"
                "请拆成多次调用（例：先 workflow_action=\"advance\"，再 reassign_stage）"
            ),
            "error_code": "ambiguous_task_update",
        }

    # 终态只允许评论（step2 §4.2 第一道门）。
    # 依赖变更也在这里拦：已交付任务的依赖关系不允许事后篡改（会改变「当时为什么能开始」的
    # 事实），与交棒/打回/节点调整同等对待，错误码沿用 `task_already_terminal` 保持一致口径。
    if old_status in (TaskStatus.DONE, TaskStatus.CANCELLED) and (
        flow_action is not None or has_node_op or has_depends_change
    ):
        # 依赖变更给独立错误码（`task_depends_on_terminal`），既有 `task_already_terminal`
        # 一字不改 —— 后者是线上调用方已在解析的码，不允许重命名/挪语义。
        if has_depends_change:
            return {
                "success": False,
                "message": f"任务已处于终态（{old_status.value}），不可修改依赖（仅可追加评论）",
                "error_code": "task_depends_on_terminal",
            }
        return {
            "success": False,
            "message": f"任务已处于终态（{old_status.value}），不可再交棒/打回/调整节点（仅可追加评论）",
            "error_code": "task_already_terminal",
        }

    stages = await _load_stages(task) if (flow_action is not None or has_node_op) else []

    if flow_action is not None:
        return await _apply_workflow_action(
            team_id,
            task,
            stages,
            caller_id=caller_id,
            action=flow_action,
            target_stage=target_stage,
            note=note,
        )

    if has_node_op:
        return await _apply_stage_update(
            team_id,
            task,
            stages,
            caller_id=caller_id,
            append_stages=append_stages,
            remove_stage_index=remove_stage_index,
            reassign_stage=reassign_stage,
            note=note,
        )

    # ---- 验收人变更分支（必须先于评论分支：省略 new_status 时否则会落到「note 必填的评论」）----
    if wants_reviewer_change:
        return await _handle_reviewer_change(
            team_id,
            task,
            caller_id=caller_id,
            target=target,
            reviewer_id=reviewer_id,
            clearing=clearing,
            note=note,
        )

    # ---- 依赖变更分支（同样必须先于评论分支）----
    if has_depends_change:
        return await _handle_depends_on_change(
            team_id,
            task,
            caller_id=caller_id,
            depends_on=depends_on,  # type: ignore[arg-type]
            note=note,
        )

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
        message = "已追加评论（任务状态未变更）"
        if old_status in (TaskStatus.DONE, TaskStatus.CANCELLED):
            message += (
                f"；提醒：该任务已是终态（{old_status.value}），评论已记录，但不要在这张单上继续投入。"
                "确实还有要处理的事项，请新建任务单（create_task）或汇报主管，由主管判断是否开新单。"
            )
        return {
            "success": True,
            "action": "commented",
            "task": model_to_dict(task, recurse=False),
            "comment": _serialize_event(event),
            "message": message,
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

    # v3：「谁拿棒谁说话」——推进类动作一律按**当前持棒人**裁定。
    # v2 形状的任务（单节点 / 单审核节点）下与旧口径逐字等价：节点 0 持棒人 =
    # assignee，节点 1 持棒人 = reviewer。故既有用例的权限结论不变。
    transition_stages = await _load_stages(task)
    holder_id = _holder_of(task, transition_stages)

    # 权限校验（按顺序命中、先匹配先裁定）
    if target == TaskStatus.REVIEWING:
        if caller_id != holder_id:
            return {"success": False, "message": "只有执行人（当前持棒人）才能提交验收", "error_code": "permission_denied"}
    elif target == TaskStatus.DONE and old_status == TaskStatus.REVIEWING:
        if caller_id != holder_id:
            return {"success": False, "message": "只有验收人（当前持棒人）才能通过验收", "error_code": "permission_denied"}
    elif target == TaskStatus.IN_PROGRESS and old_status == TaskStatus.REVIEWING:
        if caller_id != holder_id:
            return {"success": False, "message": "只有验收人（当前持棒人）才能打回任务", "error_code": "permission_denied"}
    elif target == TaskStatus.CANCELLED:
        # v3（step1 §3.2.4）：认领前仅创建人；认领后仅当前持棒人（创建人不再可直接取消）
        if not can_cancel(task, caller_id):
            if old_status in (TaskStatus.PENDING, TaskStatus.TODO):
                return {
                    "success": False,
                    "message": "任务尚未认领，只有创建人才能取消任务",
                    "error_code": "permission_denied",
                }
            return {
                "success": False,
                "message": (
                    f"任务已被认领，创建人不能直接取消；只有当前持棒人（agent_id={holder_id}）有权取消。"
                    "请用 send_chat_msg 请持棒人处理"
                ),
                "error_code": "creator_cannot_cancel_after_claim",
            }
    else:
        if caller_id != holder_id:
            return {"success": False, "message": "只有执行人（当前持棒人）才能更新任务状态", "error_code": "permission_denied"}

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

    # 设有验收人时，禁止绕过验收直接 → DONE（仅允许 REVIEWING → DONE）。
    # S-6（#253，原为待讨论、按建议方案实现）：`reviewer_id` 只是「最后一个 REVIEW 节点
    # 负责人」的兼容快照；当游标已在**末节点**（例如 [EXEC, REVIEW, EXEC] 停在节点 2）时，
    # 后面没有任何待审节点，再拦就是误拒。故本闸门只在「后面还有节点」时生效，
    # 与 `_plan_for_compat_transition` 的末节点判定对齐。
    done_cursor = _effective_cursor(task, transition_stages)
    done_last_index = len(transition_stages) - 1
    if (
        target == TaskStatus.DONE
        and old_status != TaskStatus.REVIEWING
        and task.reviewer_id is not None
        and done_cursor < done_last_index
    ):
        return {
            "success": False,
            "message": "该任务设有验收人，请先提交验收（status=REVIEWING），由验收人审核后完成",
            "error_code": "review_required",
        }

    # 执行更新：同一事务内更新主表状态（+ v3 游标/节点局部状态）+ 追加一条流转事件，事务后推送
    # v3：v2 的状态流转在同一事务里同步节点链（映射见 `_plan_for_compat_transition`）
    transition_plan, plan_error = _plan_for_compat_transition(
        task, transition_stages, old_status, target
    )
    if plan_error is not None:
        return plan_error
    updated = await _transition_with_event(
        team_id,
        task,
        actor_id=caller_id,
        from_status=old_status,
        to_status=target,
        content=note,
        **(transition_plan or {}),
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
        return toolResult.failure(f"任务不存在：task_id={task_id}", toolResult.NOT_FOUND)
    if task.team_id != team_id:
        return toolResult.failure("任务不属于当前团队", toolResult.PERMISSION_DENIED)

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
    task_dict["current_block_reason"] = await current_block_reason(
        task, truncate_content=truncate_content
    )

    # v3：节点链视图（含各自局部状态与展示名），前端/TUI 据此渲染工作流进度条
    stages = await _load_stages(task)
    stage_names = await _resolve_display_names(
        [task.current_owner_id, *(_stage_field(stage, "owner_id") for stage in stages)]
    )
    task_dict["current_stage"] = task.current_stage
    task_dict["current_owner_id"] = task.current_owner_id
    task_dict["current_owner_name"] = (
        stage_names.get(task.current_owner_id, f"Agent#{task.current_owner_id}")
        if task.current_owner_id is not None else None
    )
    task_dict["stages"] = _stages_payload(stages, stage_names)

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
        return toolResult.failure(f"事件不存在：event_id={event_id}", toolResult.NOT_FOUND)

    task = await gtAgentTaskManager.get_task(event.task_id)
    if task is None:
        return toolResult.failure(f"事件不存在：event_id={event_id}", toolResult.NOT_FOUND)
    if task.team_id != team_id:
        return toolResult.failure("事件不属于当前团队", toolResult.PERMISSION_DENIED)

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
        return toolResult.failure(f"任务不存在：task_id={task_id}", toolResult.NOT_FOUND)
    if task.team_id != team_id:
        return toolResult.failure("任务不属于当前团队", toolResult.PERMISSION_DENIED)

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


class VisibleScope(NamedTuple):
    """`resolve_visible_scope()` 的返回值：可见 Agent 集合 + 是否发生了降级。"""

    agent_ids: Optional[list[int]]
    degraded: bool = False


async def resolve_visible_scope(team_id: int, caller_id: Optional[int]) -> VisibleScope:
    """算出 `caller_id` 在「管理视角」下可见的 Agent 集合（**唯一权限入口**）。

    返回值语义（三态，调用方必须区分）：
    - `agent_ids=None`：不限范围（Root Leader = 根部门主管，可见全团队）
    - `agent_ids` 非空：可见范围 = 自己 + 所有直接/间接下属（部门主管）
    - `agent_ids=[]`：无管理视角（普通成员，或取不到调用者身份）

    `degraded=True` 只在「团队还没有部门树」时出现：此时谁都不是 Root Leader，
    只能退化为「只看自己」。**降级必须显式告知调用方**（工具体返回
    `scope_degraded=true` + 说明），否则调用方会误以为「全团队就这些任务」——
    这正是我们不接受的静默降级。

    层级信息全部来自 `deptService` 的部门树（不新增配置）：
    - 根部门（`parent_id is None`）的主管是 Root Leader；
    - 其余主管的可查集合 = `{self} ∪ get_sub_agent_ids(...)`（含子孙部门成员）。
    """
    if caller_id is None:
        return VisibleScope([], False)

    tree = await deptService.get_dept_tree(team_id)
    if tree is not None and tree.manager_id == caller_id:
        return VisibleScope(None, False)

    subordinates = await deptService.get_sub_agent_ids(team_id, caller_id)
    if not subordinates:
        if tree is None:
            return VisibleScope([caller_id], True)
        return VisibleScope([], False)
    return VisibleScope([caller_id, *sorted(subordinates)], False)


async def resolve_visible_agent_ids(team_id: int, caller_id: Optional[int]) -> Optional[list[int]]:
    """`resolve_visible_scope()` 的薄封装：只取可见集合（不需要降级标记时用）。"""
    return (await resolve_visible_scope(team_id, caller_id)).agent_ids


async def _resolve_task_agent_names(tasks: list[GtAgentTask]) -> dict[int, str]:
    """批量解析任务列表里出现的 Agent 展示名：`{agent_id: display_name}`。

    **一次** `get_agents_by_ids` 查完所有 `assignee_id` / `reviewer_id`（带缓存的批量
    接口），不做逐条查询——列表接口按条查 Agent 是典型 N+1，20 条任务会多打 40 次库。
    查不到（Agent 已删除）的 ID 不在返回值里，由 `_task_to_dict_with_actor_names`
    统一回退 `Agent#<id>`（与 `agentTurnRunner._resolve_event_actor_name` 同口径）。
    """
    agent_ids: list[int] = []
    seen: set[int] = set()
    for task in tasks:
        for agent_id in (task.assignee_id, task.reviewer_id):
            if agent_id is not None and agent_id not in seen:
                seen.add(agent_id)
                agent_ids.append(agent_id)
    if not agent_ids:
        return {}
    agents = await gtAgentManager.get_agents_by_ids(agent_ids)
    return {agent.id: agent.display_name for agent in agents}


def _task_to_dict_with_actor_names(
    task: GtAgentTask, agent_names: dict[int, str]
) -> dict:
    """任务 ORM 对象 → dict，并补 `assignee_name` / `reviewer_name`（展示名）。

    只**追加**字段，已有字段的名字与语义原样保留（Web 列表接口也走这条链路）。
    `assignee_id` / `reviewer_id` 为 `None` 时对应 name 给 `None`——不给 `Agent#None`；
    Agent 已被删除时回退 `Agent#<id>`（见 `_resolve_task_agent_names`）。
    """
    from playhouse.shortcuts import model_to_dict

    data = model_to_dict(task, recurse=False)
    for role in ("assignee", "reviewer"):
        agent_id = getattr(task, f"{role}_id", None)
        if agent_id is None:
            data[f"{role}_name"] = None
        else:
            data[f"{role}_name"] = agent_names.get(agent_id, f"Agent#{agent_id}")
    return data


async def list_tasks(
    team_id: int,
    assignee_id: Optional[int] = None,
    reviewer_id: Optional[int] = None,
    status: Optional[Union[str, Sequence[str]]] = None,
    open_only: bool = False,
    limit: int = 20,
    scope: Optional[str] = None,
    caller_id: Optional[int] = None,
    title_keyword: Optional[str] = None,
    order: str = gtAgentTaskManager.ORDER_PRIORITY_FIRST,
    mine: bool = False,
    include_upcoming: bool = False,
) -> dict:
    """查询协作任务列表。

    返回 `tasks`（本页，高优先级优先）、`count`（本页条数）、
    `total`（**符合条件的任务总数**，跨页、不受 `limit` 影响）——
    `total > count` 即说明还有更多任务被 `limit` 截断。`limit` 会原样回显
    （工具层已收敛过范围，这里回显的是**实际生效值**）。

    `status` 接受**单值或列表**（单值 `"TODO"` / 列表 `["TODO","DONE"]`），
    大小写不敏感。列表里只要有非法值就**整体报错**并把非法项全列出来——
    不静默丢弃后返回一个看起来正常的子集（静默降级正是这里要避免的）。

    `title_keyword` 按**任务标题**过滤：大小写不敏感子串匹配，`None` 表示不按标题
    过滤；空串/纯空白由工具层拦掉（`LIKE '%%'` 会匹配全部，等于静默取消过滤）。

    `order` 是排序口径，**默认 `ORDER_PRIORITY_FIRST` 与新增该参数前完全一致**
    （Web 列表接口依赖它）：`ORDER_PRIORITY_FIRST` = 优先级优先、同级 id 升序；
    `ORDER_ID_DESC` = 最新在前（Agent 侧用）。非法值由 DAL 抛 `ValueError`。

    `scope` 是**过滤条件**（与 assignee / reviewer 并列，三者至少给一个，工具层入口检查）：
    - `None`（默认）：不加范围过滤——保持历史行为，Web 列表接口依赖它拿全团队数据；
    - `"self"`：与我相关 = 我执行 **OR** 我验收，等价 `agent_ids=[caller_id]`；
    - `"team"`：我可见范围（Root Leader 全团队 / 部门主管 自己+子孙部门），
      无管理视角且没给精确过滤器时返回 `error_code="scope_not_permitted"`
      （**不静默降级为自己**，否则调用方会误以为拿到全团队数据）。
    与 assignee / reviewer 同传时：先按 scope 算范围、再叠加精确过滤（AND）。
    唯一例外：`scope="team"` 但调用者没有管理视角、又给了精确过滤器 ⇒ 忽略 scope
    按过滤器查（「成员查自己的任务」不该无条件报错）。
    部门树缺失时 `scope="team"` 退化为只看自己，并显式返回 `scope_degraded=True`。
    """
    if scope not in (None, 'self', 'team'):
        return {
            "success": False,
            "message": f"无效的 scope：{scope}，有效值为 self / team",
            "error_code": "invalid_scope",
        }

    # `status` 兼容裸字符串：native / tsp 驱动不做 schema 校验，模型可能按老习惯传
    # 单值。先归一成列表再解析，否则 `for s in "TODO"` 会逐字符迭代成 T/O/D/O，
    # 报出一个假的「非法状态 T」。
    single_value = isinstance(status, str)
    if isinstance(status, str):
        status = [status]
    # 归一后不该再是 str，下面直接迭代 `status`。用 assert 钉住这条不变量，
    # 而不是再写一个 `if not isinstance(status, str)` 的恒真分支（那曾是不可达死代码，
    # 且分支里的失败返回漏了 error_code）。
    assert not isinstance(status, str), "status 归一后不应仍是 str"

    status_enum: Optional[TaskStatus] = None
    status_enum_list: list[TaskStatus] | None = None
    exclude_statuses: list[TaskStatus] | None = None
    if status is not None:
        parsed: list[TaskStatus] = []
        invalid: list[str] = []
        for raw in status:
            enum_value = TaskStatus.value_of(str(raw).upper()) if isinstance(raw, str) else None
            if enum_value is None:
                invalid.append(str(raw))
            elif enum_value not in parsed:
                parsed.append(enum_value)
        if invalid:
            return {
                "success": False,
                "message": f"无效的状态：{'、'.join(invalid)}；有效值为 "
                           f"{' / '.join(s.name for s in TaskStatus)}",
                "error_code": "invalid_status",
            }
        if not parsed:
            return {
                "success": False,
                "message": "status 列表为空；如需不按状态过滤请省略该参数",
                "error_code": "invalid_status",
            }
        if single_value:
            # 单值仍走 `==`（DAL 的 `status_enum` 分支）：既有 SQL 与
            # `assert_awaited_once_with(status=TaskStatus.X, ...)` 断言口径不变。
            status_enum = parsed[0]
        else:
            status_enum_list = parsed
    elif open_only:
        exclude_statuses = [TaskStatus.DONE, TaskStatus.CANCELLED]

    # 标题关键字走 SQL 子串匹配；空串归一成 None（`LIKE '%%'` 会匹配全部，
    # 当成过滤条件等于静默取消过滤）。
    title_keyword = (title_keyword or "").strip() or None

    # v3「待办视图」：只看接力棒当前在我手上的任务（`include_upcoming=True` 时并把
    # 「后续节点有我但还没轮到我」的在途任务一并列出）。
    # **默认不启用**：v2 的 Web 列表 / 既有 Agent 调用都依赖「不传过滤项 = 看全团队」，
    # 把默认改成持棒人视图会静默改变既有调用方看到的数据（比多一个参数危险得多）。
    holder_filter_id: Optional[int] = None
    upcoming_task_ids: list[int] | None = None
    if mine:
        if caller_id is None:
            return {
                "success": False,
                "message": "mine=True 需要调用者身份（caller_id 不可得）",
                "error_code": "missing_target",
            }
        if include_upcoming:
            upcoming_task_ids = await gtAgentTaskStageManager.get_task_ids_by_upcoming_owner(caller_id)
        holder_filter_id = caller_id
    elif include_upcoming:
        return {
            "success": False,
            "message": "include_upcoming 只能与 mine=True 一起用（它表示「除当前待办外，再包含即将轮到我」）",
            "error_code": "invalid_filter_combo",
        }

    visible_agent_ids: Optional[list[int]] = None
    scope_degraded = False
    if scope == 'self':
        if caller_id is None:
            return {
                "success": False,
                "message": "无法确定调用者身份，scope=\"self\" 需要 caller_id",
                "error_code": "missing_target",
            }
        visible_agent_ids = [caller_id]
    elif scope == 'team':
        visible_scope = await resolve_visible_scope(team_id, caller_id)
        visible_agent_ids = visible_scope.agent_ids
        scope_degraded = visible_scope.degraded
        if visible_agent_ids is not None and not visible_agent_ids:
            if assignee_id is None and reviewer_id is None and title_keyword is None:
                return {
                    "success": False,
                    "message": "当前身份没有管理视角，无法用 scope=\"team\" 查看全团队任务；请改为指定 assignee 或 reviewer",
                    "error_code": "scope_not_permitted",
                }
            # 有精确过滤器：忽略 scope，按过滤器查（不报错）
            visible_agent_ids = None

    # `list` 与 `count` 必须共用同一份过滤条件（`total` 与列表口径一致）
    filters: dict = dict(
        team_id=team_id,
        assignee_id=assignee_id,
        reviewer_id=reviewer_id,
        status=status_enum_list if status_enum_list is not None else status_enum,
        exclude_statuses=exclude_statuses,
        agent_ids=visible_agent_ids,
    )
    if holder_filter_id is not None:
        filters["current_owner_id"] = holder_filter_id
    if upcoming_task_ids:
        filters["upcoming_task_ids"] = upcoming_task_ids
    if title_keyword is not None:
        filters["title_keyword"] = title_keyword
    tasks = await gtAgentTaskManager.list_tasks(**filters, limit=limit, order=order)
    total = await gtAgentTaskManager.count_tasks(**filters)
    agent_names = await _resolve_task_agent_names(tasks)
    result = {
        "success": True,
        "tasks": [
            _task_to_dict_with_actor_names(task, agent_names) for task in tasks
        ],
        "count": len(tasks),
        "total": total,
        "limit": limit,
    }
    if mine:
        result["holder_view"] = True
        if include_upcoming:
            result["include_upcoming"] = True
    if scope_degraded:
        result["scope_degraded"] = True
        result["message"] = "团队暂无部门层级数据，scope=\"team\" 已降级为「只看与我相关的任务」，结果不代表全团队"
    return result

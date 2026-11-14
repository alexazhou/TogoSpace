"""#227 任务系统 v3（单棒接力多级审核工作流）集成判据：真库、真入口。

覆盖交付标准里点名的两条链路 + 四条红例：
  1. 完整多级链路「执行 → 初审 → 终审」走通到 DONE；
  2. reject 到**中间节点**后逐级重推进走通（防跳过重审）；
  3. 「删除 REVIEW 节点被拒」（防绕过红线）；
  4. 「认领后创建人取消被拒」；
  5. v2 兼容：0 个审核节点 ⇒ 末节点 advance 直接 DONE；1 个审核节点 ⇒ 与旧行为等价；
  6. 待办隔离：`mine` / `include_upcoming` 的持棒人过滤。

全部调用 `taskService` 的公开入口（create_task / update_task / get_task / list_tasks），
不直接拼 SQL，也不 mock 任何东西——所以「节点表真的写了、游标真的推进了」是被真库验证的。
"""
from __future__ import annotations

import os
import sys

import pytest

from constants import StageNodeType, StageStatus, TaskEventAction, TaskStatus
from dal.db import gtAgentManager, gtAgentTaskManager, gtAgentTaskStageManager, gtTeamManager
from service import (
    agentService,
    funcToolService,
    ormService,
    persistenceService,
    presetService,
    roomService,
    taskService,
)
from tests.base import ServiceTestCase
from util import configUtil

TEAM = "test_team"
_CONFIG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "test_collaboration_task_flow",
    "config",
)

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


def _status_of(task: dict) -> str:
    """把 `get_task` 返回体的 `status` 归一成字符串。

    `playhouse.shortcuts.model_to_dict` 对 `EnumField` 的既有输出是**单元素列表**
    （`[TaskStatus.TODO]`），这是 v2 就有的形态、v3 未改；用例里不想被它干扰，
    故集中在这里归一，而不是散落一堆 `[0].value`。
    """
    raw = task["status"]
    if isinstance(raw, list) and raw:
        raw = raw[0]
    return raw.value if hasattr(raw, "value") else str(raw)


class _WorkflowCase(ServiceTestCase):
    """真库 + 真团队（alice 主管 / bob 成员），复用协作流测试的团队配置。"""

    @classmethod
    async def async_setup_class(cls):
        db_path = cls._get_test_db_path()
        await ormService.startup(db_path)
        await persistenceService.startup()
        await roomService.startup()
        await presetService._import_role_templates_from_app_config()
        await funcToolService.startup()
        cfg = configUtil.load(_CONFIG_DIR, preset_dir=_CONFIG_DIR, force_reload=True)
        await presetService._import_team_from_config(cfg.teams_preset[0])
        await agentService.startup()
        await agentService.load_all_team_agents()
        await roomService.load_all_rooms()

    @classmethod
    async def async_teardown_class(cls):
        await agentService.shutdown()
        funcToolService.shutdown()
        roomService.shutdown()
        await persistenceService.shutdown()
        await ormService.shutdown()

    async def _agents(self) -> tuple[int, int, int]:
        """返回 (team_id, alice_id, bob_id)。alice 是团队主管，bob 是她的下属。"""
        team = await gtTeamManager.get_team(TEAM)
        alice = await gtAgentManager.get_agent(team.id, "alice")
        bob = await gtAgentManager.get_agent(team.id, "bob")
        assert alice is not None and bob is not None, "团队配置缺 alice / bob"
        return team.id, alice.id, bob.id

    async def _create_chain(self, *, title: str) -> tuple[int, int, int, int]:
        """建一条 3 节点链：`[bob(EXEC), alice(REVIEW 初审), bob(REVIEW 终审)]`。

        返回 (task_id, team_id, alice_id, bob_id)。创建人是 alice（主管），
        她可以把任务派给下属 bob；节点负责人都是本团队在职成员，故创建合法。
        """
        team_id, alice, bob = await self._agents()
        result = await taskService.create_task(
            team_id=team_id,
            creator_id=alice,
            title=title,
            assignee_id=bob,
            stages=[
                {"owner_id": alice, "node_type": "REVIEW", "name": "组长初审"},
                {"owner_id": bob, "node_type": "REVIEW", "name": "总监终审"},
            ],
        )
        assert result["success"] is True, result
        return result["task_id"], team_id, alice, bob

    async def _stages(self, task_id: int) -> list[tuple[int, str, int, str]]:
        """(stage_index, node_type.value, owner_id, stage_status.value) 列表。"""
        rows = await gtAgentTaskStageManager.get_stages_by_task(task_id)
        return [
            (r.stage_index, r.node_type.value, r.owner_id, r.stage_status.value) for r in rows
        ]

    async def _task(self, team_id: int, task_id: int) -> dict:
        result = await taskService.get_task(team_id=team_id, task_id=task_id)
        assert result["success"] is True, result
        return result["task"]


class TestFullChain(_WorkflowCase):
    """链路 1：执行 → 初审 → 终审 走通到 DONE。"""

    async def test_create_builds_three_stage_chain(self):
        """创建即物化 3 个节点：节点 0 恒为 EXECUTION(assignee)，后续按 stages 顺序。"""
        task_id, team_id, alice, bob = await self._create_chain(title="v3-链-创建")

        assert await self._stages(task_id) == [
            (0, "EXECUTION", bob, "ACTIVE"),
            (1, "REVIEW", alice, "WAITING"),
            (2, "REVIEW", bob, "WAITING"),
        ]
        task = await self._task(team_id, task_id)
        assert _status_of(task) == "TODO"
        assert task["current_stage"] == 0
        assert task["current_owner_id"] == bob
        assert [s["name"] for s in task["stages"]] == ["", "组长初审", "总监终审"]

    async def test_full_advance_chain_reaches_done(self):
        """三步交棒：TODO →(认领) IN_PROGRESS →(交棒) REVIEWING → REVIEWING → DONE。"""
        task_id, team_id, alice, bob = await self._create_chain(title="v3-链-走通")

        claimed = await taskService.update_task(
            team_id=team_id, caller_id=bob, task_id=task_id, new_status="IN_PROGRESS",
        )
        assert claimed["success"] is True, claimed

        first = await taskService.update_task(
            team_id=team_id, caller_id=bob, task_id=task_id,
            workflow_action="advance", note="开发完成，交初审",
        )
        assert first["success"] is True, first
        assert first["action"] == "advanced"
        task = await self._task(team_id, task_id)
        assert (_status_of(task), task["current_stage"], task["current_owner_id"]) == (
            "REVIEWING", 1, alice,
        )
        assert await self._stages(task_id) == [
            (0, "EXECUTION", bob, "APPROVED"),
            (1, "REVIEW", alice, "ACTIVE"),
            (2, "REVIEW", bob, "WAITING"),
        ]

        second = await taskService.update_task(
            team_id=team_id, caller_id=alice, task_id=task_id,
            workflow_action="advance", note="初审通过，交终审",
        )
        assert second["success"] is True, second
        task = await self._task(team_id, task_id)
        assert (_status_of(task), task["current_stage"], task["current_owner_id"]) == (
            "REVIEWING", 2, bob,
        )

        third = await taskService.update_task(
            team_id=team_id, caller_id=bob, task_id=task_id,
            workflow_action="advance", note="终审通过",
        )
        assert third["success"] is True, third
        task = await self._task(team_id, task_id)
        assert _status_of(task) == "DONE"
        assert await self._stages(task_id) == [
            (0, "EXECUTION", bob, "APPROVED"),
            (1, "REVIEW", alice, "APPROVED"),
            (2, "REVIEW", bob, "APPROVED"),
        ]

        # 事件时间线：每步都有明确动作类型与节点游标（同态流转不再靠猜）
        events = await taskService.list_task_events(team_id=team_id, task_id=task_id)
        # `list_task_events` 返回**最新在前**（v2 既有契约），故按事件 id 正序还原时间顺序
        advanced = sorted(
            (e for e in events["events"] if e["action_type"] == TaskEventAction.ADVANCE.value),
            key=lambda e: e["id"],
        )
        assert [(e["from_stage"], e["to_stage"]) for e in advanced] == [(0, 1), (1, 2), (2, 2)]


class TestReject(_WorkflowCase):
    """链路 2：打回中间节点后必须逐级重推进。"""

    async def test_reject_to_middle_stage_resets_later_stages(self):
        """终审打回到初审（2 → 1）：节点 1 ACTIVE、节点 2 重置 WAITING，状态仍 REVIEWING。"""
        task_id, team_id, alice, bob = await self._create_chain(title="v3-打回")
        await taskService.update_task(team_id=team_id, caller_id=bob, task_id=task_id, new_status="IN_PROGRESS")
        await taskService.update_task(team_id=team_id, caller_id=bob, task_id=task_id, workflow_action="advance")
        await taskService.update_task(team_id=team_id, caller_id=alice, task_id=task_id, workflow_action="advance")

        rejected = await taskService.update_task(
            team_id=team_id, caller_id=bob, task_id=task_id,
            workflow_action="reject", target_stage=1, note="初审口径有遗漏，退回重核",
        )
        assert rejected["success"] is True, rejected
        assert rejected["action"] == "rejected"
        task = await self._task(team_id, task_id)
        assert (_status_of(task), task["current_stage"], task["current_owner_id"]) == (
            "REVIEWING", 1, alice,
        )
        assert await self._stages(task_id) == [
            (0, "EXECUTION", bob, "APPROVED"),
            (1, "REVIEW", alice, "ACTIVE"),
            (2, "REVIEW", bob, "WAITING"),
        ]

        # 逐级重推：初审 → 终审 → DONE
        again = await taskService.update_task(
            team_id=team_id, caller_id=alice, task_id=task_id,
            workflow_action="advance", note="已重新核对",
        )
        assert again["success"] is True, again
        done = await taskService.update_task(
            team_id=team_id, caller_id=bob, task_id=task_id,
            workflow_action="advance", note="终审通过",
        )
        assert done["success"] is True, done
        assert _status_of(await self._task(team_id, task_id)) == "DONE"

    async def test_reject_back_to_execution_returns_in_progress(self):
        """打回节点 0（执行）：状态回到 IN_PROGRESS，后续审核节点全部 WAITING。"""
        task_id, team_id, alice, bob = await self._create_chain(title="v3-打回执行")
        await taskService.update_task(team_id=team_id, caller_id=bob, task_id=task_id, new_status="IN_PROGRESS")
        await taskService.update_task(team_id=team_id, caller_id=bob, task_id=task_id, workflow_action="advance")

        rejected = await taskService.update_task(
            team_id=team_id, caller_id=alice, task_id=task_id,
            workflow_action="reject", note="实现有缺陷，退回开发",
        )
        assert rejected["success"] is True, rejected
        task = await self._task(team_id, task_id)
        assert (_status_of(task), task["current_stage"], task["current_owner_id"]) == (
            "IN_PROGRESS", 0, bob,
        )
        assert [s[3] for s in await self._stages(task_id)] == ["ACTIVE", "WAITING", "WAITING"]

    async def test_reject_requires_note(self):
        """打回必须写原因（否则被打回的人不知道要改什么）。"""
        task_id, team_id, alice, bob = await self._create_chain(title="v3-打回无note")
        await taskService.update_task(team_id=team_id, caller_id=bob, task_id=task_id, new_status="IN_PROGRESS")
        await taskService.update_task(team_id=team_id, caller_id=bob, task_id=task_id, workflow_action="advance")

        result = await taskService.update_task(
            team_id=team_id, caller_id=alice, task_id=task_id, workflow_action="reject",
        )
        assert result["success"] is False
        assert result["error_code"] == "reject_note_required"

    async def test_reject_target_must_be_earlier(self):
        """target_stage 必须 < 当前游标（含「打回给自己」也必须被拒）。"""
        task_id, team_id, alice, bob = await self._create_chain(title="v3-打回越界")
        await taskService.update_task(team_id=team_id, caller_id=bob, task_id=task_id, new_status="IN_PROGRESS")
        await taskService.update_task(team_id=team_id, caller_id=bob, task_id=task_id, workflow_action="advance")

        for target in (1, 2):
            result = await taskService.update_task(
                team_id=team_id, caller_id=alice, task_id=task_id,
                workflow_action="reject", target_stage=target, note="x",
            )
            assert result["success"] is False, (target, result)
            assert result["error_code"] == "invalid_reject_target", (target, result)

    async def test_reject_from_stage_zero_rejected(self):
        """节点 0 没有前序节点，不可打回。"""
        task_id, team_id, alice, bob = await self._create_chain(title="v3-节点0打回")
        await taskService.update_task(team_id=team_id, caller_id=bob, task_id=task_id, new_status="IN_PROGRESS")

        result = await taskService.update_task(
            team_id=team_id, caller_id=bob, task_id=task_id,
            workflow_action="reject", target_stage=0, note="x",
        )
        assert result["success"] is False
        assert result["error_code"] == "invalid_reject_target"


class TestStageProtection(_WorkflowCase):
    """红线与权限：REVIEW 不可删（认领后）、非持棒人不能推进、认领后创建人不能取消。"""

    async def test_remove_review_stage_rejected_after_claim(self):
        """认领后删除 REVIEW 节点必须被拒——这是「防绕过审批」的核心红线。"""
        task_id, team_id, alice, bob = await self._create_chain(title="v3-删审核")
        await taskService.update_task(team_id=team_id, caller_id=bob, task_id=task_id, new_status="IN_PROGRESS")

        result = await taskService.update_task(
            team_id=team_id, caller_id=bob, task_id=task_id, remove_stage_index=1,
        )
        assert result["success"] is False, result
        assert result["error_code"] == "cannot_remove_review_stage"
        # 链没被打断：仍 3 个节点，第 1 个还是 REVIEW
        assert len(await self._stages(task_id)) == 3
        assert (await self._stages(task_id))[1][1] == StageNodeType.REVIEW.value

    async def test_remove_review_stage_rejected_even_for_creator_after_claim(self):
        """创建人也不例外：认领后谁都不能删 REVIEW 节点。"""
        task_id, team_id, alice, bob = await self._create_chain(title="v3-创建人删审核")
        await taskService.update_task(team_id=team_id, caller_id=bob, task_id=task_id, new_status="IN_PROGRESS")

        result = await taskService.update_task(
            team_id=team_id, caller_id=alice, task_id=task_id, remove_stage_index=2,
        )
        assert result["success"] is False, result
        assert result["error_code"] in ("cannot_remove_review_stage", "not_current_stage_owner")

    async def test_creator_can_remove_review_stage_before_claim(self):
        """认领前创建人可自由编排（含删 REVIEW 节点）——与「认领后不可删」成对，避免一刀切。"""
        team_id, alice, bob = await self._agents()
        created = await taskService.create_task(
            team_id=team_id, creator_id=alice, title="v3-认领前删", assignee_id=bob,
            stages=[{"owner_id": bob, "node_type": "REVIEW", "name": "临时审核"}],
        )
        assert created["success"] is True, created
        task_id = created["task_id"]

        removed = await taskService.update_task(
            team_id=team_id, caller_id=alice, task_id=task_id, remove_stage_index=1,
            note="该审核节点不需要了",
        )
        assert removed["success"] is True, removed
        assert [s[0] for s in await self._stages(task_id)] == [0]
        # 兼容列同步：没有任何 REVIEW 节点 ⇒ reviewer_id 置空
        task = await self._task(team_id, task_id)
        assert task["reviewer_id"] is None

    async def test_append_execution_stage_then_remove_it(self):
        """后置加签加一个执行节点（可删），再加一个审核节点（不可删）。"""
        task_id, team_id, alice, bob = await self._create_chain(title="v3-加签")
        await taskService.update_task(team_id=team_id, caller_id=bob, task_id=task_id, new_status="IN_PROGRESS")

        added = await taskService.update_task(
            team_id=team_id, caller_id=bob, task_id=task_id,
            append_stages=[{"owner_id": bob, "node_type": "EXECUTION", "name": "补测"}],
        )
        assert added["success"] is True, added
        assert len(await self._stages(task_id)) == 4
        assert (await self._stages(task_id))[3][1] == StageNodeType.EXECUTION.value

        removed = await taskService.update_task(
            team_id=team_id, caller_id=bob, task_id=task_id, remove_stage_index=3,
            note="复用现有用例，无需补测",
        )
        assert removed["success"] is True, removed
        assert len(await self._stages(task_id)) == 3

    async def test_non_holder_cannot_advance(self):
        """「谁拿棒谁说话」：棒在 bob 手上时 alice 不能替他交棒。"""
        task_id, team_id, alice, bob = await self._create_chain(title="v3-越权交棒")
        await taskService.update_task(team_id=team_id, caller_id=bob, task_id=task_id, new_status="IN_PROGRESS")

        result = await taskService.update_task(
            team_id=team_id, caller_id=alice, task_id=task_id, workflow_action="advance", note="x",
        )
        assert result["success"] is False, result
        assert result["error_code"] == "not_current_stage_owner"

    async def test_creator_cannot_cancel_after_claim(self):
        """认领后创建人取消必须被拒；当前持棒人可以取消（成对，避免一刀切）。"""
        task_id, team_id, alice, bob = await self._create_chain(title="v3-取消权")
        await taskService.update_task(team_id=team_id, caller_id=bob, task_id=task_id, new_status="IN_PROGRESS")

        denied = await taskService.update_task(
            team_id=team_id, caller_id=alice, task_id=task_id, new_status="CANCELLED",
        )
        assert denied["success"] is False, denied
        assert denied["error_code"] == "creator_cannot_cancel_after_claim"

        ok = await taskService.update_task(
            team_id=team_id, caller_id=bob, task_id=task_id, new_status="CANCELLED",
            note="需求取消",
        )
        assert ok["success"] is True, ok
        assert _status_of(await self._task(team_id, task_id)) == "CANCELLED"

    async def test_creator_can_cancel_before_claim(self):
        """认领前创建人可取消（v2 行为保留）。"""
        task_id, team_id, alice, bob = await self._create_chain(title="v3-认领前取消")
        result = await taskService.update_task(
            team_id=team_id, caller_id=alice, task_id=task_id, new_status="CANCELLED",
        )
        assert result["success"] is True, result
        assert _status_of(await self._task(team_id, task_id)) == "CANCELLED"


class TestV2Compatibility(_WorkflowCase):
    """v2 兼容硬判据：0 个审核节点直接 DONE；1 个审核节点与旧行为等价。"""

    async def test_no_reviewer_single_node_advance_to_done(self):
        """只传 assignee（0 个审核节点）⇒ 单节点链；交棒即 DONE，且不需要「提交验收」。"""
        team_id, alice, bob = await self._agents()
        created = await taskService.create_task(
            team_id=team_id, creator_id=alice, title="v2-无审核", assignee_id=bob,
        )
        assert created["success"] is True, created
        task_id = created["task_id"]
        assert len(await self._stages(task_id)) == 1

        await taskService.update_task(team_id=team_id, caller_id=bob, task_id=task_id, new_status="IN_PROGRESS")
        result = await taskService.update_task(
            team_id=team_id, caller_id=bob, task_id=task_id, workflow_action="advance",
            note="做完了",
        )
        assert result["success"] is True, result
        task = await self._task(team_id, task_id)
        assert _status_of(task) == "DONE"
        assert [s[3] for s in await self._stages(task_id)] == ["APPROVED"]

    async def test_single_reviewer_matches_legacy_v2_flow(self):
        """1 个审核节点：完全用 v2 的 `new_status` 调用方式走完，行为与旧版一致。"""
        team_id, alice, bob = await self._agents()
        created = await taskService.create_task(
            team_id=team_id, creator_id=alice, title="v2-单审核", assignee_id=bob, reviewer_id=alice,
        )
        assert created["success"] is True, created
        task_id = created["task_id"]
        assert await self._stages(task_id) == [
            (0, "EXECUTION", bob, "ACTIVE"),
            (1, "REVIEW", alice, "WAITING"),
        ]

        await taskService.update_task(
            team_id=team_id, caller_id=bob, task_id=task_id, new_status="IN_PROGRESS", note="开工",
        )
        submit = await taskService.update_task(
            team_id=team_id, caller_id=bob, task_id=task_id, new_status="REVIEWING", note="完成",
        )
        assert submit["success"] is True, submit
        assert submit["action"] == "transitioned"
        assert _status_of(await self._task(team_id, task_id)) == "REVIEWING"

        approve = await taskService.update_task(
            team_id=team_id, caller_id=alice, task_id=task_id, new_status="DONE", note="通过",
        )
        assert approve["success"] is True, approve
        assert _status_of(await self._task(team_id, task_id)) == "DONE"
        assert await self._stages(task_id) == [
            (0, "EXECUTION", bob, "APPROVED"),
            (1, "REVIEW", alice, "APPROVED"),
        ]

    async def test_single_reviewer_reject_via_legacy_new_status(self):
        """v2 的「打回」调用方式（REVIEWING → IN_PROGRESS）仍等价于打回节点 0。"""
        team_id, alice, bob = await self._agents()
        created = await taskService.create_task(
            team_id=team_id, creator_id=alice, title="v2-打回", assignee_id=bob, reviewer_id=alice,
        )
        task_id = created["task_id"]
        await taskService.update_task(team_id=team_id, caller_id=bob, task_id=task_id, new_status="IN_PROGRESS")
        await taskService.update_task(team_id=team_id, caller_id=bob, task_id=task_id, new_status="REVIEWING")
        result = await taskService.update_task(
            team_id=team_id, caller_id=alice, task_id=task_id, new_status="IN_PROGRESS", note="返工",
        )
        assert result["success"] is True, result
        task = await self._task(team_id, task_id)
        assert (_status_of(task), task["current_stage"], task["current_owner_id"]) == ("IN_PROGRESS", 0, bob)
        assert [s[3] for s in await self._stages(task_id)] == ["ACTIVE", "WAITING"]


class TestHolderView(_WorkflowCase):
    """待办隔离：`mine` / `include_upcoming`。"""

    async def test_mine_only_returns_current_holder(self):
        """棒在 bob 手上时：bob 的 mine 里能看到，alice 的 mine 里看不到。"""
        task_id, team_id, alice, bob = await self._create_chain(title="v3-待办隔离")

        bob_view = await taskService.list_tasks(team_id=team_id, caller_id=bob, mine=True)
        assert task_id in [t["id"] for t in bob_view["tasks"]]
        assert bob_view.get("holder_view") is True

        alice_view = await taskService.list_tasks(team_id=team_id, caller_id=alice, mine=True)
        assert task_id not in [t["id"] for t in alice_view["tasks"]]

    async def test_include_upcoming_shows_future_owner(self):
        """alice 是节点 1 的负责人但还没轮到：默认看不到，`include_upcoming=True` 能看到。"""
        task_id, team_id, alice, bob = await self._create_chain(title="v3-即将轮到我")

        await taskService.update_task(team_id=team_id, caller_id=bob, task_id=task_id, new_status="IN_PROGRESS")
        await taskService.update_task(team_id=team_id, caller_id=bob, task_id=task_id, workflow_action="advance")

        # 棒已在 alice 手上（节点 1）⇒ 此时两个人的 mine 都能看到，属预期，先钉住
        both = await taskService.list_tasks(team_id=team_id, caller_id=alice, mine=True)
        assert task_id in [t["id"] for t in both["tasks"]]

        # 把棒交到终审（bob）后，alice 不再是持棒人；但她的节点已完成，
        # 故 include_upcoming 也不应再把它当成「即将轮到我」
        await taskService.update_task(team_id=team_id, caller_id=alice, task_id=task_id, workflow_action="advance")
        after = await taskService.list_tasks(
            team_id=team_id, caller_id=alice, mine=True, include_upcoming=True,
        )
        assert task_id not in [t["id"] for t in after["tasks"]]

    async def test_include_upcoming_requires_mine(self):
        """`include_upcoming` 单独用会被拒（它只是 mine 视图的扩展）。"""
        team_id, alice, bob = await self._agents()
        result = await taskService.list_tasks(
            team_id=team_id, caller_id=bob, include_upcoming=True,
        )
        assert result["success"] is False
        assert result["error_code"] == "invalid_filter_combo"


class TestAmbiguity(_WorkflowCase):
    """一次调用只能做一件事：动作组合必须显式拒绝，不能猜。"""

    async def test_advance_with_reassign_rejected(self):
        task_id, team_id, alice, bob = await self._create_chain(title="v3-动作互斥")
        await taskService.update_task(team_id=team_id, caller_id=bob, task_id=task_id, new_status="IN_PROGRESS")

        result = await taskService.update_task(
            team_id=team_id, caller_id=bob, task_id=task_id,
            workflow_action="advance", reassign_stage={"stage_index": 1, "owner_id": bob},
        )
        assert result["success"] is False, result
        assert result["error_code"] == "ambiguous_task_update"

    async def test_terminal_task_rejects_workflow_action(self):
        """终态只允许评论：DONE 之后还能 advance 就等于「终态没收口」。"""
        task_id, team_id, alice, bob = await self._create_chain(title="v3-终态")
        await taskService.update_task(team_id=team_id, caller_id=alice, task_id=task_id, new_status="CANCELLED")

        result = await taskService.update_task(
            team_id=team_id, caller_id=bob, task_id=task_id, workflow_action="advance", note="x",
        )
        assert result["success"] is False, result
        assert result["error_code"] == "task_already_terminal"


class TestJudgeDebt(_WorkflowCase):
    """#229 判据债：D1 状态纠偏 / D2 任务级互斥锁。

    这两条行为的共同点是「正常路径上永远不会发生」——D1 只有夹具/手工修过的行才
    触发纠偏，D2 只有并发才触发锁。所以它们此前**没有任何机器判据**（#227 验收人
    的 M9 / M10 变异各自 145 / 139 例全绿），本类专门给它们上锁。
    """

    async def _stages_of(self, task_id: int) -> list[tuple[int, str, int, str]]:
        return await self._stages(task_id)

    # ------------------------------------------------------------------
    # D1：`status=REVIEWING` 但游标仍停在 0（执行节点）⇒ 纠偏到首个 REVIEW 节点
    # ------------------------------------------------------------------

    async def test_reviewing_with_stale_cursor_can_reach_done(self):
        """状态已是 REVIEWING、`current_stage` 却仍是 0 的行，advance 必须能走到 DONE。

        这行的来源是真实的：迁移 0022 会把存量 REVIEWING 回填成 1，但**直接用 SQL
        插入的测试夹具 / 手工修过的行**不会，于是「状态说在审核、游标说在执行」。
        若不纠偏，advance 会把棒交给节点 1（还是 REVIEW）而不是判末节点完成，
        调用方看到的是「明明只有 1 个审核节点却过不去」。

        变异（M9 方向：去掉 `_effective_cursor` 的纠偏）
        ⇒ 本用例的 DONE 断言必红（结果停在 REVIEWING）。
        """
        team_id, alice, bob = await self._agents()
        created = await taskService.create_task(
            team_id=team_id,
            creator_id=alice,
            title="D1-游标滞后-单审核节点",
            assignee_id=bob,
            stages=[{"owner_id": alice, "node_type": "REVIEW", "name": "组长初审"}],
        )
        assert created["success"] is True, created
        task_id = created["task_id"]

        # 造出「状态 REVIEWING + 游标 0」的滞后行（模拟夹具 / 手工修正）
        from model.dbModel.gtAgentTask import GtAgentTask

        row = await gtAgentTaskManager.get_task(task_id)
        row.status = TaskStatus.REVIEWING
        row.current_stage = 0
        row.current_owner_id = alice  # 持棒人是审核节点负责人
        await gtAgentTaskManager.save_task(
            row,
            only=[GtAgentTask.status, GtAgentTask.current_stage, GtAgentTask.current_owner_id],
        )
        await gtAgentTaskStageManager.update_stage_statuses(
            task_id, {0: StageStatus.APPROVED, 1: StageStatus.ACTIVE}
        )
        assert (await self._task(team_id, task_id))["current_stage"] == 0, "前置：游标必须停在 0"

        result = await taskService.update_task(
            team_id=team_id,
            caller_id=alice,
            task_id=task_id,
            workflow_action="advance",
            note="终审通过（滞后游标行）",
        )

        assert result["success"] is True, f"滞后游标行交棒被拒：{result}"
        task = await self._task(team_id, task_id)
        assert _status_of(task) == "DONE", (
            f"纠偏失效：状态停在 {_status_of(task)}、游标 {task['current_stage']}"
            f"（单 REVIEW 节点应从节点 1 直接完成）"
        )

    async def test_stale_cursor_correction_does_not_apply_to_normal_rows(self):
        """对照组：正常行（游标与状态一致）不受纠偏影响，逐级推进的语义不变。

        纠偏若写成「REVIEWING 就一律跳到首个 REVIEW 节点」，会把**多级链的中间态**
        也一并改判（在节点 1 初审中却被跳到 1 —— 幸好相同；但在节点 0 打回后重推时
        会跳级）。这条用例守住「纠偏只在矛盾时生效」。
        """
        task_id, team_id, alice, bob = await self._create_chain(title="D1-正常行对照")
        await taskService.update_task(
            team_id=team_id, caller_id=bob, task_id=task_id, new_status="IN_PROGRESS"
        )
        first = await taskService.update_task(
            team_id=team_id, caller_id=bob, task_id=task_id, workflow_action="advance", note="交初审"
        )
        assert first["success"] is True, first

        task = await self._task(team_id, task_id)
        assert (_status_of(task), task["current_stage"], task["current_owner_id"]) == (
            "REVIEWING", 1, alice,
        ), "正常推进的游标被纠偏改坏了"

    # ------------------------------------------------------------------
    # D2：同一任务的两路并发写必须串行化
    # ------------------------------------------------------------------

    async def test_concurrent_advance_only_one_wins(self):
        """同一 `task_id` 两路并发 advance：只允许一次成功，且只前进一格。

        为什么必须并发才有意义：`update_task` 按 `task_id` 加进程内互斥锁
        （step2 §6.1），锁外的两路会**读到同一份旧状态**再各自写 —— 表现为
        「两次都成功、棒被推两格」或「一次成功但从同一节点算了两次」。

        为了让竞态必然发生（否则两次调用可能天然串行、判据恒绿），这里把
        `_load_stages` 包一层 `sleep`：持有锁的那一路睡完继续，未被锁挡住的那一路
        会**同时**停在读之后 ⇒ 去掉锁（M10 方向）时两路都会看到游标 0。

        断言三件：恰好一次成功 / 游标只前进到 1 / 库里恰有 1 条 ADVANCE 事件。
        """
        import asyncio
        from unittest import mock

        task_id, team_id, alice, bob = await self._create_chain(title="D2-并发交棒")
        await taskService.update_task(
            team_id=team_id, caller_id=bob, task_id=task_id, new_status="IN_PROGRESS"
        )

        real_load = taskService._load_stages
        entered = 0

        async def slow_load(task):
            nonlocal entered
            stages = await real_load(task)
            entered += 1
            # 把「读完 → 写」之间的窗口撑开：没有锁时第二路会在这里与第一路重叠
            await asyncio.sleep(0.05)
            return stages

        with mock.patch.object(taskService, "_load_stages", slow_load):
            results = await asyncio.gather(
                taskService.update_task(
                    team_id=team_id, caller_id=bob, task_id=task_id,
                    workflow_action="advance", note="并发 A",
                ),
                taskService.update_task(
                    team_id=team_id, caller_id=bob, task_id=task_id,
                    workflow_action="advance", note="并发 B",
                ),
            )

        successes = [r for r in results if r.get("success")]
        assert len(successes) == 1, (
            f"并发两路应只有一次成功，实际 {len(successes)} 次：{results}"
        )
        task = await self._task(team_id, task_id)
        assert task["current_stage"] == 1, (
            f"棒被推了 {task['current_stage']} 格（并发双推）；应只前进到节点 1"
        )
        assert task["current_owner_id"] == alice, "持棒人应已交给初审节点负责人"

        events = await taskService.list_task_events(team_id=team_id, task_id=task_id)
        advances = [
            e for e in events["events"] if e["action_type"] == TaskEventAction.ADVANCE.value
        ]
        assert len(advances) == 1, f"库里应有 1 条 ADVANCE 事件，实际 {len(advances)} 条"
        assert (advances[0]["from_stage"], advances[0]["to_stage"]) == (0, 1)

    async def test_concurrent_updates_on_different_tasks_are_not_serialized(self):
        """对照组：不同 `task_id` 的两路写**不**互相阻塞（锁是按任务分的，不是全局锁）。

        这条防的是「把锁写成全局锁也能过 D2」的错误修法：全局锁同样让并发只成功一次，
        但会把不相干的任务串成一队。判据用耗时差：两路各睡 0.15s，按任务分锁应接近
        0.15s，全局锁则接近 0.3s。
        """
        import asyncio
        import time
        from unittest import mock

        team_id, alice, bob = await self._agents()
        ids = []
        for title in ("D2-对照-A", "D2-对照-B"):
            created = await taskService.create_task(
                team_id=team_id, creator_id=alice, title=title, assignee_id=bob,
            )
            assert created["success"] is True, created
            ids.append(created["task_id"])
            await taskService.update_task(
                team_id=team_id, caller_id=bob, task_id=created["task_id"],
                new_status="IN_PROGRESS",
            )

        real_load = taskService._load_stages

        async def slow_load(task):
            stages = await real_load(task)
            await asyncio.sleep(0.15)
            return stages

        with mock.patch.object(taskService, "_load_stages", slow_load):
            started = time.monotonic()
            await asyncio.gather(
                *[
                    taskService.update_task(
                        team_id=team_id, caller_id=bob, task_id=tid,
                        workflow_action="advance", note="并发对照",
                    )
                    for tid in ids
                ]
            )
            elapsed = time.monotonic() - started

        assert elapsed < 0.27, (
            f"两个不同任务被串行化了（耗时 {elapsed:.2f}s，按任务分锁应 ≈0.15s）——"
            "锁写成了全局锁"
        )

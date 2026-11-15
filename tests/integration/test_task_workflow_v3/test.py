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

    async def _third_agent(self, team_id: int) -> int:
        """取或建团队里的第三个成员 `carol`。

        S-1 的「换验收人」必须有第三人：验收人不能等于执行人，二人团队里换人无处可换。
        """
        from dal.db import gtAgentManager
        from model.dbModel.gtAgent import GtAgent

        existing = await gtAgentManager.get_agent(team_id, "carol")
        if existing is not None:
            return existing.id
        alice = await gtAgentManager.get_agent(team_id, "alice")
        assert alice is not None
        await gtAgentManager.batch_save_agents(
            team_id,
            [GtAgent(
                team_id=team_id,
                name="carol",
                role_template_id=alice.role_template_id,
                i18n={"display_name": {"zh-CN": "carol"}},
            )],
        )
        created = await gtAgentManager.get_agent(team_id, "carol")
        assert created is not None, "第三个成员创建失败"
        return created.id

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

        链是 3 节点双 REVIEW：`[bob(EXEC), alice(REVIEW 初审), bob(REVIEW 终审)]`，
        本用例**必须推到节点 2 再交棒**才有鉴别力：纠偏若写成「只要 `REVIEWING` 就
        跳到首个 REVIEW 节点」（过度纠偏，去掉 `node_type != REVIEW` 这半个条件），
        在 0→1、1→2 两步上结果与正常推进**完全相同**（首个 REVIEW 恰好就是节点 1），
        看不出任何差别；只有在「持棒人已在节点 2、状态仍是 `REVIEWING`」时，游标才被
        错误地拉回节点 1 —— 那一步末节点交棒会被算成「1 → 2 再交一次」，整单永远
        到不了 DONE。#229 的旧对照组用 2 节点链（停在节点 1），纠偏在上面是空操作，
        因此过度纠偏变异下 4/4 全绿，形同虚设。

        变异（过度纠偏：`if task.status == TaskStatus.REVIEWING:` 去掉
        `and node_type != StageNodeType.REVIEW`）⇒ 本用例末步的 DONE 断言与
        ADVANCE 事件序列断言必红（红在**本对照组**内）。
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

        second = await taskService.update_task(
            team_id=team_id, caller_id=alice, task_id=task_id, workflow_action="advance", note="初审通过，交终审"
        )
        assert second["success"] is True, second
        task = await self._task(team_id, task_id)
        assert (_status_of(task), task["current_stage"], task["current_owner_id"]) == (
            "REVIEWING", 2, bob,
        ), "正常推进到节点 2 的游标被纠偏改坏了"

        # 节点 2 是 REVIEW 节点（不是首个 REVIEW）且状态是 REVIEWING：
        # 这里交棒必须是「末节点 ⇒整单 DONE」，不能被纠偏拉回节点 1 再交一次
        third = await taskService.update_task(
            team_id=team_id, caller_id=bob, task_id=task_id, workflow_action="advance", note="终审通过"
        )
        assert third["success"] is True, third
        task = await self._task(team_id, task_id)
        assert _status_of(task) == "DONE", (
            f"过度纠偏：节点 2（非首个 REVIEW）的末节点交棒被拉回节点 1，"
            f"状态停在 {_status_of(task)}、游标 {task['current_stage']}"
        )
        assert await self._stages(task_id) == [
            (0, "EXECUTION", bob, "APPROVED"),
            (1, "REVIEW", alice, "APPROVED"),
            (2, "REVIEW", bob, "APPROVED"),
        ]

        events = await taskService.list_task_events(team_id=team_id, task_id=task_id)
        advanced = sorted(
            (e for e in events["events"] if e["action_type"] == TaskEventAction.ADVANCE.value),
            key=lambda e: e["id"],
        )
        assert [(e["from_stage"], e["to_stage"]) for e in advanced] == [(0, 1), (1, 2), (2, 2)], (
            "ADVANCE 事件的 from/to 游标序列被纠偏改坏了"
        )

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
        但会把不相干的任务串成一队。

        **判据不绑墙钟**（#229 记档：旧版断言 `elapsed < 0.27s`，实测 0.160~0.163s，
        余量仅 0.11s，并行负载下有假红风险）。这里改判**临界区是否真的重叠**：
        在被锁包住的 `_load_stages` 上记 `enter/exit` 事件序列，算最大并发深度
        ——按任务分锁时两路能同时停在锁内（深度 2）；全局锁时后一路必须等前一路
        放锁，深度恒为 1。深度是事件序列算出来的，与机器快慢、负载无关。

        会合点用 `asyncio.Event` 而不是「各自 sleep 固定时长」：Task A 进锁后等
        Task B 也进锁（两边互相等到对方入内才继续），所以判绿路径上**不存在任何
        时间阈值**；只有被串行化（错误修法）时先入内的一路才等到 2s 超时后放行，
        那是失败路径的兜底，不会造成通过路径的假红。

        变异（M10g：`_task_lock` 恒返回同一个全局 `_GLOBAL_LOCK`）⇒ 最大并发深度
        恒为 1，本用例必红。

        **深度判据的前提**（#232 / verify-230 记档①）：深度只有在「同一任务一次
        `update_task` 只进一次临界区」时才有意义——若实现改成嵌套进入，单任务自己
        就能贡献深度 2，全局单锁也会判绿（假绿）。故这里先把前提本身判掉：时间线上
        同一 `task_id` 的未配对 `enter` 恒 ≤ 1、且 enter/exit 严格配对收尾。
        变异（`_load_stages` 读完后嵌套再调自身）⇒ 本不变式必红，且此时深度可能是 2
        （判据盲区被覆盖）；「嵌套 + 全局单锁」组合下深度判绿、不变式仍红。
        """
        import asyncio
        from unittest import mock

        team_id, alice, bob = await self._agents()
        ids = []
        for title in ("D2-对照-A", "D2-对照-B"):
            # 用 3 节点链（0→1 是真实的一格推进），单节点链会在 advance 时直接 DONE、
            # 游标不动，那样两路的结构结果区分不出「推了没推」
            task_id, _team_id, _alice, _bob = await self._create_chain(title=title)
            ids.append(task_id)
            await taskService.update_task(
                team_id=team_id, caller_id=bob, task_id=task_id, new_status="IN_PROGRESS",
            )

        real_load = taskService._load_stages
        timeline: list[tuple[str, int]] = []
        both_inside = asyncio.Event()
        # 仅用于「被串行化时别把用例挂死」的兜底；判绿路径不依赖它
        SERIALIZED_FALLBACK = 2.0

        async def recording_load(task):
            # `enter` 记在真实读之前：区间必须**罩住整个临界区调用**，否则区间内的
            # 嵌套调用会被记到区间外（#232 的不变式就白写了）
            timeline.append(("enter", task.id))
            stages = await real_load(task)
            if len({tid for kind, tid in timeline if kind == "enter"}) >= 2:
                both_inside.set()
            try:
                await asyncio.wait_for(both_inside.wait(), timeout=SERIALIZED_FALLBACK)
            except asyncio.TimeoutError:
                pass  # 另一路在本次进去之前拿不到锁 ⇒ 交给下面的深度断言去判红
            timeline.append(("exit", task.id))
            return stages

        with mock.patch.object(taskService, "_load_stages", recording_load):
            results = await asyncio.gather(
                *[
                    taskService.update_task(
                        team_id=team_id, caller_id=bob, task_id=tid,
                        workflow_action="advance", note="并发对照",
                    )
                    for tid in ids
                ]
            )

        depths = []
        depth = 0
        for kind, _tid in timeline:
            depth += 1 if kind == "enter" else -1
            depths.append(depth)
        max_depth = max(depths, default=0)
        open_enters: dict[int, int] = {}

        assert all(r.get("success") is True for r in results), f"两路都该成功：{results}"

        # 前提不变式先证：同任务的临界区不得嵌套（否则深度判据会把「单任务自己」
        # 当成两路并发，全局单锁假绿 —— verify-230 记档①）
        for kind, tid in timeline:
            open_enters[tid] = open_enters.get(tid, 0) + (1 if kind == "enter" else -1)
            assert open_enters[tid] >= 0, (
                f"同一任务出现未配对的 exit（task {tid}）——时间线不成对：{timeline}"
            )
            assert open_enters[tid] <= 1, (
                f"同一任务的临界区出现**嵌套**（task {tid} 同时有 {open_enters[tid]} 个未配对 enter）："
                f"深度判据的前提被破坏 —— 此时「全局单锁」也会被算成深度 {max_depth} 而假绿；"
                f"时间线={timeline}"
            )
        assert set(open_enters.values()) <= {0}, (
            f"enter/exit 未收尾（残留未配对 enter）：{open_enters}；时间线={timeline}"
        )

        assert max_depth >= 2, (
            f"两个不同任务被串行化了（临界区最大并发深度 {max_depth}，按任务分锁应为 2）——"
            f"锁写成了全局锁；临界区时间线={timeline}"
        )
        # 结构正确性：两路各推进一格，且各留 1 条 (0→1) 的 ADVANCE
        for tid in ids:
            task = await self._task(team_id, tid)
            assert (task["current_stage"], task["current_owner_id"]) == (1, alice), (
                f"任务 {tid} 没被正确推进：游标 {task['current_stage']}、持棒人 {task['current_owner_id']}"
            )
            events = await taskService.list_task_events(team_id=team_id, task_id=tid)
            cursor_seq = sorted(
                (e["from_stage"], e["to_stage"])
                for e in events["events"]
                if e["action_type"] == TaskEventAction.ADVANCE.value
            )
            assert cursor_seq == [(0, 1)], f"任务 {tid} 的 ADVANCE 事件异常：{cursor_seq}"


class TestReviewFix253(_WorkflowCase):
    """#253 review_notes 落地判据：S-1（reviewer 变更同步）/ S-2（PENDING·ON_HOLD 闸门）
    / S-4（append_stages·reassign_stage 兼容列同步）/ S-6（末节点 EXECUTION 的 v2 DONE 写法）。

    每条都先在 `2e155037`（未改代码）上跑过并红（证据见 `shared/task-v3-review-fix.md`）。
    """

    # ---------------- S-1：reviewer_id 变更必须同步节点链与持棒人 ----------------

    async def test_s1_change_reviewer_while_reviewing_moves_holder(self):
        """REVIEWING 换验收人：节点 1 负责人与 `current_owner_id` 必须一起换，新验收人可收尾。

        原缺陷：`_update_reviewer_with_event` 只改 `agent_tasks.reviewer_id`，节点链与
        持棒人不动 ⇒ 新验收人调 DONE/advance/reject 全被拒，待办仍挂在旧人名下。
        """
        team_id, alice, bob = await self._agents()
        carol = await self._third_agent(team_id)
        created = await taskService.create_task(
            team_id=team_id, creator_id=alice, title="S1-REVIEWING换验收人",
            assignee_id=bob, reviewer_id=alice,
        )
        assert created["success"] is True, created
        task_id = created["task_id"]
        await taskService.update_task(team_id=team_id, caller_id=bob, task_id=task_id, new_status="IN_PROGRESS")
        submitted = await taskService.update_task(
            team_id=team_id, caller_id=bob, task_id=task_id, new_status="REVIEWING", note="提交验收",
        )
        assert submitted["success"] is True, submitted
        assert (await self._stages(task_id))[1][2] == alice, "前置：节点 1 应是旧验收人"

        changed = await taskService.update_task(
            team_id=team_id, caller_id=alice, task_id=task_id, reviewer_id=carol,
        )
        assert changed["success"] is True, changed

        task = await self._task(team_id, task_id)
        assert (await self._stages(task_id))[1][2] == carol, "节点 1 负责人没跟着换 ⇒ v3 侧持棒人仍是旧人"
        assert task["current_owner_id"] == carol, "主表持棒人没跟着换"
        assert task["reviewer_id"] == carol

        done = await taskService.update_task(
            team_id=team_id, caller_id=carol, task_id=task_id, new_status="DONE", note="新验收人通过",
        )
        assert done["success"] is True, f"换人后的新验收人被拒：{done}"
        assert _status_of(await self._task(team_id, task_id)) == "DONE"

    async def test_s1_clear_reviewer_removes_review_stage(self):
        """撤销验收人：必须同时移除 REVIEW 节点，之后执行人可直接 DONE。

        原缺陷：节点链残留 REVIEW 节点 ⇒ 执行人置 DONE 时被 `non_final_stage_cannot_done` 拦住。
        """
        team_id, alice, bob = await self._agents()
        created = await taskService.create_task(
            team_id=team_id, creator_id=alice, title="S1-撤销验收人",
            assignee_id=bob, reviewer_id=alice,
        )
        assert created["success"] is True, created
        task_id = created["task_id"]
        await taskService.update_task(team_id=team_id, caller_id=bob, task_id=task_id, new_status="IN_PROGRESS")

        cleared = await taskService.update_task(
            team_id=team_id, caller_id=alice, task_id=task_id, clear_fields=["reviewer_id"],
        )
        assert cleared["success"] is True, cleared

        stages = await self._stages(task_id)
        assert [s[1] for s in stages] == ["EXECUTION"], f"撤销验收人后仍残留审核节点：{stages}"
        assert (await self._task(team_id, task_id))["reviewer_id"] is None

        done = await taskService.update_task(
            team_id=team_id, caller_id=bob, task_id=task_id, new_status="DONE", note="无需验收，直接完成",
        )
        assert done["success"] is True, f"撤销验收人后执行人仍不能直接完成：{done}"
        assert _status_of(await self._task(team_id, task_id)) == "DONE"

    async def test_s1_set_reviewer_appends_review_stage(self):
        """给无验收人的任务补设 `reviewer_id`：必须追加 REVIEW 节点，之后能提交验收。

        原缺陷：节点链只有 1 个 EXECUTION 节点 ⇒ 提交 REVIEWING 被 `non_final_stage_cannot_done` 拒。
        """
        team_id, alice, bob = await self._agents()
        created = await taskService.create_task(
            team_id=team_id, creator_id=alice, title="S1-补设验收人", assignee_id=bob,
        )
        assert created["success"] is True, created
        task_id = created["task_id"]
        assert [s[1] for s in await self._stages(task_id)] == ["EXECUTION"], "前置：单执行节点"
        await taskService.update_task(team_id=team_id, caller_id=bob, task_id=task_id, new_status="IN_PROGRESS")

        set_reviewer = await taskService.update_task(
            team_id=team_id, caller_id=alice, task_id=task_id, reviewer_id=alice,
        )
        assert set_reviewer["success"] is True, set_reviewer
        assert [s[1] for s in await self._stages(task_id)] == ["EXECUTION", "REVIEW"], (
            f"补设验收人后没有追加审核节点：{await self._stages(task_id)}"
        )

        submitted = await taskService.update_task(
            team_id=team_id, caller_id=bob, task_id=task_id, new_status="REVIEWING", note="提交验收",
        )
        assert submitted["success"] is True, f"补设验收人后仍不能提交验收：{submitted}"

    async def test_s1_multi_level_task_rejects_reviewer_id_shorthand(self):
        """多级任务（节点数 > 2 或 REVIEW 节点 > 1）拒绝 `reviewer_id=` 写法，引导用 `reassign_stage`。"""
        task_id, team_id, alice, bob = await self._create_chain(title="S1-多级拒绝v2写法")
        carol = await self._third_agent(team_id)
        reviewer_before = (await self._task(team_id, task_id))["reviewer_id"]

        result = await taskService.update_task(
            team_id=team_id, caller_id=alice, task_id=task_id, reviewer_id=carol,
        )
        assert result["success"] is False, f"多级任务应拒绝 reviewer_id 写法：{result}"
        assert result["error_code"] == "reviewer_change_requires_reassign_stage", result
        assert "reassign_stage" in result["message"], result["message"]
        assert (await self._task(team_id, task_id))["reviewer_id"] == reviewer_before, (
            "被拒的调用不该改动任何东西"
        )

    # ---------------- S-4：节点链调整后同步 v2 兼容列 ----------------

    async def test_s4_append_stages_syncs_compat_reviewer_id(self):
        """`append_stages` 追加 REVIEW 节点后，主表 `reviewer_id` 必须同步，否则 v2 提交验收被 `no_reviewer` 拒。"""
        team_id, alice, bob = await self._agents()
        created = await taskService.create_task(
            team_id=team_id, creator_id=alice, title="S4-append同步reviewer", assignee_id=bob,
        )
        assert created["success"] is True, created
        task_id = created["task_id"]
        await taskService.update_task(team_id=team_id, caller_id=bob, task_id=task_id, new_status="IN_PROGRESS")

        appended = await taskService.update_task(
            team_id=team_id, caller_id=bob, task_id=task_id,
            append_stages=[{"owner_id": alice, "node_type": "REVIEW", "name": "补审核"}],
        )
        assert appended["success"] is True, appended
        assert (await self._task(team_id, task_id))["reviewer_id"] == alice, (
            "append_stages 没同步 reviewer_id ⇒ v2 提交验收会被 no_reviewer 拒"
        )

        submitted = await taskService.update_task(
            team_id=team_id, caller_id=bob, task_id=task_id, new_status="REVIEWING", note="提交验收",
        )
        assert submitted["success"] is True, f"加签后 v2 提交验收仍被拒：{submitted}"

    async def test_s4_reassign_stage_zero_syncs_assignee_id(self):
        """`reassign_stage` 换节点 0 负责人时，主表 `assignee_id` 必须同步，否则新人认领失败。"""
        team_id, alice, bob = await self._agents()
        carol = await self._third_agent(team_id)
        created = await taskService.create_task(
            team_id=team_id, creator_id=alice, title="S4-换节点0负责人", assignee_id=bob,
        )
        assert created["success"] is True, created
        task_id = created["task_id"]

        moved = await taskService.update_task(
            team_id=team_id, caller_id=alice, task_id=task_id,
            reassign_stage={"stage_index": 0, "owner_id": carol},
        )
        assert moved["success"] is True, moved
        assert (await self._task(team_id, task_id))["assignee_id"] == carol, (
            "换节点 0 负责人后 assignee_id 没同步 ⇒ claim_task 与按 assignee 过滤仍归属旧人"
        )

        claimed = await taskService.update_task(
            team_id=team_id, caller_id=carol, task_id=task_id, new_status="IN_PROGRESS", note="新人认领",
        )
        assert claimed["success"] is True, f"新负责人认领被拒：{claimed}"

    # ---------------- S-2：advance/reject 不能绕过 PENDING 依赖与 ON_HOLD ----------------

    async def test_s2_advance_rejected_when_dependency_unmet(self):
        """`PENDING`（依赖未完成）时 `advance` 必须被拒并报 `dependency_not_met`。"""
        team_id, alice, bob = await self._agents()
        blocker = await taskService.create_task(
            team_id=team_id, creator_id=alice, title="S2-前置未完成", assignee_id=bob,
        )
        assert blocker["success"] is True, blocker
        blocked = await taskService.create_task(
            team_id=team_id, creator_id=alice, title="S2-依赖未满足",
            assignee_id=bob, depends_on=[blocker["task_id"]],
        )
        assert blocked["success"] is True, blocked
        task_id = blocked["task_id"]
        assert _status_of(await self._task(team_id, task_id)) == "PENDING", "前置：依赖未完成应为 PENDING"

        result = await taskService.update_task(
            team_id=team_id, caller_id=bob, task_id=task_id, workflow_action="advance", note="硬推",
        )
        assert result["success"] is False, f"PENDING 任务被 advance 绕过依赖直接推进：{result}"
        assert result["error_code"] == "dependency_not_met", result
        assert _status_of(await self._task(team_id, task_id)) == "PENDING", "被拒后状态不该变"

    async def test_s2_advance_and_reject_rejected_when_on_hold(self):
        """`ON_HOLD` 时 `advance` 与 `reject` 都必须被拒（先恢复在途状态）。"""
        team_id, alice, bob = await self._agents()
        created = await taskService.create_task(
            team_id=team_id, creator_id=alice, title="S2-搁置中", assignee_id=bob, status="ON_HOLD",
        )
        assert created["success"] is True, created
        task_id = created["task_id"]
        assert _status_of(await self._task(team_id, task_id)) == "ON_HOLD", "前置：创建即搁置"

        advanced = await taskService.update_task(
            team_id=team_id, caller_id=bob, task_id=task_id, workflow_action="advance", note="搁置中硬推",
        )
        assert advanced["success"] is False, f"ON_HOLD 任务被 advance 绕过：{advanced}"
        assert advanced["error_code"] == "task_on_hold", advanced

        rejected = await taskService.update_task(
            team_id=team_id, caller_id=bob, task_id=task_id,
            workflow_action="reject", target_stage=0, note="搁置中硬打回",
        )
        assert rejected["success"] is False, f"ON_HOLD 任务被 reject 绕过：{rejected}"
        assert rejected["error_code"] == "task_on_hold", rejected

    # ---------------- S-6：末节点是 EXECUTION 时 v2 的 DONE 写法不该被误拒 ----------------

    async def test_s6_last_execution_node_allows_legacy_done(self):
        """`[EXEC(bob), REVIEW(alice), EXEC(bob)]` 走到末节点（EXECUTION）时，`new_status="DONE"` 应被允许。

        原缺陷：`reviewer_id`（最后一个 REVIEW 节点负责人）非空 ⇒ 被 `review_required` 误拒，
        尽管当前已在末节点、后面没有任何待审节点。
        """
        team_id, alice, bob = await self._agents()
        # `stages` 与 `reviewer_id` 互斥，故三段链整体由 stages 描述：
        # [EXECUTION(bob 由 assignee 决定), REVIEW(alice), EXECUTION(bob)]
        created = await taskService.create_task(
            team_id=team_id, creator_id=alice, title="S6-末节点执行",
            assignee_id=bob,
            stages=[
                {"owner_id": alice, "node_type": "REVIEW", "name": "初审"},
                {"owner_id": bob, "node_type": "EXECUTION", "name": "返工收尾"},
            ],
        )
        assert created["success"] is True, created
        task_id = created["task_id"]
        assert [s[1] for s in await self._stages(task_id)] == ["EXECUTION", "REVIEW", "EXECUTION"], (
            f"前置：应为三段链，实际 {await self._stages(task_id)}"
        )

        await taskService.update_task(team_id=team_id, caller_id=bob, task_id=task_id, new_status="IN_PROGRESS")
        first = await taskService.update_task(
            team_id=team_id, caller_id=bob, task_id=task_id, workflow_action="advance", note="交初审",
        )
        assert first["success"] is True, first
        second = await taskService.update_task(
            team_id=team_id, caller_id=alice, task_id=task_id, workflow_action="advance", note="初审通过",
        )
        assert second["success"] is True, second
        task = await self._task(team_id, task_id)
        assert (_status_of(task), task["current_stage"]) == ("IN_PROGRESS", 2), "前置：棒应在末节点"

        done = await taskService.update_task(
            team_id=team_id, caller_id=bob, task_id=task_id, new_status="DONE", note="末节点完成",
        )
        assert done["success"] is True, f"末节点 EXECUTION 的 v2 DONE 写法被误拒：{done}"
        assert _status_of(await self._task(team_id, task_id)) == "DONE"

    # ---------------- S-3：节点链调整的多步写必须原子 ----------------

    async def test_s3_append_stages_rolls_back_on_event_failure(self):
        """`append_stages` 写事件失败时，节点表与主表都不能留下部分写入。

        原缺陷：`_apply_stage_update` 三个分支都没包 `atomic()`（违反
        `gtAgentTaskStageManager` 的「调用方已在事务内」约定）⇒ 事件写入抛错时，
        节点已被插入、`reviewer_id` 已被改，库里出现「节点多了、事件没记」的半状态。
        """
        from unittest import mock

        team_id, alice, bob = await self._agents()
        created = await taskService.create_task(
            team_id=team_id, creator_id=alice, title="S3-append原子性", assignee_id=bob,
        )
        assert created["success"] is True, created
        task_id = created["task_id"]
        await taskService.update_task(team_id=team_id, caller_id=bob, task_id=task_id, new_status="IN_PROGRESS")
        stages_before = await self._stages(task_id)
        reviewer_before = (await self._task(team_id, task_id))["reviewer_id"]

        with mock.patch.object(
            taskService.gtAgentTaskEventManager, "append_event",
            side_effect=RuntimeError("injected-append-event-failure"),
        ):
            with pytest.raises(RuntimeError):
                await taskService.update_task(
                    team_id=team_id, caller_id=bob, task_id=task_id,
                    append_stages=[{"owner_id": alice, "node_type": "REVIEW", "name": "补审核"}],
                )

        assert await self._stages(task_id) == stages_before, (
            f"事件写入失败后节点表仍有部分写入：{await self._stages(task_id)}"
        )
        assert (await self._task(team_id, task_id))["reviewer_id"] == reviewer_before, (
            "事件写入失败后主表 reviewer_id 已被改（半状态）"
        )

    async def test_s3_reassign_stage_rolls_back_on_event_failure(self):
        """`reassign_stage` 写事件失败时，节点负责人与主表快照都要回滚。"""
        from unittest import mock

        team_id, alice, bob = await self._agents()
        created = await taskService.create_task(
            team_id=team_id, creator_id=alice, title="S3-reassign原子性", assignee_id=bob,
        )
        assert created["success"] is True, created
        task_id = created["task_id"]
        stages_before = await self._stages(task_id)
        task_before = await self._task(team_id, task_id)

        with mock.patch.object(
            taskService.gtAgentTaskEventManager, "append_event",
            side_effect=RuntimeError("injected-reassign-event-failure"),
        ):
            with pytest.raises(RuntimeError):
                await taskService.update_task(
                    team_id=team_id, caller_id=alice, task_id=task_id,
                    reassign_stage={"stage_index": 0, "owner_id": alice},
                )

        assert await self._stages(task_id) == stages_before, (
            f"事件写入失败后节点负责人已被改：{await self._stages(task_id)}"
        )
        task_after = await self._task(team_id, task_id)
        assert (task_after["assignee_id"], task_after["current_owner_id"]) == (
            task_before["assignee_id"], task_before["current_owner_id"],
        ), "事件写入失败后主表 assignee_id / current_owner_id 已被改（半状态）"


class TestFirstActiveTask253(_WorkflowCase):
    """#253 S-5：多级 EXECUTION 链下，旧持棒人不该被 `get_first_active_task` 命中。

    单独一个类是为了拿到**干净的库**：`get_first_active_task` 返回「最早的一条」，
    若与前面的用例共库，任何更早的在途任务都会干扰判定。
    """

    async def test_s5_former_holder_is_not_woken_on_multi_exec_chain(self):
        """`[EXEC(bob), EXEC(carol), REVIEW(alice)]` 棒已交到 carol ⇒ bob 不该再被唤醒。

        原缺陷：查询把 v2 的 `(assignee_id=我 & status IN [TODO, IN_PROGRESS])` 与
        `current_owner_id=我` 直接 `OR` —— bob 仍是 assignee_id 且任务 IN_PROGRESS，
        于是拿到一个「幽灵待办」触发调度；而真正持棒的 carol 也可能因为 ID 顺序被挡住。
        """
        from dal.db import gtAgentTaskManager

        team_id, alice, bob = await self._agents()
        carol = await self._third_agent(team_id)
        created = await taskService.create_task(
            team_id=team_id, creator_id=alice, title="S5-多级执行链",
            assignee_id=bob,
            stages=[
                {"owner_id": carol, "node_type": "EXECUTION", "name": "第二步执行"},
                {"owner_id": alice, "node_type": "REVIEW", "name": "终审"},
            ],
        )
        assert created["success"] is True, created
        task_id = created["task_id"]
        assert [s[1] for s in await self._stages(task_id)] == ["EXECUTION", "EXECUTION", "REVIEW"], (
            f"前置：应为两段执行 + 一段审核，实际 {await self._stages(task_id)}"
        )

        await taskService.update_task(team_id=team_id, caller_id=bob, task_id=task_id, new_status="IN_PROGRESS")
        moved = await taskService.update_task(
            team_id=team_id, caller_id=bob, task_id=task_id, workflow_action="advance", note="交给第二步",
        )
        assert moved["success"] is True, moved
        task = await self._task(team_id, task_id)
        assert (task["current_stage"], task["current_owner_id"], task["assignee_id"]) == (1, carol, bob), (
            f"前置：棒应在 carol 手上而 assignee 仍是 bob，实际 {task['current_stage']}/{task['current_owner_id']}/{task['assignee_id']}"
        )

        woken = await gtAgentTaskManager.get_first_active_task(bob)
        assert woken is None or woken.id != task_id, (
            f"旧持棒人 bob 仍被 get_first_active_task 命中（task_id={getattr(woken, 'id', None)}）⇒ 幽灵待办"
        )
        holder_task = await gtAgentTaskManager.get_first_active_task(carol)
        assert holder_task is not None and holder_task.id == task_id, (
            "真正持棒的 carol 反而查不到该任务（判据不能靠「谁都不返回」过关）"
        )

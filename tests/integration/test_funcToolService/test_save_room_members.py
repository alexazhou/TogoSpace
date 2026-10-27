"""#85：`save_room` 工具层的成员重复拒绝（Layer 1）。

缺陷：`save_room` 用 `len(member_names) < 2` 判人数，而 `["alice","alice"]` 的
len 是 2 直接通过 → `resolved_ids=[7,7]` → 房间被 `upsert_room` 判成 PRIVATE（len==2）
并生成「alice 和 alice」话题；之后调度器把「同一人连续发言」判为跳过，房间永久 IDLE。

修复口径：**重复成员一律报错，不静默去重**——与 `roomService._assert_room_agents_valid`
及 REST 入口同口径。去重会悄悄改掉成员数与房间类型（`["alice","alice","bob"]` 会从
「3 人群聊」变成「2 人单聊」），比直接报错更难排查；是否去掉重复由调用方决定。

  · `["alice","alice"]` → `duplicate_agent_ids`（不是「不足 2 人」）；
  · `["alice","alice","bob"]` → 同样报错，不落地任何房间；
  · `["alice"]` 仍走原来的「不足 2 人」分支，文案不变；
  · 名字不存在时仍先报「未找到成员」，判重不得抢先改文案。
"""

import os
import sys

from constants import EmployStatus, RoomType
from dal.db import gtAgentManager, gtRoleTemplateManager, gtRoomManager, gtTeamManager
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtRoleTemplate import GtRoleTemplate
from model.dbModel.gtRoom import GtRoom
from model.dbModel.gtRoomMessage import GtRoomMessage
from model.dbModel.gtTeam import GtTeam
import service.ormService as ormService
from service.funcToolService import tools
from service.roomService import ToolCallContext
from tests.base import ServiceTestCase

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

TEAM = "t_save_room_members"


class TestSaveRoomMemberDedup(ServiceTestCase):
    """每个用例独立建 team（本仓 `ServiceTestCase` 没有 async_setup_method 钩子，故显式调用）。"""

    @classmethod
    async def async_setup_class(cls):
        await ormService.startup(cls._get_test_db_path())

    @classmethod
    async def async_teardown_class(cls):
        await ormService.shutdown()

    async def _prepare(self) -> None:
        await GtRoom.delete().aio_execute()
        await GtRoomMessage.delete().aio_execute()
        await GtAgent.delete().aio_execute()
        await GtTeam.delete().aio_execute()
        await GtRoleTemplate.delete().aio_execute()
        gtAgentManager.clear_agent_cache()

        team = await gtTeamManager.save_team(GtTeam(name=TEAM))
        template = await gtRoleTemplateManager.save_role_template(
            GtRoleTemplate(name="tpl_save_room", model="gpt-4o")
        )
        await gtAgentManager.batch_save_agents(
            team.id,
            [
                GtAgent(
                    team_id=team.id,
                    name=name,
                    role_template_id=template.id,
                    model="",
                    employ_status=EmployStatus.ON_BOARD,
                )
                for name in ("alice", "bob", "charlie")
            ],
        )
        self.team_id = team.id
        self.ctx = ToolCallContext(agent_id=0, team_id=team.id, tool_name="save_room")
        agents = await gtAgentManager.get_team_all_agents(team.id)
        self.agent_ids = {a.name: a.id for a in agents}

    async def _save(self, name: str, member_names: list[str]) -> dict:
        await self._prepare()
        return await tools.save_room(name=name, member_names=member_names, _context=self.ctx)

    async def _rooms(self) -> list[GtRoom]:
        return await gtRoomManager.get_rooms_by_team(self.team_id)

    # ------------------------------------------------------------------ #
    # 重复成员：一律报错，不去重
    # ------------------------------------------------------------------ #

    async def test_duplicate_only_members_rejected(self):
        """`["alice","alice"]` → 报 duplicate_agent_ids，且不落库。"""
        result = await self._save("dup_only", ["alice", "alice"])

        assert result["success"] is False
        assert result["error_code"] == "duplicate_agent_ids"
        assert "重复" in result["message"]
        assert "alice" in result["message"]
        assert await self._rooms() == []

    async def test_duplicate_plus_valid_member_rejected(self):
        """`["alice","alice","bob"]` 同样报错 —— 不许静默去重成 2 人 PRIVATE。"""
        result = await self._save("dup_plus_bob", ["alice", "alice", "bob"])

        assert result["success"] is False
        assert result["error_code"] == "duplicate_agent_ids"
        assert "alice" in result["message"]
        # 去重会把它变成 2 人单聊，语义被悄悄改掉；必须拦住而不是"顺手修正"
        assert await self._rooms() == []

    async def test_duplicate_reported_before_room_lookup(self):
        """判重要在"房间已存在"之前：否则 repeats 会被误导成去改 overwrite_existing。"""
        await self._prepare()
        first = await tools.save_room(
            name="same_name", member_names=["alice", "bob"], _context=self.ctx
        )
        assert first["success"] is True

        result = await tools.save_room(
            name="same_name", member_names=["alice", "alice"], overwrite_existing=True, _context=self.ctx
        )

        assert result["success"] is False
        assert result["error_code"] == "duplicate_agent_ids"

    async def test_duplicate_names_with_whitespace_detected(self):
        """名称两侧空白先被 strip，再判重复：`["alice"," alice "]` 是同一人。"""
        result = await self._save("ws_dup", ["alice", " alice "])

        assert result["success"] is False
        assert result["error_code"] == "duplicate_agent_ids"
        assert await self._rooms() == []

    # ------------------------------------------------------------------ #
    # 非重复的失败分支不得被改文案
    # ------------------------------------------------------------------ #

    async def test_single_member_message_unchanged(self):
        """`["alice"]` 仍走「不足 2 人」分支，与重复是两回事。"""
        result = await self._save("solo", ["alice"])

        assert result["success"] is False
        assert result["message"] == "房间成员不足 2 人（当前 1 人）。"
        assert await self._rooms() == []

    async def test_unknown_member_still_reported_first(self):
        """名字不存在时仍报「未找到成员」，判重逻辑不得抢先改文案。"""
        result = await self._save("ghost", ["alice", "ghost"])

        assert result["success"] is False
        assert "未找到成员: ghost" in result["message"]

    # ------------------------------------------------------------------ #
    # 合法入参的回归护栏
    # ------------------------------------------------------------------ #

    async def test_two_distinct_members_creates_private(self):
        """合法 2 人 → PRIVATE，话题是两个人的名字。"""
        result = await self._save("private_room", ["alice", "bob"])

        assert result["success"] is True
        assert result["room"]["type"] == "单聊"
        assert result["room"]["members"] == ["alice", "bob"]

        rooms = await self._rooms()
        assert len(rooms) == 1
        saved = rooms[0]
        assert saved.type == RoomType.PRIVATE
        assert saved.agent_ids == [self.agent_ids["alice"], self.agent_ids["bob"]]
        assert saved.initial_topic.count("alice") == 1
        assert "bob" in saved.initial_topic

    async def test_three_distinct_members_still_group(self):
        """回归护栏：3 个互不相同的成员仍应是群聊，成员顺序保持入参顺序。"""
        result = await self._save("group_room", ["alice", "bob", "charlie"])

        assert result["success"] is True
        assert result["room"]["type"] == "群聊"
        assert result["room"]["members"] == ["alice", "bob", "charlie"]
        saved = (await self._rooms())[0]
        assert saved.type == RoomType.GROUP

"""#85：`save_room` 工具层的成员去重（Layer 1）。

缺陷：`save_room` 用 `len(member_names) < 2` 判人数，而 `["alice","alice"]` 的
len 是 2 直接通过 → `resolved_ids=[7,7]` → 房间被 `upsert_room` 判成 PRIVATE（len==2）
并生成「alice 和 alice」话题；之后调度器把「同一人连续发言」判为跳过，房间永久 IDLE。

修复口径（#85 裁定）：
  · **按 ID** 去重（不是按名字数量），去重后不足 2 人才报错；
  · `["alice","alice","bob"]` **静默去重后继续** → 落地 `[alice, bob]`、2 人 PRIVATE、
    话题是「alice 和 bob」——调用方语义是「这批成员」，重复只是写法冗余；
  · 报错文案要能区分「名称重复导致实际只有 N 人」与「本来就少于 2 人」。
"""

import os
import sys

import pytest

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
    # 去重后不足 2 人：必须明确失败
    # ------------------------------------------------------------------ #

    async def test_duplicate_only_members_rejected_with_reason(self):
        """`["alice","alice"]` 去重后只有 1 人 → 失败，且文案说明是重复导致。"""
        result = await self._save("dup_only", ["alice", "alice"])

        assert result["success"] is False
        assert "去重后不足 2 人" in result["message"]
        assert "alice" in result["message"]
        # 与「本来就只给了一个人」的文案要能区分开
        assert "房间成员不足 2 人" not in result["message"]
        assert await self._rooms() == []

    async def test_single_member_message_unchanged(self):
        """`["alice"]` 仍走原来的「不足 2 人」分支，文案不得被去重逻辑改写。"""
        result = await self._save("solo", ["alice"])

        assert result["success"] is False
        assert result["message"] == "房间成员不足 2 人（当前 1 人）。"
        assert await self._rooms() == []

    # ------------------------------------------------------------------ #
    # 去重后仍有 2 人：静默去重后继续
    # ------------------------------------------------------------------ #

    async def test_duplicate_plus_one_member_succeeds_after_dedup(self):
        """`["alice","alice","bob"]` → 成功，成员落地为 `[alice, bob]`（2 人 PRIVATE）。"""
        result = await self._save("dup_plus_bob", ["alice", "alice", "bob"])

        assert result["success"] is True
        assert result["room"]["type"] == "单聊"
        assert result["room"]["members"] == ["alice", "bob"]

        rooms = await self._rooms()
        assert len(rooms) == 1
        saved = rooms[0]
        assert saved.type == RoomType.PRIVATE
        assert saved.agent_ids == [self.agent_ids["alice"], self.agent_ids["bob"]]

    async def test_deduped_private_topic_uses_two_distinct_names(self):
        """核心断言：话题必须是「alice 和 bob」，不能是「alice 和 alice」。"""
        await self._save("dup_topic", ["alice", "alice", "bob"])

        saved = (await self._rooms())[0]
        assert "alice" in saved.initial_topic
        assert "bob" in saved.initial_topic
        assert "alice和alice" not in saved.initial_topic
        assert saved.initial_topic.count("alice") == 1

    async def test_order_preserved_after_dedup(self):
        """去重保留**首次出现**的顺序，不要把 [bob, bob, alice] 变成 [alice, bob]。"""
        await self._save("order_room", ["bob", "bob", "alice"])

        saved = (await self._rooms())[0]
        assert saved.agent_ids == [self.agent_ids["bob"], self.agent_ids["alice"]]

    async def test_three_distinct_members_still_group(self):
        """回归护栏：3 个互不相同的成员仍应是群聊，成员不去重也不丢。"""
        result = await self._save("group_room", ["alice", "bob", "charlie"])

        assert result["success"] is True
        assert result["room"]["type"] == "群聊"
        assert result["room"]["members"] == ["alice", "bob", "charlie"]
        saved = (await self._rooms())[0]
        assert saved.type == RoomType.GROUP

    async def test_unknown_member_still_reported_first(self):
        """名字不存在时仍报「未找到成员」，去重逻辑不得抢先改文案。"""
        result = await self._save("ghost", ["alice", "ghost"])

        assert result["success"] is False
        assert "未找到成员: ghost" in result["message"]

    async def test_duplicate_names_with_whitespace_are_deduped(self):
        """名称两侧空白先被 strip，再判重复：`["alice"," alice "]` 是同一人。"""
        result = await self._save("ws_dup", ["alice", " alice "])

        assert result["success"] is False
        assert "去重后不足 2 人" in result["message"]

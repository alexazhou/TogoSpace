import os
import sys

import pytest

from tests.base import ServiceTestCase
from dal.db import gtAgentManager, gtRoomManager, gtTeamManager, gtRoleTemplateManager
from exception import TogoException
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtRoom import GtRoom
from model.dbModel.gtRoomMessage import GtRoomMessage
from model.dbModel.gtTeam import GtTeam
from model.dbModel.gtRoleTemplate import GtRoleTemplate
from service import ormService
import service.roomService as roomService
from constants import RoomType, EmployStatus
from util.configTypes import AgentConfig

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


class TestUpsertRoom(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        await ormService.startup(cls._get_test_db_path())

    @classmethod
    async def async_teardown_class(cls):
        await ormService.shutdown()

    async def _reset_tables(self):
        await GtRoom.delete().aio_execute()
        await GtAgent.delete().aio_execute()
        await GtRoomMessage.delete().aio_execute()
        await GtTeam.delete().aio_execute()
        await GtRoleTemplate.delete().aio_execute()

    async def _setup_team_with_agents(self, team_name: str, agent_names: list[str]) -> GtTeam:
        await gtRoleTemplateManager.save_role_template(
            GtRoleTemplate(name="dummy", model="gpt-4o")
        )
        team = await gtTeamManager.save_team(GtTeam(name=team_name))
        agents = [
            GtAgent(
                team_id=team.id,
                name=n,
                role_template_id=await gtRoleTemplateManager.resolve_role_template_id_by_name("dummy"),
                model="",
                employ_status=EmployStatus.ON_BOARD,
            )
            for n in agent_names
        ]
        await gtAgentManager.batch_save_agents(team.id, agents)
        return team

    async def _get_agent_id(self, team_id: int, agent_name: str) -> int:
        agent = await gtAgentManager.get_agent(team_id, agent_name, status=None)
        assert agent is not None
        return agent.id

    # ------------------------------------------------------------------
    # upsert_room — 类型自动判断
    # ------------------------------------------------------------------

    async def test_upsert_room_two_members_creates_private(self):
        """2 名成员时，房间类型应自动设为 PRIVATE。"""
        await self._reset_tables()
        team = await self._setup_team_with_agents("t_room_private", ["alice", "bob"])
        alice_id = await self._get_agent_id(team.id, "alice")
        bob_id = await self._get_agent_id(team.id, "bob")

        saved = await roomService.upsert_room(
            team_id=team.id, name="chat", agent_ids=[alice_id, bob_id],
        )

        assert saved.type == RoomType.PRIVATE

    async def test_upsert_room_three_or_more_members_creates_group(self):
        """3 名及以上成员时，房间类型应自动设为 GROUP。"""
        await self._reset_tables()
        team = await self._setup_team_with_agents("t_room_group", ["alice", "bob", "charlie"])
        alice_id = await self._get_agent_id(team.id, "alice")
        bob_id = await self._get_agent_id(team.id, "bob")
        charlie_id = await self._get_agent_id(team.id, "charlie")

        saved = await roomService.upsert_room(
            team_id=team.id, name="group_chat", agent_ids=[alice_id, bob_id, charlie_id],
        )

        assert saved.type == RoomType.GROUP

    async def test_upsert_room_without_max_rounds_keeps_null(self):
        """未显式传 max_rounds 时，房间记录应保留为空，而不是自动写入默认值。"""
        await self._reset_tables()
        team = await self._setup_team_with_agents("t_room_null_rounds", ["alice", "bob", "charlie"])
        alice_id = await self._get_agent_id(team.id, "alice")
        bob_id = await self._get_agent_id(team.id, "bob")
        charlie_id = await self._get_agent_id(team.id, "charlie")

        saved = await roomService.upsert_room(
            team_id=team.id, name="group_chat", agent_ids=[alice_id, bob_id, charlie_id],
        )

        assert saved.max_rounds is None

    # ------------------------------------------------------------------
    # upsert_room — 重复成员校验 (ROOM_DUPLICATE)
    # ------------------------------------------------------------------

    async def test_upsert_room_raises_on_duplicate_members(self):
        """创建与已有房间成员集合相同的房间，应报错 ROOM_DUPLICATE。"""
        await self._reset_tables()
        team = await self._setup_team_with_agents("t_room_dup", ["alice", "bob"])
        alice_id = await self._get_agent_id(team.id, "alice")
        bob_id = await self._get_agent_id(team.id, "bob")

        await roomService.upsert_room(
            team_id=team.id, name="room_a", agent_ids=[alice_id, bob_id],
        )

        with pytest.raises(TogoException) as exc_info:
            await roomService.upsert_room(
                team_id=team.id, name="room_b", agent_ids=[alice_id, bob_id],
            )
        assert exc_info.value.error_code == "ROOM_DUPLICATE"

    async def test_upsert_room_duplicate_check_ignores_member_order(self):
        """成员顺序不同但集合相同时，应报错 ROOM_DUPLICATE。"""
        await self._reset_tables()
        team = await self._setup_team_with_agents("t_room_dup_order", ["alice", "bob", "charlie"])
        alice_id = await self._get_agent_id(team.id, "alice")
        bob_id = await self._get_agent_id(team.id, "bob")
        charlie_id = await self._get_agent_id(team.id, "charlie")

        await roomService.upsert_room(
            team_id=team.id, name="room_a", agent_ids=[alice_id, bob_id, charlie_id],
        )

        with pytest.raises(TogoException) as exc_info:
            await roomService.upsert_room(
                team_id=team.id, name="room_b", agent_ids=[charlie_id, alice_id, bob_id],
            )
        assert exc_info.value.error_code == "ROOM_DUPLICATE"

    async def test_upsert_room_update_self_does_not_raise_duplicate(self):
        """更新已有房间时保持相同成员（通过 room_id），不应误报 ROOM_DUPLICATE。"""
        await self._reset_tables()
        team = await self._setup_team_with_agents("t_room_update_self", ["alice", "bob"])
        alice_id = await self._get_agent_id(team.id, "alice")
        bob_id = await self._get_agent_id(team.id, "bob")

        first = await roomService.upsert_room(
            team_id=team.id, name="room_a", agent_ids=[alice_id, bob_id],
        )

        # 更新同一房间（rename + 修改 topic），保持成员不变，不应报错
        updated = await roomService.upsert_room(
            team_id=team.id, name="room_a_renamed", agent_ids=[alice_id, bob_id],
            initial_topic="new topic", room_id=first.id,
        )
        assert updated.id == first.id
        assert updated.name == "room_a_renamed"

    # ------------------------------------------------------------------
    # upsert_room — DEPT 房间保护 (ROOM_DEPT_PROTECTED)
    # ------------------------------------------------------------------

    async def test_upsert_room_raises_on_dept_room(self):
        """尝试更新带有 DEPT tag 的房间，应报错 ROOM_DEPT_PROTECTED。"""
        await self._reset_tables()
        team = await self._setup_team_with_agents("t_room_dept", ["alice", "bob"])
        alice_id = await self._get_agent_id(team.id, "alice")
        bob_id = await self._get_agent_id(team.id, "bob")

        dept_room = await gtRoomManager.save_room(GtRoom(
            team_id=team.id, name="dept_room", type=RoomType.GROUP,
            initial_topic="", max_rounds=10,
            agent_ids=[alice_id, bob_id], biz_id="DEPT:1", tags=["DEPT"],
        ))

        with pytest.raises(TogoException) as exc_info:
            await roomService.upsert_room(
                team_id=team.id, name="dept_room", agent_ids=[alice_id, bob_id],
                room_id=dept_room.id,
            )
        assert exc_info.value.error_code == "ROOM_DEPT_PROTECTED"

    # ------------------------------------------------------------------
    # delete_managed_room — 正常删除 / DEPT 保护
    # ------------------------------------------------------------------

    async def test_delete_managed_room_removes_room(self):
        """正常删除房间后，数据库中应不再存在该房间。"""
        await self._reset_tables()
        team = await self._setup_team_with_agents("t_room_delete", ["alice", "bob"])
        alice_id = await self._get_agent_id(team.id, "alice")
        bob_id = await self._get_agent_id(team.id, "bob")

        saved = await roomService.upsert_room(
            team_id=team.id, name="to_delete", agent_ids=[alice_id, bob_id],
        )

        await roomService.delete_managed_room(team.id, saved.id)

        assert await gtRoomManager.get_room_by_id(saved.id) is None

    async def test_delete_managed_room_raises_on_dept_room(self):
        """尝试删除带有 DEPT tag 的房间，应报错 ROOM_DEPT_PROTECTED。"""
        await self._reset_tables()
        team = await self._setup_team_with_agents("t_room_delete_dept", ["alice", "bob"])
        alice_id = await self._get_agent_id(team.id, "alice")
        bob_id = await self._get_agent_id(team.id, "bob")

        dept_room = await gtRoomManager.save_room(GtRoom(
            team_id=team.id, name="dept_room", type=RoomType.GROUP,
            initial_topic="", max_rounds=10,
            agent_ids=[alice_id, bob_id], biz_id="DEPT:2", tags=["DEPT"],
        ))

        with pytest.raises(TogoException) as exc_info:
            await roomService.delete_managed_room(team.id, dept_room.id)
        assert exc_info.value.error_code == "ROOM_DEPT_PROTECTED"

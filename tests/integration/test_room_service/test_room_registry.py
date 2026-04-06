import os
import sys

import pytest

import service.ormService as ormService
import service.persistenceService as persistenceService
import service.roomService as roomService
from constants import RoomType, SpecialAgent
from dal.db import gtTeamManager, gtAgentManager, gtRoomMessageManager
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtRoom import GtRoom
from model.dbModel.gtTeam import GtTeam
from service.roomService import ChatRoom
from ...base import ServiceTestCase

TEAM = "test_team"

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")



class TestRoomRegistry(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        db_path = cls._get_test_db_path()
        await ormService.startup(db_path)
        await persistenceService.startup()
        await roomService.startup()

        # 预创建 team，_create_room 不再自动创建
        team = await gtTeamManager.save_team(GtTeam(name=TEAM))
        await gtAgentManager.batch_save_agents(
            team.id,
            [
                GtAgent(team_id=team.id, name="a", role_template_id=0),
                GtAgent(team_id=team.id, name="alice", role_template_id=0),
                GtAgent(team_id=team.id, name="bob", role_template_id=0),
            ],
        )
        cls.agent_ids = {
            agent.name: agent.id
            for agent in await gtAgentManager.get_team_agents(team.id)
        }

    @classmethod
    async def async_teardown_class(cls):
        roomService.shutdown()
        await persistenceService.shutdown()
        await ormService.shutdown()

    async def test_ensure_room_record(self):
        """ensure_room_record 后应可通过 key 获取 ChatRoom 实例。"""
        await roomService.ensure_room_record(TEAM, "myroom", ["alice"])
        key = f"myroom@{TEAM}"
        assert key in roomService._rooms
        assert isinstance(roomService.get_room_by_key(key), ChatRoom)

    async def test_close_all(self):
        """shutdown 会清空全局 rooms 注册表。"""
        await roomService.ensure_room_record(TEAM, "tmp", ["a"])
        roomService.shutdown()
        assert len(roomService._rooms) == 0

    async def test_setup_agents(self):
        """get_agent_names 返回创建时配置的参与者顺序。"""
        await roomService.ensure_room_record(TEAM, "r1", ["alice", "bob"])
        room = roomService.get_room_by_key(f"r1@{TEAM}")
        assert roomService.get_agent_names(room.room_id) == ["alice", "bob"]

    async def test_get_rooms_for_agent(self):
        """按 agent 过滤房间时，只返回该 agent 参与的 room_id 列表。"""
        await roomService.ensure_room_record(TEAM, "r1", ["alice"])
        await roomService.ensure_room_record(TEAM, "r2", ["bob"])
        await roomService.ensure_room_record(TEAM, "r3", ["alice", "bob"])
        r1 = roomService.get_room_by_key(f"r1@{TEAM}")
        r2 = roomService.get_room_by_key(f"r2@{TEAM}")
        r3 = roomService.get_room_by_key(f"r3@{TEAM}")

        assert roomService.get_rooms_for_agent(r1.team_id, self.agent_ids["alice"]) == [r1.room_id, r3.room_id]
        assert roomService.get_rooms_for_agent(r1.team_id, self.agent_ids["bob"]) == [r2.room_id, r3.room_id]

    async def test_create_rooms_keeps_empty_history_before_activation(self):
        """批量建房路径在激活前不应预先塞入初始化消息。"""
        team = await gtTeamManager.get_team(TEAM)
        assert team is not None
        agent_ids = list(map(
            lambda agent: agent.id,
            await gtAgentManager.get_team_agents_by_names(team.id, ["alice"], include_special=True),
        ))
        await roomService.overwrite_team_rooms(team.id, [
            GtRoom(
                team_id=team.id,
                name="boot_room",
                type=RoomType.GROUP,
                initial_topic="boot topic",
                max_turns=5,
                agent_ids=agent_ids,
                biz_id=None,
                tags=[],
            ),
        ])
        await roomService.refresh_rooms_for_team(team.id)

        room = roomService.get_room_by_key(f"boot_room@{TEAM}")
        assert room.messages == []

        await room.activate_scheduling()

        assert len(room.messages) == 1
        assert room.messages[0].sender_id == room.SYSTEM_MEMBER_ID
        assert "boot topic" in room.messages[0].content

    async def test_restore_state_for_team_prevents_duplicate_initial_messages_after_refresh(self):
        """刷新 Team 房间运行态后，恢复历史再激活，不应重复写初始消息。"""
        team = await gtTeamManager.get_team(TEAM)
        assert team is not None

        await roomService.ensure_room_record(TEAM, "restore_safe_room", ["alice", "bob"])
        room = roomService.get_room_by_key(f"restore_safe_room@{TEAM}")

        await room.activate_scheduling()
        rows = await gtRoomMessageManager.get_room_messages(room.room_id)
        assert len(rows) == 1
        assert "房间已经创建" in rows[0].content

        await roomService.refresh_rooms_for_team(team.id)
        await roomService.restore_state_for_team(team.id)
        await roomService.activate_rooms(TEAM)

        reloaded_room = roomService.get_room_by_key(f"restore_safe_room@{TEAM}")
        assert len(reloaded_room.messages) == 1

        rows = await gtRoomMessageManager.get_room_messages(reloaded_room.room_id)
        assert len(rows) == 1
        assert "房间已经创建" in rows[0].content

    async def test_special_agent_ids(self):
        """SYSTEM 和 OPERATOR 应有特殊的 agent_id。"""
        await roomService.ensure_room_record(TEAM, "special_room", ["Operator", "alice"])
        room = roomService.get_room_by_key(f"special_room@{TEAM}")

        assert room.get_agent_id_by_name(SpecialAgent.SYSTEM.name) == ChatRoom.SYSTEM_MEMBER_ID
        assert room.get_agent_id_by_name(SpecialAgent.OPERATOR.name) == ChatRoom.OPERATOR_MEMBER_ID
        assert room.get_agent_id_by_name("alice") == self.agent_ids["alice"]
        assert room.get_agent_id_by_name("unknown") is None

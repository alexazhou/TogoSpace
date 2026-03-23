import os
import sys

import pytest

import service.roomService as roomService
from dal.db import gtTeamManager
from service.roomService import ChatRoom
from ...base import ServiceTestCase

TEAM = "test_team"

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


@pytest.mark.forked
class TestRoomRegistry(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        await roomService.startup()

    async def test_create_room(self):
        """create_room 后应可通过 key 获取 ChatRoom 实例。"""
        await roomService.create_room(TEAM, "myroom", ["alice"])
        key = f"myroom@{TEAM}"
        assert key in roomService._rooms
        assert isinstance(roomService.get_room_by_key(key), ChatRoom)

    async def test_close_all(self):
        """shutdown 会清空全局 rooms 注册表。"""
        await roomService.create_room(TEAM, "tmp", ["a"])
        roomService.shutdown()
        assert len(roomService._rooms) == 0

    async def test_setup_members(self):
        """get_member_names 返回创建时配置的成员顺序。"""
        await roomService.create_room(TEAM, "r1", ["alice", "bob"])
        room = roomService.get_room_by_key(f"r1@{TEAM}")
        assert roomService.get_member_names(room.room_id) == ["alice", "bob"]

    async def test_get_rooms_for_agent(self):
        """按 agent 过滤房间时，只返回该 agent 参与的 room_id 列表。"""
        await roomService.create_room(TEAM, "r1", ["alice"])
        await roomService.create_room(TEAM, "r2", ["bob"])
        await roomService.create_room(TEAM, "r3", ["alice", "bob"])
        team = await gtTeamManager.get_team(TEAM)
        assert team is not None
        r1 = roomService.get_room_by_key(f"r1@{TEAM}")
        r2 = roomService.get_room_by_key(f"r2@{TEAM}")
        r3 = roomService.get_room_by_key(f"r3@{TEAM}")

        assert roomService.get_rooms_for_agent(team.id, "alice") == [r1.room_id, r3.room_id]
        assert roomService.get_rooms_for_agent(team.id, "bob") == [r2.room_id, r3.room_id]

    async def test_create_rooms_always_emits_initial_message(self):
        """批量建房路径应始终生成初始化消息，供后续恢复逻辑覆盖。"""
        teams_config = [{
            "name": TEAM,
            "groups": [{
                "name": "boot_room",
                "type": "group",
                "members": ["alice"],
                "initial_topic": "boot topic",
                "max_turns": 5,
            }],
        }]

        await roomService.create_rooms(teams_config)

        room = roomService.get_room_by_key(f"boot_room@{TEAM}")
        assert len(room.messages) == 1
        assert room.messages[0].sender_name == "system"
        assert "boot topic" in room.messages[0].content

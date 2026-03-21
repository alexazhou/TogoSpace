import os
import sys

import pytest

import service.room_service as room_service
from service.room_service import ChatRoom
from ...base import ServiceTestCase

TEAM = "test_team"

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


@pytest.mark.forked
class TestRoomRegistry(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        await room_service.startup()

    async def test_create_room(self):
        """create_room 后应可通过 key 获取 ChatRoom 实例。"""
        await room_service.create_room(TEAM, "myroom", ["alice"])
        key = f"myroom@{TEAM}"
        assert key in room_service._rooms
        assert isinstance(room_service.get_room(key), ChatRoom)

    async def test_close_all(self):
        """shutdown 会清空全局 rooms 注册表。"""
        await room_service.create_room(TEAM, "tmp", ["a"])
        room_service.shutdown()
        assert len(room_service._rooms) == 0

    async def test_setup_members(self):
        """get_member_names 返回创建时配置的成员顺序。"""
        await room_service.create_room(TEAM, "r1", ["alice", "bob"])
        assert room_service.get_member_names(TEAM, "r1") == ["alice", "bob"]

    async def test_get_rooms_for_agent(self):
        """按 agent 过滤房间时，只返回该 agent 参与的 room_key 列表。"""
        await room_service.create_room(TEAM, "r1", ["alice"])
        await room_service.create_room(TEAM, "r2", ["bob"])
        await room_service.create_room(TEAM, "r3", ["alice", "bob"])

        assert room_service.get_rooms_for_agent(TEAM, "alice") == [f"r1@{TEAM}", f"r3@{TEAM}"]
        assert room_service.get_rooms_for_agent(TEAM, "bob") == [f"r2@{TEAM}", f"r3@{TEAM}"]

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

        await room_service.create_rooms(teams_config)

        room = room_service.get_room(f"boot_room@{TEAM}")
        assert len(room.messages) == 1
        assert room.messages[0].sender_name == "system"
        assert "boot topic" in room.messages[0].content

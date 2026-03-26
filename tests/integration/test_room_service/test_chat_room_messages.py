import os
import sys
from unittest.mock import patch

import pytest

import service.ormService as ormService
import service.persistenceService as persistenceService
import service.roomService as roomService
from constants import MessageBusTopic
from dal.db import gtTeamManager
from util.configTypes import TeamConfig
from ...base import ServiceTestCase

TEAM = "test_team"

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


@pytest.mark.forked
class TestChatRoomMessages(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        db_path = cls.TEST_DB_PATH
        await ormService.startup(db_path)
        await persistenceService.startup()
        await roomService.startup()

        # 预创建 team，_create_room 不再自动创建
        await gtTeamManager.upsert_team(TeamConfig(name=TEAM, members=[], preset_rooms=[]))

    @classmethod
    async def async_teardown_class(cls):
        roomService.shutdown()
        await persistenceService.shutdown()
        await ormService.shutdown()

    async def test_add_message(self):
        """add_message 会追加消息并发布 ROOM_MSG_ADDED 事件。"""
        await roomService.create_room(TEAM, "test_room", ["alice"])
        room = roomService.get_room_by_key(f"test_room@{TEAM}")
        room.activate_scheduling()
        with patch("service.messageBus.publish") as mock_publish:
            await room.add_message("alice", "hello")
            assert len(room.messages) == 2
            assert room.messages[1].sender_name == "alice"
            assert room.messages[1].content == "hello"
            mock_publish.assert_any_call(
                MessageBusTopic.ROOM_MSG_ADDED,
                room_id=room.room_id,
                room_name="test_room",
                room_key=f"test_room@{TEAM}",
                team_name=TEAM,
                sender="alice",
                content="hello",
                time=room.messages[1].send_time.isoformat(),
            )

    async def test_get_unread_messages_initial(self):
        """首次拉取未读应拿到系统初始化公告。"""
        await roomService.create_room(TEAM, "test_room", ["alice"])
        room = roomService.get_room_by_key(f"test_room@{TEAM}")
        msgs = await room.get_unread_messages("alice")
        assert len(msgs) == 1
        assert "房间已经创建" in msgs[0].content

    async def test_get_unread_messages_advances_index(self):
        """读取未读会推进游标，重复读取不应返回旧消息。"""
        await roomService.create_room(TEAM, "test_room", ["alice"])
        room = roomService.get_room_by_key(f"test_room@{TEAM}")
        await room.get_unread_messages("alice")
        await room.add_message("bob", "msg1")
        msgs = await room.get_unread_messages("alice")
        assert len(msgs) == 1
        assert msgs[0].content == "msg1"

        msgs2 = await room.get_unread_messages("alice")
        assert len(msgs2) == 0

    async def test_get_unread_messages_independent_per_agent(self):
        """不同 agent 的未读游标互相独立。"""
        await roomService.create_room(TEAM, "test_room", ["alice"])
        room = roomService.get_room_by_key(f"test_room@{TEAM}")
        await room.get_unread_messages("alice")
        await room.get_unread_messages("bob")
        await room.add_message("char", "hi")
        assert len(await room.get_unread_messages("alice")) == 1
        assert len(await room.get_unread_messages("bob")) == 1

    async def test_format_log(self):
        """format_log 输出包含房间标题与消息发送者。"""
        await roomService.create_room(TEAM, "test_room", ["alice"])
        room = roomService.get_room_by_key(f"test_room@{TEAM}")
        log = room.format_log()
        assert f"=== test_room@{TEAM} 聊天记录 ===" in log
        assert "SYSTEM" in log

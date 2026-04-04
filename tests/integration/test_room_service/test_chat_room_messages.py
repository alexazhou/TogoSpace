import os
import sys
from unittest.mock import patch

import pytest

import service.ormService as ormService
import service.persistenceService as persistenceService
import service.roomService as roomService
from constants import MessageBusTopic
from dal.db import gtTeamManager, gtRoomMessageManager, gtAgentManager
from exception import TeamAgentException
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtTeam import GtTeam
from ...base import ServiceTestCase

TEAM = "test_team"

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")



class TestChatRoomMessages(ServiceTestCase):
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
                GtAgent(team_id=team.id, name="alice", role_template_id=0),
                GtAgent(team_id=team.id, name="bob", role_template_id=0),
                GtAgent(team_id=team.id, name="char", role_template_id=0),
            ],
        )

    @classmethod
    async def async_teardown_class(cls):
        roomService.shutdown()
        await persistenceService.shutdown()
        await ormService.shutdown()

    async def test_add_message(self):
        """add_message 会追加消息并发布 ROOM_MSG_ADDED 事件。"""
        await roomService.ensure_room_record(TEAM, "test_room", ["alice"])
        room = roomService.get_room_by_key(f"test_room@{TEAM}")
        await room.activate_scheduling()
        alice_id = room.get_agent_id_by_name("alice")
        with patch("service.messageBus.publish") as mock_publish:
            await room.add_message(alice_id, "hello")
            assert len(room.messages) == 2
            assert room.messages[1].sender_id == alice_id
            assert room.messages[1].content == "hello"
            mock_publish.assert_any_call(
                MessageBusTopic.ROOM_MSG_ADDED,
                gt_room=room.gt_room,
                sender_id=alice_id,
                content="hello",
                time=room.messages[1].send_time.isoformat(),
            )

    async def test_get_unread_messages_initial(self):
        """首次拉取未读应拿到系统初始化公告。"""
        await roomService.ensure_room_record(TEAM, "test_room", ["alice"])
        room = roomService.get_room_by_key(f"test_room@{TEAM}")
        await room.activate_scheduling()
        alice_id = room.get_agent_id_by_name("alice")
        msgs = await room.get_unread_messages(alice_id)
        assert len(msgs) == 1
        assert "房间已经创建" in msgs[0].content

    async def test_get_unread_messages_advances_index(self):
        """读取未读会推进游标，重复读取不应返回旧消息。"""
        await roomService.ensure_room_record(TEAM, "test_room", ["alice", "bob"])
        room = roomService.get_room_by_key(f"test_room@{TEAM}")
        await room.activate_scheduling()
        alice_id = room.get_agent_id_by_name("alice")
        bob_id = room.get_agent_id_by_name("bob")
        await room.get_unread_messages(alice_id)
        await room.add_message(bob_id, "msg1")
        msgs = await room.get_unread_messages(alice_id)
        assert len(msgs) == 1
        assert msgs[0].content == "msg1"

        msgs2 = await room.get_unread_messages(alice_id)
        assert len(msgs2) == 0

    async def test_get_unread_messages_independent_per_agent(self):
        """不同 agent 的未读游标互相独立。"""
        await roomService.ensure_room_record(TEAM, "test_room", ["alice", "bob", "char"])
        room = roomService.get_room_by_key(f"test_room@{TEAM}")
        await room.activate_scheduling()
        alice_id = room.get_agent_id_by_name("alice")
        bob_id = room.get_agent_id_by_name("bob")
        char_id = room.get_agent_id_by_name("char")
        await room.get_unread_messages(alice_id)
        await room.get_unread_messages(bob_id)
        await room.add_message(char_id, "hi")
        assert len(await room.get_unread_messages(alice_id)) == 1
        assert len(await room.get_unread_messages(bob_id)) == 1

    async def test_add_message_rejects_non_member(self):
        """非房间成员写消息时应被拒绝。"""
        await roomService.ensure_room_record(TEAM, "restricted_room", ["alice"])
        room = roomService.get_room_by_key(f"restricted_room@{TEAM}")
        await room.activate_scheduling()
        # bob 不在房间中，使用一个不存在的 agent_id
        with pytest.raises(TeamAgentException):
            await room.add_message(99999, "hello")

    async def test_format_log(self):
        """format_log 输出包含房间标题与消息发送者。"""
        await roomService.ensure_room_record(TEAM, "test_room", ["alice"])
        room = roomService.get_room_by_key(f"test_room@{TEAM}")
        await room.activate_scheduling()
        log = room.format_log()
        assert f"=== test_room@{TEAM} 聊天记录 ===" in log
        assert "SYSTEM" in log

    async def test_activate_scheduling_persists_initial_message(self):
        """首次激活调度时生成的初始化消息应像普通消息一样落库。"""
        await roomService.ensure_room_record(TEAM, "persist_init_room", ["alice"])
        room = roomService.get_room_by_key(f"persist_init_room@{TEAM}")

        assert room.messages == []

        await room.activate_scheduling()

        rows = await gtRoomMessageManager.get_room_messages(room.room_id)
        assert len(rows) == 1
        assert rows[0].agent_id == room.SYSTEM_MEMBER_ID
        assert "房间已经创建" in rows[0].content

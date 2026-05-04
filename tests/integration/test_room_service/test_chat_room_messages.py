import os
import sys
from unittest.mock import patch

import pytest

import service.ormService as ormService
import service.persistenceService as persistenceService
import service.roomService as roomService
import service.agentService as agentService
from service import presetService
from constants import MessageBusTopic, RoomType
from dal.db import gtTeamManager, gtRoomMessageManager, gtAgentManager
from exception import TogoException
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
        await agentService.startup()  # 确保 SpecialAgent 记录存在
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
        cls.team_id = team.id

    @classmethod
    async def async_teardown_class(cls):
        roomService.shutdown()
        await agentService.shutdown()
        await persistenceService.shutdown()
        await ormService.shutdown()

    async def _get_agent_id(self, name: str) -> int | None:
        gt_agent = await gtAgentManager.get_agent(self.team_id, name)
        return gt_agent.id if gt_agent else None

    async def test_add_message(self):
        """add_message 会追加消息并发布 ROOM_MSG_ADDED 事件。"""
        await self.create_room(TEAM, "test_room", ["alice"])
        room = roomService.get_room_by_key(f"test_room@{TEAM}")
        await room.activate_scheduling()
        alice_id = await self._get_agent_id("alice")
        with patch("service.messageBus.publish") as mock_publish:
            await room.add_message(alice_id, "hello")
            assert len(room.messages) == 2
            assert room.messages[1].sender_id == alice_id
            assert room.messages[1].content == "hello"
            mock_publish.assert_any_call(
                MessageBusTopic.ROOM_MSG_ADDED,
                gt_room=room.gt_room,
                gt_message=room.messages[1],
            )

    async def test_get_unread_messages_initial(self):
        """首次拉取未读应拿到系统初始化公告。"""
        await self.create_room(TEAM, "test_room", ["alice"])
        room = roomService.get_room_by_key(f"test_room@{TEAM}")
        await room.activate_scheduling()
        alice_id = await self._get_agent_id("alice")
        msgs = await room.get_unread_messages(alice_id)
        assert len(msgs) == 1
        assert "房间已经创建" in msgs[0].content

    async def test_get_unread_messages_advances_index(self):
        """读取未读会推进游标，重复读取不应返回旧消息。"""
        await self.create_room(TEAM, "test_room", ["alice", "bob"])
        room = roomService.get_room_by_key(f"test_room@{TEAM}")
        await room.activate_scheduling()
        alice_id = await self._get_agent_id("alice")
        bob_id = await self._get_agent_id("bob")
        await room.get_unread_messages(alice_id)
        await room.add_message(bob_id, "msg1")
        msgs = await room.get_unread_messages(alice_id)
        assert len(msgs) == 1
        assert msgs[0].content == "msg1"

        msgs2 = await room.get_unread_messages(alice_id)
        assert len(msgs2) == 0

    async def test_get_unread_messages_independent_per_agent(self):
        """不同 agent 的未读游标互相独立。"""
        await self.create_room(TEAM, "test_room", ["alice", "bob", "char"])
        room = roomService.get_room_by_key(f"test_room@{TEAM}")
        await room.activate_scheduling()
        alice_id = await self._get_agent_id("alice")
        bob_id = await self._get_agent_id("bob")
        char_id = await self._get_agent_id("char")
        await room.get_unread_messages(alice_id)
        await room.get_unread_messages(bob_id)
        await room.add_message(char_id, "hi")
        assert len(await room.get_unread_messages(alice_id)) == 1
        assert len(await room.get_unread_messages(bob_id)) == 1

    async def test_add_message_rejects_non_member(self):
        """非房间成员写消息时应被拒绝。"""
        await self.create_room(TEAM, "restricted_room", ["alice"])
        room = roomService.get_room_by_key(f"restricted_room@{TEAM}")
        await room.activate_scheduling()
        # bob 不在房间中，使用一个不存在的 agent_id
        with pytest.raises(TogoException):
            await room.add_message(99999, "hello")

    async def test_format_log(self):
        """format_log 输出包含房间标题与消息发送者。"""
        await self.create_room(TEAM, "test_room", ["alice"])
        room = roomService.get_room_by_key(f"test_room@{TEAM}")
        await room.activate_scheduling()
        log = room.format_log()
        assert f"=== test_room@{TEAM} 聊天记录 ===" in log
        # SYSTEM agent 的 display_name 根据语言可能是"系统提醒"或"SYSTEM"
        assert ("系统提醒" in log or "SYSTEM" in log)

    async def test_activate_scheduling_persists_initial_message(self):
        """首次激活调度时生成的初始化消息应像普通消息一样落库。"""
        await self.create_room(TEAM, "persist_init_room", ["alice"])
        room = roomService.get_room_by_key(f"persist_init_room@{TEAM}")

        assert room.messages == []

        await room.activate_scheduling()

        rows = await gtRoomMessageManager.get_room_messages(room.room_id)
        assert len(rows) == 1
        assert rows[0].agent_id == room.SYSTEM_MEMBER_ID
        assert "房间已经创建" in rows[0].content

    async def test_add_message_insert_immediately_goes_to_pending_queue(self):
        """insert_immediately=True 的消息应进入 pending inject 队列，不进主消息列表。"""
        await self.create_room(TEAM, "imm_flag_room", ["alice", "bob"], max_turns=10, room_type=RoomType.PRIVATE)
        room = roomService.get_room_by_key(f"imm_flag_room@{TEAM}")
        await room.activate_scheduling()
        alice_id = await self._get_agent_id("alice")

        await room.add_message(alice_id, "普通消息")
        normal_count = len(room.messages)

        await room.add_message(alice_id, "即时消息", insert_immediately=True)
        # immediately 消息进入 pending 队列，主列表长度不变
        assert len(room.messages) == normal_count
        assert len(room._store.pending_messages) == 1
        assert room._store.pending_messages[0].insert_immediately is True

    async def test_insert_immediately_persisted_to_db_with_null_seq(self):
        """insert_immediately=True 应持久化到 DB，seq 初始为 NULL（等待注入时赋值）。"""
        await self.create_room(TEAM, "imm_db_room", ["alice", "bob"], max_turns=10, room_type=RoomType.PRIVATE)
        room = roomService.get_room_by_key(f"imm_db_room@{TEAM}")
        await room.activate_scheduling()
        alice_id = await self._get_agent_id("alice")

        await room.add_message(alice_id, "普通消息")
        await room.add_message(alice_id, "即时消息", insert_immediately=True)

        rows = await gtRoomMessageManager.get_room_messages(room.room_id)
        # rows[-1] 是即时消息（seq=NULL，排在最后）
        imm_row = next((r for r in rows if r.insert_immediately), None)
        assert imm_row is not None
        assert imm_row.insert_immediately is True
        assert imm_row.seq is None

    async def test_has_pending_immediate_messages_true_when_in_queue(self):
        """immediately 消息进入 pending 队列时 has_pending_immediate_messages 应返回 True。"""
        await self.create_room(TEAM, "imm_pending_room", ["alice", "bob"], max_turns=10, room_type=RoomType.PRIVATE)
        room = roomService.get_room_by_key(f"imm_pending_room@{TEAM}")
        await room.activate_scheduling()
        alice_id = await self._get_agent_id("alice")
        bob_id = await self._get_agent_id("bob")

        await room.add_message(alice_id, "即时消息", insert_immediately=True)
        assert room.has_pending_immediate_messages(bob_id) is True

    async def test_has_pending_immediate_messages_false_for_regular_message(self):
        """普通消息不应触发 has_pending_immediate_messages。"""
        await self.create_room(TEAM, "imm_regular_room", ["alice", "bob"])
        room = roomService.get_room_by_key(f"imm_regular_room@{TEAM}")
        await room.activate_scheduling()
        alice_id = await self._get_agent_id("alice")
        bob_id = await self._get_agent_id("bob")

        await room.add_message(alice_id, "普通消息")
        assert room.has_pending_immediate_messages(bob_id) is False

    async def test_flush_pending_assigns_seq_and_appears_in_stream(self):
        """flush_pending_immediate_messages 后消息进入主流，seq 被赋值，DB 更新。"""
        await self.create_room(TEAM, "imm_flush_room", ["alice", "bob"], max_turns=10, room_type=RoomType.PRIVATE)
        room = roomService.get_room_by_key(f"imm_flush_room@{TEAM}")
        await room.activate_scheduling()
        alice_id = await self._get_agent_id("alice")
        bob_id = await self._get_agent_id("bob")

        # 先消费初始化消息，确保 read_idx 同步
        await room.get_unread_messages(bob_id)

        await room.add_message(alice_id, "即时消息", insert_immediately=True)
        assert room.has_pending_immediate_messages(bob_id) is True

        await room.flush_pending_immediate_messages()

        # flush 后 pending 队列清空
        assert room.has_pending_immediate_messages(bob_id) is False

        # 消息进入主流，bob 可以读到
        unread = await room.get_unread_messages(bob_id)
        assert len(unread) == 1
        imm_msg = unread[0]
        assert imm_msg.insert_immediately is True
        assert imm_msg.seq is not None  # seq 已赋值

        # DB 中 seq 也已更新
        rows = await gtRoomMessageManager.get_room_messages(room.room_id)
        imm_row = next((r for r in rows if r.insert_immediately), None)
        assert imm_row is not None
        assert imm_row.seq == imm_msg.seq


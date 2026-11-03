import os
import sys
from unittest.mock import patch

import pytest

import service.ormService as ormService
import service.persistenceService as persistenceService
import service.roomService as roomService
import service.agentService as agentService
from constants import MessageBusTopic, RoomType, RoomState
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

    async def test_new_member_only_receives_messages_after_join(self):
        """新加入房间的成员只应收到加入后的消息，不应回灌历史消息。"""
        await self.create_room(TEAM, "join_room", ["alice", "bob"])
        room = roomService.get_room_by_key(f"join_room@{TEAM}")
        await room.activate_scheduling()

        alice_id = await self._get_agent_id("alice")
        bob_id = await self._get_agent_id("bob")
        char_id = await self._get_agent_id("char")

        await room.get_unread_messages(alice_id)
        await room.get_unread_messages(bob_id)
        await room.add_message(bob_id, "before join")

        await roomService.update_room_agents(room.room_id, [alice_id, bob_id, char_id])
        await roomService.close_team_rooms(self.team_id)
        await roomService.load_team_rooms(self.team_id)
        await roomService.restore_team_rooms_runtime_state(self.team_id)

        reloaded_room = roomService.get_room_by_key(f"join_room@{TEAM}")
        assert await reloaded_room.get_unread_messages(char_id) == []

        await reloaded_room.add_message(alice_id, "after join")
        char_unread = await reloaded_room.get_unread_messages(char_id)

        assert [message.content for message in char_unread] == ["after join"]

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

        rows, _ = await gtRoomMessageManager.get_room_messages(room.room_id)
        assert len(rows) == 1
        assert rows[0].sender_id == room.SYSTEM_MEMBER_ID
        assert "房间已经创建" in rows[0].content


class TestImmediateMessages(ServiceTestCase):
    """insert_immediately 的语义：只控制安全边界触发，不参与 seq 与可见性。

    旧实现下 immediate 消息先进 pending 队列（seq=NULL），需 flush 才可见；
    新实现写入即分配 seq、立即可见，标志位仅决定「要不要提前收消息」。
    """

    @classmethod
    async def async_setup_class(cls):
        db_path = cls._get_test_db_path()
        await ormService.startup(db_path)
        await persistenceService.startup()
        await agentService.startup()
        await roomService.startup()

        team = await gtTeamManager.save_team(GtTeam(name=TEAM))
        await gtAgentManager.batch_save_agents(
            team.id,
            [
                GtAgent(team_id=team.id, name="alice", role_template_id=0),
                GtAgent(team_id=team.id, name="bob", role_template_id=0),
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

    async def _prepare_room(self, name: str, agents: list[str], **kwargs):
        await self.create_room(TEAM, name, agents, **kwargs)
        room = roomService.get_room_by_key(f"{name}@{TEAM}")
        await room.activate_scheduling()
        return room

    async def test_immediate_message_enters_stream_immediately(self):
        """immediate 消息不做队列中转：写入即在主流里，且已分配 seq。"""
        room = await self._prepare_room("imm_stream_room", ["alice", "bob"],
                                        max_rounds=10, room_type=RoomType.PRIVATE)
        alice_id = await self._get_agent_id("alice")
        before = len(room.messages)

        await room.add_message(alice_id, "即时消息", insert_immediately=True)

        assert len(room.messages) == before + 1
        last = room.messages[-1]
        assert last.insert_immediately is True
        assert last.seq is not None
        assert last.seq == before  # 头部是 seq=0 的初始化消息，故等于当前长度

    async def test_immediate_message_persisted_with_seq(self):
        """DB 中不再存 seq=NULL：immediate 消息落库时就带 seq。"""
        room = await self._prepare_room("imm_db_room", ["alice", "bob"],
                                        max_rounds=10, room_type=RoomType.PRIVATE)
        alice_id = await self._get_agent_id("alice")

        await room.add_message(alice_id, "普通消息")
        await room.add_message(alice_id, "即时消息", insert_immediately=True)

        in_memory = room.messages[-1]
        rows, _ = await gtRoomMessageManager.get_room_messages(room.room_id)
        imm_row = next(r for r in rows if r.insert_immediately)
        assert imm_row.seq is not None
        assert imm_row.seq == in_memory.seq
        assert [r.seq for r in rows] == sorted(r.seq for r in rows)  # 无空洞/乱序

    async def test_immediate_message_readable_in_write_order(self):
        """读到的顺序就是写入顺序：升级/立即注入不插队。"""
        room = await self._prepare_room("imm_order_room", ["alice", "bob"],
                                        max_rounds=10, room_type=RoomType.PRIVATE)
        alice_id = await self._get_agent_id("alice")
        bob_id = await self._get_agent_id("bob")
        await room.get_unread_messages(bob_id)  # 消费初始化消息

        await room.add_message(alice_id, "第一条", insert_immediately=True)
        await room.add_message(alice_id, "第二条")

        unread = await room.get_unread_messages(bob_id)
        assert [m.content for m in unread] == ["第一条", "第二条"]
        assert [m.seq for m in unread] == sorted(m.seq for m in unread)

    async def test_has_unread_immediate_messages_true_for_other_agent(self):
        room = await self._prepare_room("imm_unread_room", ["alice", "bob"],
                                        max_rounds=10, room_type=RoomType.PRIVATE)
        alice_id = await self._get_agent_id("alice")
        bob_id = await self._get_agent_id("bob")

        await room.add_message(alice_id, "即时消息", insert_immediately=True)
        assert room.has_unread_immediate_messages(bob_id) is True
        # 自己发的不算未读
        assert room.has_unread_immediate_messages(alice_id) is False

    async def test_has_unread_immediate_messages_false_for_regular_message(self):
        room = await self._prepare_room("imm_regular_room", ["alice", "bob"])
        alice_id = await self._get_agent_id("alice")
        bob_id = await self._get_agent_id("bob")

        await room.add_message(alice_id, "普通消息")
        assert room.has_unread_immediate_messages(bob_id) is False

    async def test_has_unread_immediate_messages_false_after_read(self):
        room = await self._prepare_room("imm_read_room", ["alice", "bob"],
                                        max_rounds=10, room_type=RoomType.PRIVATE)
        alice_id = await self._get_agent_id("alice")
        bob_id = await self._get_agent_id("bob")

        await room.add_message(alice_id, "即时消息", insert_immediately=True)
        await room.get_unread_messages(bob_id)
        assert room.has_unread_immediate_messages(bob_id) is False

    async def test_immediate_downgraded_in_group_room(self):
        """非私聊房间不支持立即注入，降级为普通消息（标志位不落库）。"""
        room = await self._prepare_room("imm_group_room", ["alice", "bob"])
        alice_id = await self._get_agent_id("alice")

        await room.add_message(alice_id, "即时消息", insert_immediately=True)
        assert room.messages[-1].insert_immediately is False

        rows, _ = await gtRoomMessageManager.get_room_messages(room.room_id)
        assert all(r.insert_immediately is False for r in rows)

    async def test_immediate_not_downgraded_when_room_idle(self):
        """回归：房间 IDLE 时 immediate 消息不能被降级（督导指令立即生效的根因）。"""
        room = await self._prepare_room("imm_idle_room", ["alice", "bob"],
                                        max_rounds=10, room_type=RoomType.PRIVATE)
        alice_id = await self._get_agent_id("alice")
        bob_id = await self._get_agent_id("bob")

        # 私聊：两名 AI 都「无内容结束」→ 全部进入 skipped 集合 → 房间转入 IDLE
        with patch("service.messageBus.publish"):
            assert await room.handle_finish_request(alice_id) is True
            assert await room.handle_finish_request(bob_id) is True

        assert room.state == RoomState.IDLE

        with patch("service.messageBus.publish"):
            await room.add_message(alice_id, "督导指令", insert_immediately=True)

        assert room.messages[-1].insert_immediately is True
        assert room.state == RoomState.SCHEDULING  # 立即唤醒，不需等下一次有人说话

    async def test_escalate_keeps_seq_and_order(self):
        """升级只置标志位：seq 不变、顺序不变，DB 同步更新。"""
        room = await self._prepare_room("imm_escalate_room", ["alice", "OPERATOR"],
                                        max_rounds=10, room_type=RoomType.PRIVATE)
        alice_id = await self._get_agent_id("alice")
        await room.get_unread_messages(alice_id)  # 消费初始化消息

        await room.add_message(room.OPERATOR_MEMBER_ID, "排队消息")
        target = room.messages[-1]
        original_seq = target.seq
        assert target.insert_immediately is False

        await room.escalate_message_to_immediate(target.id)

        assert target.seq == original_seq
        assert target.insert_immediately is True
        assert room.messages[-1] is target

        rows, _ = await gtRoomMessageManager.get_room_messages(room.room_id)
        row = next(r for r in rows if r.id == target.id)
        assert row.insert_immediately is True
        assert row.seq == original_seq

        # 升级后依然按原位置被读到
        unread = await room.get_unread_messages(alice_id)
        assert [m.id for m in unread] == [target.id]
        assert unread[0].insert_immediately is True

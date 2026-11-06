"""房间运行时状态装载守卫：`_runtime_state_ready` 标记与 `load_room_with_state` 入口。

对应线上 bug：团队停用期间点开某个 Agent 单聊 → `get_or_create_control_room` 发现
内存里没有该房间 → 只走「造壳」不灌状态 → 内存里 `_messages=[]`、`_next_seq=0`，
与「全新空房间」无法区分 → `activate_scheduling` 把它当新房间，往一个已有上千条
历史消息的房间补写了一条 seq=0 的系统消息（生产库因此出现重复 seq=0）。

本文件只验「不装载就不许写」这条不变量与 `load_room_with_state` 这条唯一入口，
不重复验证既有消息/游标语义。
"""
import os
import sys
from datetime import datetime

import pytest

import service.agentService as agentService
import service.ormService as ormService
import service.persistenceService as persistenceService
import service.roomService as roomService
from constants import RoomType, SpecialAgent
from dal.db import gtAgentManager, gtRoomMessageManager, gtRoomManager, gtTeamManager
from exception import TogoException
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtRoom import GtRoom
from model.dbModel.gtTeam import GtTeam
from service.roomService import ChatRoom
from ...base import ServiceTestCase

TEAM = "test_room_state_ready"

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


async def _all_rows(room_id: int) -> list:
    rows, _ = await gtRoomMessageManager.get_room_messages(room_id)
    return rows


async def _zero_seq_system_count(room_id: int) -> int:
    """按 `room_id + sender_id=-2 + seq=0` 计数（初始系统消息的指纹）。"""
    return sum(
        1 for row in await _all_rows(room_id)
        if row.seq == 0 and row.sender_id == ChatRoom.SYSTEM_MEMBER_ID
    )


class TestRoomRuntimeStateReady(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        await ormService.startup(cls._get_test_db_path())
        await persistenceService.startup()
        await agentService.startup()  # 确保 SpecialAgent 记录存在
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

    async def _agent_id(self, name: str) -> int:
        gt_agent = await gtAgentManager.get_agent(self.team_id, name)
        assert gt_agent is not None, f"agent '{name}' 不存在"
        return gt_agent.id

    async def _seed_history_room(self, name: str, agent_names: list[str]):
        """建一个已有历史消息的房间，返回 (gt_room, 房间内已有消息数)。"""
        await self.create_room(TEAM, name, agent_names)
        room = roomService.get_room_by_key(f"{name}@{TEAM}")
        await room.activate_scheduling()  # 写入 seq=0 的初始系统消息
        await room.add_message(await self._agent_id(agent_names[0]), "历史一")
        await room.add_message(await self._agent_id(agent_names[1]), "历史二")
        gt_room = await gtRoomManager.get_room_by_id(room.room_id)
        assert gt_room is not None
        return gt_room, len(room.messages)

    # ─── 反向：只造壳不 restore → 一切写入/激活都必须被拦住 ─────────────

    async def test_shell_without_restore_rejects_activate_and_append(self):
        """DB 有历史的房间，只造壳不 restore：激活与写入都抛 `room_state_not_ready`。"""
        gt_room, seeded_count = await self._seed_history_room("shell_room", ["alice", "bob"])
        rooms_before, _ = await gtRoomMessageManager.get_room_messages(gt_room.id)
        assert len(rooms_before) == seeded_count == 3
        zero_seq_before = await _zero_seq_system_count(gt_room.id)
        assert zero_seq_before == 1

        # 故意绕开 load_room_with_state，模仿线上「裸 _load_room」造出来的壳
        shell = ChatRoom(
            team=await gtTeamManager.get_team_by_id(self.team_id),
            room=gt_room,
        )
        assert shell.runtime_state_ready is False, "新建对象不应自带就绪标记"
        assert shell.messages == [], "造壳后内存消息列表为空（与全新空房间无法区分，这正是 bug 根因）"

        with pytest.raises(TogoException) as activate_exc:
            await shell.activate_scheduling()
        assert activate_exc.value.error_code == "room_state_not_ready"

        with pytest.raises(TogoException) as append_exc:
            await shell.add_message(await self._agent_id("alice"), "不该被写入")
        assert append_exc.value.error_code == "room_state_not_ready"

        # 断言的关键：两次拦截之后，库里一条新行都不能有
        rows_after, _ = await gtRoomMessageManager.get_room_messages(gt_room.id)
        assert len(rows_after) == 3, "被拦截的调用不得落库"
        assert await _zero_seq_system_count(gt_room.id) == zero_seq_before == 1, (
            "seq=0 系统消息新增条数必须为 0"
        )

    # ─── 正向：走统一入口 → 就绪、seq 接续、不补写初始消息 ─────────────

    async def test_load_room_with_state_marks_ready_and_continues_seq(self):
        """同一房间走 `load_room_with_state`：不抛、`_next_seq == max(seq)+1`、不新增 seq=0。"""
        gt_room, _ = await self._seed_history_room("ready_room", ["alice", "bob"])
        zero_seq_before = await _zero_seq_system_count(gt_room.id)

        # 模拟「内存里没有」再按新入口装载
        await roomService.close_team_rooms(self.team_id)
        room = await roomService.load_room_with_state(
            gt_team=await gtTeamManager.get_team_by_id(self.team_id),
            gt_room=gt_room,
        )

        assert room.runtime_state_ready is True
        assert len(room.messages) == 3
        assert room._store._next_seq == max(m.seq for m in room.messages) + 1 == 3

        assert await room.activate_scheduling() is True  # INIT → SCHEDULING，不抛
        assert await _zero_seq_system_count(gt_room.id) == zero_seq_before == 1, (
            "已有历史的房间不得被补写初始系统消息"
        )

        await room.add_message(await self._agent_id("alice"), "装载后新消息")
        rows, _ = await gtRoomMessageManager.get_room_messages(gt_room.id)
        assert [row.seq for row in rows] == [0, 1, 2, 3], "seq 必须从历史末尾接续，不得从 0 重发"
        assert len({row.seq for row in rows}) == 4, "库内不得出现重复 seq"

    async def test_load_room_with_state_on_new_empty_room_writes_initial_message(self):
        """全新空房间走同一条路：就绪 + 正常写入 seq=0 初始系统消息（不误伤新建路径）。"""
        gt_room = await gtRoomManager.save_room(GtRoom(
            team_id=self.team_id,
            name="fresh_room",
            type=RoomType.GROUP,
            initial_topic="全新房间",
            max_rounds=5,
            agent_ids=[await self._agent_id("alice"), await self._agent_id("bob")],
        ))
        assert await _all_rows(gt_room.id) == [], "前置条件：DB 里确实没有任何消息"

        room = await roomService.load_room_with_state(
            gt_team=await gtTeamManager.get_team_by_id(self.team_id),
            gt_room=gt_room,
        )
        # 关键：读出来是空的，标记也必须已就绪，否则新建房间第一次写就撞断言
        assert room.runtime_state_ready is True
        assert room.messages == []

        assert await room.activate_scheduling() is True

        rows = await _all_rows(gt_room.id)
        assert len(rows) == 1
        assert (rows[0].sender_id, rows[0].seq) == (ChatRoom.SYSTEM_MEMBER_ID, 0)
        assert "房间已经创建" in rows[0].content

    # ─── 回归：复现线上场景（团队停用 → 点开单聊）────────────────────

    async def test_reopen_control_room_after_unload_keeps_history_and_read_cursor(self):
        """团队停用后点开单聊（房间不在内存）：历史全可读、读游标正确、库内无重复 seq。"""
        alice_id = await self._agent_id("alice")
        gt_room = await gtRoomManager.save_room(GtRoom(
            team_id=self.team_id,
            name="ctrl_room",
            type=RoomType.PRIVATE,
            initial_topic="",
            max_rounds=-1,
            agent_ids=[int(SpecialAgent.OPERATOR.value), alice_id],
        ))
        await roomService.load_all_rooms()
        original = roomService.get_room(gt_room.id)
        assert original is not None
        await original.activate_scheduling()
        await original.add_message(int(SpecialAgent.OPERATOR.value), "操作者消息一")
        await original.add_message(int(SpecialAgent.OPERATOR.value), "操作者消息二")
        await original.get_unread_messages(alice_id)  # 推进并持久化读游标
        read_index_before = original.export_agent_read_index()
        assert read_index_before.get(alice_id) == 3, "alice 应已读到 seq=2 之后（游标=3）"
        assert await _zero_seq_system_count(gt_room.id) == 1

        # 团队停用：restore_team 直接 return，房间根本不在内存
        await roomService.close_team_rooms(self.team_id)
        assert roomService.get_room(gt_room.id) is None

        room, created = await roomService.get_or_create_control_room(self.team_id, alice_id)

        assert created is False, "DB 已有控制房，不应新建"
        assert room.room_id == gt_room.id
        assert room.runtime_state_ready is True
        assert [m.content for m in room.messages] == [
            m.content for m in await _all_rows(gt_room.id)
        ], "历史消息必须全部装载，顺序与库一致"
        assert len(room.messages) == 3
        assert room.export_agent_read_index() == read_index_before, "读游标必须原样恢复"
        assert await _zero_seq_system_count(gt_room.id) == 1, (
            "重开后不得补写任何 seq=0 系统消息"
        )

        rows, _ = await gtRoomMessageManager.get_room_messages(gt_room.id)
        assert len({row.seq for row in rows}) == len(rows), "库内不得出现重复 seq"

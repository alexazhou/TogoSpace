"""房间消息部分加载（固定窗口 + 越界补读）判据测试。

对应设计：`docs/design/2026-11-16-room-message-partial-load/room_message_partial_load_design.md`

本文件的每条判据都必须**能证伪**（判据有效性靠变异测试证明，见交付报告）：
- 窗口判据：250 条历史的房间，启动恢复后内存**实收** WINDOW_SIZE 条，而不是 250 条；
- 补读判据：读游标落后超过窗口时，未读条数 / seq 连续性 / display_name 回填；
- 反面对照：把补读关掉，上面的条数判据随之改变（证明它不是恒真断言）；
- escalate 配套闭环：窗口外消息升级后，AI 侧能经补读发现它（缺一不可的那一半）。

约定：本文件**直接往 DB 写历史**（不经内存），因为要模拟「历史早已累积很长、
服务此刻才启动」的真实场景。
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

import service.agentService as agentService
import service.ormService as ormService
import service.persistenceService as persistenceService
import service.roomService as roomService
from constants import RoomType, SpecialAgent
from dal.db import gtAgentManager, gtRoomManager, gtRoomMessageManager, gtTeamManager
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtRoom import GtRoom
from model.dbModel.gtTeam import GtTeam
from service.funcToolService.tools import get_room_info
from service.roomService import ToolCallContext
from service.roomService.messageStore import RoomMessageStore
from ...base import ServiceTestCase

TEAM = "test_partial_load"
HISTORY = 250  # 远超窗口（100），使「全量 vs 窗口」的差异可判别
WINDOW = RoomMessageStore.WINDOW_SIZE
OPERATOR_ID = int(SpecialAgent.OPERATOR.value)


class TestRoomMessagePartialLoad(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        await ormService.startup(cls._get_test_db_path())
        await persistenceService.startup()
        await agentService.startup()
        await roomService.startup()

        team = await gtTeamManager.save_team(GtTeam(name=TEAM))
        await gtAgentManager.batch_save_agents(
            team.id,
            [
                GtAgent(team_id=team.id, name="alice", role_template_id=0),
                GtAgent(team_id=team.id, name="bob", role_template_id=0),
                GtAgent(team_id=team.id, name="charlie", role_template_id=0),
            ],
        )
        cls.team_id = team.id

    @classmethod
    async def async_teardown_class(cls):
        roomService.shutdown()
        await agentService.shutdown()
        await persistenceService.shutdown()
        await ormService.shutdown()

    # ─── helpers ────────────────────────────────────────────────────────────

    async def _agent_id(self, name: str) -> int:
        gt_agent = await gtAgentManager.get_agent(self.team_id, name)
        assert gt_agent is not None, f"agent '{name}' 不存在"
        return gt_agent.id

    async def _make_room(self, name: str, *, room_type: RoomType = RoomType.GROUP,
                         agent_ids: list[int] | None = None) -> GtRoom:
        gt_room = await gtRoomManager.save_room(GtRoom(
            team_id=self.team_id,
            name=name,
            type=room_type,
            initial_topic="",
            max_rounds=-1,
            agent_ids=agent_ids or [await self._agent_id("alice"), await self._agent_id("bob")],
        ))
        return gt_room

    async def _seed_history(self, gt_room: GtRoom, n: int, *, sender_id: int | None = None) -> list[int]:
        """直接写 DB `n` 条（seq=0..n-1），**不经内存**，返回 db id 列表。"""
        sid = sender_id if sender_id is not None else await self._agent_id("alice")
        base = datetime(2026, 11, 16, 12, 0, 0)
        ids: list[int] = []
        for seq in range(n):
            row = await gtRoomMessageManager.append_room_message(
                room_id=gt_room.id, sender_id=sid, content=f"历史消息#{seq}",
                send_time=base + timedelta(seconds=seq), seq=seq,
            )
            ids.append(row.id)
        return ids

    async def _load(self, gt_room: GtRoom):
        return await roomService.load_room_with_state(
            gt_team=await gtTeamManager.get_team_by_id(self.team_id),
            gt_room=gt_room,
        )

    async def _db_rows(self, room_id: int):
        rows, _ = await gtRoomMessageManager.get_room_messages(room_id)
        return rows

    # ─── 判据 1：启动恢复只加载窗口（实收条数，不信代码推断）─────────────────

    async def test_restore_loads_window_not_full_history(self):
        """250 条历史的房间：恢复后内存**实收 100 条**（最后 100 条），发号仍是全局 max+1。"""
        gt_room = await self._make_room("window_room")
        await self._seed_history(gt_room, HISTORY)

        # 前置条件：DB 里确实是 250 条（否则下面的差异断言毫无意义）
        assert len(await self._db_rows(gt_room.id)) == HISTORY == 250

        room = await self._load(gt_room)

        assert len(room.messages) == WINDOW == 100, (
            f"恢复后内存实收应为窗口 {WINDOW} 条，实际 {len(room.messages)} 条"
        )
        assert [m.seq for m in room.messages] == list(range(HISTORY - WINDOW, HISTORY)), (
            "窗口必须是最新的那一段（seq 递增且以 max(seq) 结尾）"
        )
        # 窗口化不得影响发号：后续写入必须从 250 继续，不能回绕撞车
        assert room._store._next_seq == HISTORY == 250

        await room.add_message(await self._agent_id("bob"), "窗口化后新消息")
        rows = await self._db_rows(gt_room.id)
        assert len(rows) == HISTORY + 1
        assert rows[-1].seq == HISTORY, "窗口化后新消息 seq 必须接续全局 max(seq)+1"
        assert len({row.seq for row in rows}) == len(rows), "库内不得出现重复 seq"

    # ─── 判据 2：越界补读（正向），seq 连续 / 无重 / 无漏 / display_name ─────

    async def test_unread_beyond_window_is_backfilled(self):
        """读游标(0)落后超过窗口：未读必须补读回全部 250 条，seq 连续无重无漏。"""
        gt_room = await self._make_room("backfill_room")
        await self._seed_history(gt_room, HISTORY)
        room = await self._load(gt_room)
        assert len(room.messages) == WINDOW  # 前置：内存里只有 100 条

        alice_id = await self._agent_id("alice")
        unread = await room.get_unread_messages(alice_id)

        assert len(unread) == HISTORY == 250, (
            f"游标落后窗口时把全部未读补回来应得 {HISTORY} 条，实际 {len(unread)} 条"
        )
        seqs = [m.seq for m in unread]
        assert seqs == list(range(HISTORY)), "补读结果必须按 seq 升序且连续（无空洞）"
        assert len(set(seqs)) == HISTORY, "补读结果不得重复"
        assert unread[0].content == "历史消息#0" and unread[-1].content == f"历史消息#{HISTORY - 1}"
        assert all(m.sender_display_name for m in unread), (
            "补读出的 DB 消息必须回填 sender_display_name（否则 prompt 里 sender 为空）"
        )

        # 游标推进后不重复返回
        assert await room.get_unread_messages(alice_id) == []

    async def test_unread_inside_window_does_not_hit_db(self, monkeypatch):
        """游标在窗口内时走纯内存快路径：一次 DB 补读都不该发生。"""
        gt_room = await self._make_room("fastpath_room")
        await self._seed_history(gt_room, HISTORY)
        room = await self._load(gt_room)
        alice_id = await self._agent_id("alice")

        calls: list[tuple] = []

        async def _spy(*args, **kwargs):
            calls.append((args, kwargs))
            return []

        monkeypatch.setattr(gtRoomMessageManager, "get_room_messages_by_seq_range", _spy)

        # 先把游标推到窗口内（首次调用会补读，此处只关心之后的调用）
        await room.get_unread_messages(alice_id)
        calls.clear()

        unread = await room.get_unread_messages(alice_id)
        assert unread == []
        assert calls == [], "游标已在窗口内，不应再触发 DB 补读"

    async def test_cursor_at_window_boundary_not_lost_not_duplicated(self):
        """**已读/未读边界落在窗口交界处**时仍正确：不漏、不重、顺序对。

        窗口 = `[150..249]`（下界 150）。窗口化最容易破坏的正是这条边界：
        补读区间 `[游标, 150)` 与窗口 `[150..]` 的拼接点若写错（左开/右闭搞反、
        或拼接时重复计入窗口首条），就会出现「漏一条」或「同一条读两次」。

        覆盖 5 个位置：跨边界 145（补读 5 + 窗口 100）、仅补读 1 条 149、
        恰好等于窗口下界 150（纯内存路径）、窗口内 151、已读到底 250。
        """
        gt_room = await self._make_room("boundary_room")
        await self._seed_history(gt_room, HISTORY)
        room = await self._load(gt_room)
        alice_id = await self._agent_id("alice")
        store = room._store
        lower = HISTORY - WINDOW  # 150
        assert [m.seq for m in room.messages][0] == lower, "前置：窗口下界必须是 150"

        cases = [
            (145, list(range(145, HISTORY))),
            (lower - 1, list(range(lower - 1, HISTORY))),
            (lower, list(range(lower, HISTORY))),
            (lower + 1, list(range(lower + 1, HISTORY))),
            (HISTORY, []),
        ]
        for cursor, expected in cases:
            store._agent_seq_read[alice_id] = cursor
            unread = await room.get_unread_messages(alice_id)
            seqs = [m.seq for m in unread]
            assert seqs == expected, f"游标={cursor}：期望 seq {expected[:3]}…，实际 {seqs[:3]}…"
            assert len(set(seqs)) == len(seqs), f"游标={cursor}：出现重复消息（拼接点写错）"
            if expected:
                # 边界处的对象必须是真消息（补读对象回填完好），不是空壳
                assert unread[0].content == f"历史消息#{expected[0]}"
                assert unread[-1].content == f"历史消息#{expected[-1]}"
                assert all(m.sender_display_name for m in unread), (
                    f"游标={cursor}：补读段 display_name 未回填"
                )

    # ─── 判据 3：反面对照 —— 补读缺失时历史确实会丢 ────────────────────────

    async def test_without_backfill_history_is_lost(self, monkeypatch):
        """**反面对照，不是功能期望**：把补读分支关掉，未读只剩窗口内 100 条。

        这条同时给出「判据 2 应红」的依据：变异测试正是把这里的情况变成现实，
        届时判据 2 的 `len(unread) == 250` 必然失败。
        """
        gt_room = await self._make_room("nobackfill_room")
        await self._seed_history(gt_room, HISTORY)
        room = await self._load(gt_room)
        alice_id = await self._agent_id("alice")

        async def _empty(*args, **kwargs):
            return []

        monkeypatch.setattr(gtRoomMessageManager, "get_room_messages_by_seq_range", _empty)

        unread = await room.get_unread_messages(alice_id)
        assert len(unread) == WINDOW == 100
        assert [m.seq for m in unread] == list(range(HISTORY - WINDOW, HISTORY))
        assert 0 not in [m.seq for m in unread], "窗口外的 seq=0 已经丢了（这正是被变异放大的缺陷）"

    # ─── 判据 4：escalate 窗口外消息 + 配套闭环（缺一不可的另一半）─────────

    async def test_escalate_message_outside_window_triggers_immediate_detection(self):
        """窗口外（已被淘汰）的消息升级后：DB 置位 + AI 侧经补读发现 immediate。

        少了任意一半都不成立：只做 DB fallback 而不补读，AI 永远收不到；
        只补读不做 fallback，store 直接 ValueError。
        """
        alice_id = await self._agent_id("alice")
        gt_room = await self._make_room(
            "escalate_room", room_type=RoomType.PRIVATE, agent_ids=[OPERATOR_ID, alice_id],
        )
        msg_ids = await self._seed_history(gt_room, HISTORY, sender_id=OPERATOR_ID)
        room = await self._load(gt_room)

        target_id = msg_ids[HISTORY // 2]  # seq=125，早已被窗口淘汰
        window_ids = {m.id for m in room.messages}
        assert target_id not in window_ids, "前置：目标消息必须不在内存窗口内"

        await room.escalate_message_to_immediate(target_id)

        row = await gtRoomMessageManager.get_room_message_by_id(target_id)
        assert row is not None and row.insert_immediately is True, "DB 必须已置位"

        assert await room.has_unread_immediate_messages(alice_id) is True, (
            "窗口外的 immediate 消息必须能被 AI 侧发现（靠越界补读）"
        )
        assert await room.has_unread_messages(alice_id) is True
        # 全量未读里也包含它，且顺序正确
        unread = await room.get_unread_messages(alice_id)
        assert target_id in [m.id for m in unread]

    async def test_escalate_rejects_message_from_other_room(self):
        """DB fallback 必须校验 room_id：别的房间的消息 id 不得跨房间升级（越权）。"""
        alice_id = await self._agent_id("alice")
        room_a = await self._make_room(
            "esc_room_a", room_type=RoomType.PRIVATE, agent_ids=[OPERATOR_ID, alice_id],
        )
        room_b = await self._make_room(
            "esc_room_b", room_type=RoomType.PRIVATE, agent_ids=[OPERATOR_ID, alice_id],
        )
        ids_b = await self._seed_history(room_b, 3, sender_id=OPERATOR_ID)

        chat_a = await self._load(room_a)
        with pytest.raises(ValueError):
            await chat_a.escalate_message_to_immediate(ids_b[0])
        # 且不得把 B 房间的消息写脏
        row_b = await gtRoomMessageManager.get_room_message_by_id(ids_b[0])
        assert row_b is not None and row_b.insert_immediately is False

    async def test_escalate_unknown_message_raises(self):
        """不存在的 db_id：内存与 DB 都没有 → ValueError（不得静默成功）。"""
        alice_id = await self._agent_id("alice")
        gt_room = await self._make_room(
            "esc_unknown", room_type=RoomType.PRIVATE, agent_ids=[OPERATOR_ID, alice_id],
        )
        chat = await self._load(gt_room)
        with pytest.raises(ValueError):
            await chat.escalate_message_to_immediate(99999999)

    # ─── 判据 5：join 游标改用 limit=1 取最后一条，结果与旧全量口径一致 ─────

    async def test_update_room_agents_join_cursor_equals_max_seq_plus_one(self):
        """新成员 join 游标必须 = 全局 max(seq)+1（旧实现是全量查询后取 max）。"""
        gt_room = await self._make_room("join_room")
        await self._seed_history(gt_room, HISTORY)
        rows = await self._db_rows(gt_room.id)
        expected = max(r.seq for r in rows) + 1
        assert expected == HISTORY

        charlie_id = await self._agent_id("charlie")
        alice_id = await self._agent_id("alice")
        bob_id = await self._agent_id("bob")

        await roomService.update_room_agents(gt_room.id, [alice_id, bob_id, charlie_id])

        read_index, _speaker = await gtRoomManager.get_room_state(gt_room.id)
        assert read_index is not None
        assert read_index[str(charlie_id)] == expected, (
            f"新成员 join 游标应为 {expected}，实际 {read_index.get(str(charlie_id))}"
        )

    # ─── 判据 7：写入侧窗口淘汰（内存淘汰 ≠ 删库）────────────────────────────

    async def test_append_beyond_window_keeps_only_latest(self):
        """连续写入超过窗口：内存只留最近 WINDOW 条，`_next_seq` 仍全局递增，DB 不丢。"""
        gt_room = await self._make_room("trim_room")
        room = await self._load(gt_room)
        bob_id = await self._agent_id("bob")

        total = WINDOW + 20
        for i in range(total):
            await room.add_message(bob_id, f"新消息#{i}")

        assert len(room.messages) == WINDOW == 100, (
            f"写入 {total} 条后内存应只保留 {WINDOW} 条，实际 {len(room.messages)} 条"
        )
        assert [m.seq for m in room.messages] == list(range(total - WINDOW, total)), (
            "淘汰必须从头部（最老）开始，保留最新一段"
        )
        assert room._store._next_seq == total, "窗口淘汰不得影响发号"
        assert len(await self._db_rows(gt_room.id)) == total, (
            "内存淘汰不等于删库：DB 必须留下全部消息"
        )

    # ─── 判据 6：房间详情的 total_messages 走 DB count，不随窗口缩水 ────────

    async def test_room_detail_total_messages_counts_db_not_window(self):
        """`total_messages` 必须是真实总数（250），不能是内存窗口大小（100）。"""
        gt_room = await self._make_room("count_room")
        await self._seed_history(gt_room, HISTORY)
        await self._load(gt_room)  # 房间已在内存，且内存只有窗口

        ctx = ToolCallContext(
            agent_id=await self._agent_id("alice"),
            team_id=self.team_id,
            chat_room=roomService.get_room(gt_room.id),
        )
        detail = await get_room_info(name="count_room", _context=ctx)
        assert detail["success"] is True
        assert detail["room"]["total_messages"] == HISTORY == 250, (
            f"total_messages 应为 DB 真实总数 {HISTORY}，实际 {detail['room']['total_messages']}"
        )

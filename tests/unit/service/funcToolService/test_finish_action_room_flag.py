"""#307：`finish_action` 误报「未在收到消息的房间发言」——判定源回归判据。

**为什么要这组用例**：`tools.py:2106` 只读 `chat_room.current_turn_has_content`，而该标志是
「**调度器当前 turn** 有没有内容」，不是「**这个 agent** 在本房间有没有发过言」。两者在
「房间已不在本 agent 的 turn 上」时分离：agent 的消息合法落库了，标志仍是 False ⇒ 误报。

这里用**真实**的 `ChatRoom` + `RoomScheduler`，只把落库（`store.append_and_assign_seq`）、
DB 持久化（`gtRoomManager.update_room_state`）、事件发布（`messageBus.publish`）与 agent 名字打桩，
驱动真实的 `add_message` / `handle_finish_request` / `finish_action` 全链路。
"""

from __future__ import annotations

from typing import List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from constants import RoomState, RoomType, SpecialAgent
from service.funcToolService.tools import finish_action
from service.roomService import chatRoom as chat_room_mod
from service.roomService import roomScheduler as rs_mod
from service.roomService.chatRoom import ChatRoom
from service.roomService.core import ToolCallContext

OP = int(SpecialAgent.OPERATOR.value)


def _fake_gt_room(agent_ids: List[int], room_type: RoomType = RoomType.PRIVATE) -> MagicMock:
    room = MagicMock()
    room.id = 7
    room.name = "私聊"
    room.agent_ids = list(agent_ids)
    room.type = room_type
    room.max_rounds = None
    return room


@pytest.fixture(autouse=True)
def _stubbed_infra():
    """外部副作用全部打桩，调度与判定逻辑保持真实。"""
    with patch.object(chat_room_mod, "gtAgentManager") as chat_agents, patch.object(
        chat_room_mod, "messageBus"
    ), patch.object(chat_room_mod, "gtRoomMessageManager"), patch.object(
        rs_mod, "gtRoomManager"
    ) as room_mgr, patch.object(
        rs_mod, "gtAgentManager"
    ) as sched_agents, patch.object(
        rs_mod, "messageBus"
    ), patch.object(
        rs_mod, "configUtil"
    ) as cfg:
        agent = MagicMock()
        agent.display_name = "李哥"
        chat_agents.get_agent_by_id = AsyncMock(return_value=agent)
        room_mgr.update_room_state = AsyncMock()
        sched_agents.get_agent_name = MagicMock(side_effect=lambda aid: f"agent{aid}")
        cfg.get_app_config.return_value.setting.default_room_max_rounds = 10
        yield


def _chat_room(agent_ids: List[int], room_type: RoomType = RoomType.PRIVATE) -> ChatRoom:
    room = ChatRoom(team=MagicMock(), room=_fake_gt_room(agent_ids, room_type))
    room._runtime_state_ready = True
    room._store.append_and_assign_seq = AsyncMock()
    room._store.get_read_index = MagicMock(return_value={})
    return room


def _ctx(agent_id: int, room: ChatRoom) -> ToolCallContext:
    return ToolCallContext(agent_id=agent_id, team_id=1, chat_room=room)


@pytest.mark.asyncio
async def test_post_in_idle_room_counts_as_speaking() -> None:
    """房间已 IDLE 时发出的消息，仍然算「在本房间发言」（本次修复的核心判据）。

    进入 IDLE 走真实路径（`activate` → A 发言 → A 结束 → 无下一个 AI ⇒ IDLE），
    然后再发一条消息 —— 这正是人工停止 / 唤醒等真实路径留下的状态：
    消息确实写进了房间，但 `on_message` 走「非 SCHEDULING」分支（`roomScheduler.py:151`）
    把标志清零 ⇒ 旧判定读到 False ⇒ 误报「未在收到消息的房间发言」。
    """
    room = _chat_room([1, OP])
    room._scheduler.activate()
    await room.add_message(1, "第一轮发言")
    assert room.current_turn_has_content is True, "SCHEDULING 且 sender==当前发言人应置 True"

    assert await room.handle_finish_request(1) is True
    assert room.state == RoomState.IDLE, "A 结束后无下一个可调度 AI ⇒ 回 IDLE（既有语义，不动）"

    await room.add_message(1, "房间 IDLE 之后仍合法发出的消息")
    assert room.current_turn_has_content is False, (
        "前提核对：IDLE 分支确实把调度器标志清零了（这正是误报的来源）"
    )

    result = await finish_action(_context=_ctx(1, room))
    assert result["success"] is True, f"消息已在房间内，不该报未发言: {result['message']}"


@pytest.mark.asyncio
async def test_post_in_scheduling_turn_still_counts() -> None:
    """回归：正常 turn 内发言仍然通过（不能为了修误报把判定做废）。"""
    room = _chat_room([1, OP])
    room._scheduler.activate()
    await room.add_message(1, "正常发言")
    result = await finish_action(_context=_ctx(1, room))
    assert result["success"] is True, result["message"]


@pytest.mark.asyncio
async def test_finish_without_posting_still_reports() -> None:
    """反方向格：本轮真的没在这个房间发过言 ⇒ 仍必须报错（判定不能被修成恒真）。"""
    room = _chat_room([1, OP])
    room._scheduler.activate()
    result = await finish_action(_context=_ctx(1, room))
    assert result["success"] is False
    assert "未在收到消息的房间" in result["message"]


@pytest.mark.asyncio
async def test_other_agent_message_does_not_count_as_mine() -> None:
    """反方向格：别人在本房间发消息，不能算「我发过言」。"""
    room = _chat_room([1, 2, OP], RoomType.GROUP)
    room._scheduler.activate()  # 当前发言人 = 1
    await room.add_message(2, "2 插话")
    result = await finish_action(_context=_ctx(1, room))
    assert result["success"] is False, "agent 2 的发言不能被记到 agent 1 头上"
    assert "未在收到消息的房间" in result["message"]


@pytest.mark.asyncio
async def test_same_agent_not_scheduled_twice_in_a_row() -> None:
    """轮次语义：同一人不得连续发言（钉 `_should_skip` 的「与上一位发言人相同则跳过」）。

    为什么用**只有 1 个 AI** 的房间 `[1]`：A 结束后，下一个候选绕回到 **A 自己**，只有「同人跳过」这条
    规则才会让房间停下来。规则被破坏时 A 会被重新排上。
    刻意**不放 OPERATOR**：否则「OPERATOR 跳过」那条规则也会影响本场景，M8 与 M9 就分不开了
    （#311 要求的「只红自己」）。

    **不读 `get_current_turn_agent_id()`**：`_stop_if_done()` 会把 `current_speaker_index` 置 None，
    而该方法在 None 时退化成 `agent_ids[0]`（= 1），与「A 又被排上」的读数**巧合相同**——
    旧用例正是被这个退化值骗过（#311 缺口 1）。这里改成断言 `state` 与 `current_speaker_index`。
    """
    room = _chat_room([1])
    room._scheduler.activate()
    assert room.state == RoomState.SCHEDULING
    assert room._scheduler.get_current_turn_agent_id() == 1

    await room.add_message(1, "唯一一个 AI 发言")
    assert await room.handle_finish_request(1) is True

    assert room.state == RoomState.IDLE, "下一候选是 A 自己 ⇒ 必须被跳过 ⇒ 全员跳过 ⇒ 停"
    assert room._scheduler.current_speaker_index is None


@pytest.mark.asyncio
async def test_operator_never_scheduled_as_speaker() -> None:
    """轮次语义：OPERATOR 始终跳过（钉 `_should_skip` 的第一条规则）。

    用 `[1, 2, OP]` 群聊，两人各发言一次后轮次会绕到 OPERATOR：
    - OPERATOR 被正确跳过 ⇒ 下一位是 1，且 `state == SCHEDULING`（不是退化值巧合）；
    - OPERATOR 未被跳过 ⇒ 下一位会是 OP，断言立刻红。
    """
    room = _chat_room([1, 2, OP], RoomType.GROUP)
    room._scheduler.activate()
    assert room.state == RoomState.SCHEDULING
    assert room._scheduler.get_current_turn_agent_id() == 1

    await room.add_message(1, "A 发言")
    assert await room.handle_finish_request(1) is True
    assert room.state == RoomState.SCHEDULING
    assert room._scheduler.get_current_turn_agent_id() == 2, "轮到下一位 AI（不是 A）"

    await room.add_message(2, "B 发言")
    assert await room.handle_finish_request(2) is True
    assert room.state == RoomState.SCHEDULING, "还有可调度成员，不该停"
    assert room._scheduler.get_current_turn_agent_id() != OP, "OPERATOR 不得被排上发言位"
    assert room._scheduler.get_current_turn_agent_id() == 1


@pytest.mark.asyncio
async def test_previous_speaker_must_speak_again_after_turn_advance() -> None:
    """换人后上一轮的发言记录必须失效（钉 `_go_next_agent` 里的 `_turn_speakers.clear()`）。

    漏清 ⇒ 上一轮发言人跨轮记忆 ⇒ 他在新一轮**没发言也能通过** `finish_action` ——
    方向与 #307 修的误报相反（假绿放行），比误报更危险。
    """
    room = _chat_room([1, 2])
    room._scheduler.activate()
    await room.add_message(1, "A 在第一轮发言")
    assert room.has_spoken_in_turn(1) is True
    assert await room.handle_finish_request(1) is True
    assert room._scheduler.get_current_turn_agent_id() == 2, "已换到 B"

    assert room.has_spoken_in_turn(1) is False, "换人后 A 上一轮的发言记录必须失效"
    result = await finish_action(_context=_ctx(1, room))
    assert result["success"] is False, "A 在新一轮没发言，不能被上一轮的记录放行"
    assert "未在收到消息的房间" in result["message"]


@pytest.mark.asyncio
async def test_previous_speaker_must_speak_again_after_cancel() -> None:
    """人工停止后同样要清（钉 `cancel_current_turn` 里的 `_turn_speakers.clear()`）。"""
    room = _chat_room([1, OP])
    room._scheduler.activate()
    await room.add_message(1, "A 在被停止前发言")
    assert room.has_spoken_in_turn(1) is True

    room._scheduler.cancel_current_turn()
    assert room.state == RoomState.IDLE

    assert room.has_spoken_in_turn(1) is False, "人工停止后本轮记录必须失效"
    result = await finish_action(_context=_ctx(1, room))
    assert result["success"] is False
    assert "未在收到消息的房间" in result["message"]


@pytest.mark.asyncio
async def test_confirm_no_need_talk_still_rejected_after_speaking() -> None:
    """对外契约不变：发过言还显式传 `confirm_no_need_talk=true` 仍被拒绝。"""
    room = _chat_room([1, OP])
    room._scheduler.activate()
    await room.add_message(1, "正常发言")
    result = await finish_action(_context=_ctx(1, room), confirm_no_need_talk=True)
    assert result["success"] is False
    assert "已经通过 send_chat_msg 发过消息" in result["message"]


# ---------------------------------------------------------------------------
# #313：剩下 3 处 `_turn_speakers.clear()` 的判据
#（38 逐个删掉后本文件仍 9 passed 全绿 ⇒ 三处此前全无判据）
# ---------------------------------------------------------------------------


async def _idle_room_with_stale_record() -> ChatRoom:
    """构造「房间已 IDLE，但上一轮某 agent 的发言记录还留着」的状态。

    序列（全部走真实路径）：
    - `activate` → 发言人 1；`finish(1)` 未发言 ⇒ 记入本轮跳过集，推进到 2；
    - `post(1)`：1 在别人的发言位插话 ⇒ 记录 `{1}`（这是「自上次换人以来 1 说过话」）；
    - `finish(2)` 未发言 ⇒ 本轮所有 AI 均已跳过 ⇒ `_stop_if_done()` 直接进 IDLE
      （**这条路径不经过 `_go_next_agent`**，所以 `_turn_speakers` 仍是 `{1}`）。
    """
    room = _chat_room([1, 2])
    room._scheduler.activate()
    assert room._scheduler.get_current_turn_agent_id() == 1
    # 1 未发言就结束 ⇒ 记入本轮跳过集，推进到 2
    assert await room.handle_finish_request(1) is True
    assert room._scheduler.get_current_turn_agent_id() == 2
    # 1 在 2 的发言位插话 ⇒ 记录 {1}（「自上次换人以来 1 说过话」）
    await room.add_message(1, "在 2 的发言位插话")
    assert room.has_spoken_in_turn(1) is True
    # 2 也未发言 ⇒ 本轮所有 AI 均已跳过 ⇒ `_stop_if_done()` 直接进 IDLE
    assert await room.handle_finish_request(2) is True
    return room


@pytest.mark.asyncio
async def test_set_current_speaker_index_clears_stale_record() -> None:
    """钉 `set_current_speaker_index` 里的 `_turn_speakers.clear()`

    入口是真实方法 `rebuild_state_from_history`（房间运行时态重建 / 恢复持久化发言位都会走它）：
    重建后上一轮的发言记录必须失效，否则重建完就能白嫖通过 `finish_action`。
    """
    room = _chat_room([1, 2])
    room._scheduler.activate()
    await room.add_message(1, "重建前发过言")
    assert room.has_spoken_in_turn(1) is True

    room.rebuild_state_from_history(0)
    assert room.has_spoken_in_turn(1) is False, "重建发言位后上一轮记录必须失效"

    result = await finish_action(_context=_ctx(1, room))
    assert result["success"] is False
    assert "未在收到消息的房间" in result["message"]


@pytest.mark.asyncio
async def test_wake_from_idle_clears_stale_record() -> None:
    """钉 `on_message` 非 SCHEDULING 分支（IDLE 唤醒）里的 `_turn_speakers.clear()`

    房间 IDLE 且残留 `{1}` 时，被一条新消息唤醒 ⇒ 新一轮开始，1 的旧记录必须失效。
    漏清 ⇒ 1 在新一轮**一句没说也能通过 `finish_action`**（假绿放行）。
    """
    room = await _idle_room_with_stale_record()
    assert room.state == RoomState.IDLE
    assert room.has_spoken_in_turn(1) is True, "前提：此刻 1 的记录还在（IDLE 停止路径不清集合）"

    # 房间被 2 的新消息唤醒（非 SCHEDULING 分支）
    await room.add_message(2, "唤醒房间的新消息")
    assert room.state == RoomState.SCHEDULING
    assert room.has_spoken_in_turn(2) is True, "唤醒者本人算发言"
    assert room.has_spoken_in_turn(1) is False, "唤醒后上一轮的记录必须失效"

    result = await finish_action(_context=_ctx(1, room))
    assert result["success"] is False, "1 在新一轮没发言，不能被唤醒前的记录放行"
    assert "未在收到消息的房间" in result["message"]


@pytest.mark.asyncio
async def test_finish_on_idle_room_clears_stale_record() -> None:
    """钉 `handle_finish_request` 的「IDLE 唤醒」分支里的 `_turn_speakers.clear()`

    该分支注释写着「IDLE 唤醒：重置轮次状态后直接调度」—— 它意味着「新一轮开始」，
    所以进入后上一轮的记录必须失效；漏清 ⇒ 同一条记录能被用来**连续放行两次** `finish_action`。

    为什么必须用 3 人房间且「插话者不在 0 号位」：`_advance_to_first_dispatchable()` 若把 0 号位
    当作上一位发言人跳过，会在 `_go_next_agent()` 里顺手清掉集合，那处的 clear 就变成**不可观测**的
    —— 我实测过：2 人房间下删掉这处 clear，用例仍全绿（等于没钉住）。这里让 2 号成员（index=1）插话，
    进入 IDLE 分支后 0 号位（1）是可直接调度的 ⇒ 推进**不会**调用 `_go_next_agent`
    ⇒ 只有这处 clear 能生效，判据才有牙。
    """
    room = _chat_room([1, 2, 3], RoomType.GROUP)
    room._scheduler.activate()
    assert room._scheduler.get_current_turn_agent_id() == 1

    # 1、2 依次未发言就结束（各自记入本轮跳过集），轮次推进到 3
    assert await room.handle_finish_request(1) is True
    assert room._scheduler.get_current_turn_agent_id() == 2
    assert await room.handle_finish_request(2) is True
    assert room._scheduler.get_current_turn_agent_id() == 3

    # 2 在 3 的发言位插话 ⇒ 记录 {2}（index=1，不是 0 号位）
    await room.add_message(2, "2 在 3 的发言位插话")
    assert room.has_spoken_in_turn(2) is True

    # 3 也未发言就结束 ⇒ 本轮全员跳过 ⇒ `_stop_if_done()` 在推进前直接进 IDLE
    #（这条路径**不经过** `_go_next_agent`，所以 2 的旧记录还在）
    assert await room.handle_finish_request(3) is True
    assert room.state == RoomState.IDLE
    assert room.has_spoken_in_turn(2) is True, "前提：IDLE 停止路径不清集合，2 的旧记录仍在"

    first = await finish_action(_context=_ctx(2, room))
    assert first["success"] is True, "刚发过言 ⇒ 这一次应当放行（#307 的语义）"
    assert room.has_spoken_in_turn(2) is False, "进了「IDLE 唤醒」分支 ⇒ 新一轮开始，旧记录必须失效"

    second = await finish_action(_context=_ctx(2, room))
    assert second["success"] is False, "同一条记录不能被用来连续放行两次"
    assert "未在收到消息的房间" in second["message"]

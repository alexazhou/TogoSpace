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
async def test_round_turn_semantics_unchanged() -> None:
    """针对性回归：发言轮次语义（`_should_skip`）本次**不许**被改动。

    - 同一人不得连续发言（A 结束后下一位不是 A）；
    - OPERATOR 始终跳过（不会被排上发言位）。
    """
    room = _chat_room([1, 2, OP], RoomType.GROUP)
    scheduler = room._scheduler
    scheduler.activate()
    assert scheduler.get_current_turn_agent_id() == 1

    assert await room.handle_finish_request(1) is True
    assert scheduler.get_current_turn_agent_id() == 2, "同一人不得连续发言"

    assert await room.handle_finish_request(2) is True
    assert scheduler.get_current_turn_agent_id() == 1, "OPERATOR 必须被跳过（回到 A）"


@pytest.mark.asyncio
async def test_confirm_no_need_talk_still_rejected_after_speaking() -> None:
    """对外契约不变：发过言还显式传 `confirm_no_need_talk=true` 仍被拒绝。"""
    room = _chat_room([1, OP])
    room._scheduler.activate()
    await room.add_message(1, "正常发言")
    result = await finish_action(_context=_ctx(1, room), confirm_no_need_talk=True)
    assert result["success"] is False
    assert "已经通过 send_chat_msg 发过消息" in result["message"]

"""工具层单测：send_chat_msg 的 insert_immediately 生效与降级。

对应设计文档 `docs/design/2026-11-05-agent-immediate-message/step2_technical.md` §6 第一组：
- 双 AI 单聊且对方支持 `host_managed_turn_loop` → 以 `insert_immediately=True` 写入；
- 群聊 / Operator 单聊 / 对方驱动不支持 → 自动降级为 False 写入并返回说明，不抛错。
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from constants import RoomType, SpecialAgent
from service.funcToolService import tools
from service.roomService.core import ToolCallContext

OPERATOR_ID = int(SpecialAgent.OPERATOR.value)


def _room(room_type: RoomType, agent_ids: list[int], *, room_id: int = 1, name: str = "r") -> MagicMock:
    room = MagicMock()
    room.room_id = room_id
    room.name = name
    room.room_type = room_type
    room.get_agent_ids = MagicMock(return_value=list(agent_ids))
    room.can_post_message = MagicMock(return_value=True)
    room.add_message = AsyncMock()
    return room


def _context(agent_id: int = 1, chat_room: MagicMock | None = None) -> ToolCallContext:
    return ToolCallContext(agent_id=agent_id, team_id=1, chat_room=chat_room)


def _target_agent(host_managed: bool):
    agent = MagicMock()
    agent.host_managed_turn_loop = host_managed
    return agent


async def _send(room: MagicMock, ctx: ToolCallContext, insert_immediately: bool, target_agent=None) -> dict:
    """跑一次 send_chat_msg：房间查找与 agent 查找全部 mock 掉。"""
    room_config = MagicMock(id=room.room_id)
    with patch.object(tools.gtRoomManager, "get_room_by_team_and_name", AsyncMock(return_value=room_config)), \
         patch.object(tools.roomService, "get_room", MagicMock(return_value=room)), \
         patch("service.agentService.get_agent_or_none", MagicMock(return_value=target_agent)):
        return await tools.send_chat_msg(
            room_name=room.name,
            msg="hello",
            insert_immediately=insert_immediately,
            _context=ctx,
        )


@pytest.mark.asyncio
async def test_agent_to_agent_private_room_keeps_immediate() -> None:
    """双 AI 单聊 + 对方支持 host loop → 真正加急写入，返回不带降级说明。"""
    room = _room(RoomType.PRIVATE, [1, 2])
    result = await _send(room, _context(agent_id=1), True, _target_agent(host_managed=True))

    assert result["success"] is True
    room.add_message.assert_awaited_once()
    assert room.add_message.await_args.kwargs["insert_immediately"] is True
    assert "注：" not in result["message"]


@pytest.mark.asyncio
async def test_group_room_downgrades_with_note() -> None:
    """群聊不支持加急 → 降级为普通消息并附说明，不抛错。"""
    room = _room(RoomType.GROUP, [1, 2, 3])
    result = await _send(room, _context(agent_id=1), True)

    assert result["success"] is True
    assert room.add_message.await_args.kwargs["insert_immediately"] is False
    assert "群聊不支持加急注入" in result["message"]


@pytest.mark.asyncio
async def test_operator_private_room_downgrades_with_note() -> None:
    """与 Operator 的单聊没有 AI 接收方 → 降级为普通消息，并附说明（step1 §5 要求三种降级都带文案）。"""
    room = _room(RoomType.PRIVATE, [OPERATOR_ID, 1])
    result = await _send(room, _context(agent_id=1), True)

    assert result["success"] is True
    assert room.add_message.await_args.kwargs["insert_immediately"] is False
    assert "对方是操作者，不存在运行中插入" in result["message"]


@pytest.mark.asyncio
async def test_agent_without_host_loop_downgrades_with_note() -> None:
    """对方驱动不支持 host_managed_turn_loop → 降级并说明原因。"""
    room = _room(RoomType.PRIVATE, [1, 2])
    result = await _send(room, _context(agent_id=1), True, _target_agent(host_managed=False))

    assert result["success"] is True
    assert room.add_message.await_args.kwargs["insert_immediately"] is False
    assert "对方当前驱动不支持运行中加急注入" in result["message"]


@pytest.mark.asyncio
async def test_missing_target_agent_downgrades_with_note() -> None:
    """对方 Agent 查不到（已下线等）→ 按不支持处理，降级并说明。"""
    room = _room(RoomType.PRIVATE, [1, 2])
    result = await _send(room, _context(agent_id=1), True, None)

    assert result["success"] is True
    assert room.add_message.await_args.kwargs["insert_immediately"] is False
    assert "对方当前驱动不支持运行中加急注入" in result["message"]


@pytest.mark.asyncio
async def test_default_false_sends_plain_message() -> None:
    """不传 insert_immediately → 普通消息，且不附加任何降级说明。"""
    room = _room(RoomType.PRIVATE, [1, 2])
    result = await _send(room, _context(agent_id=1), False, _target_agent(host_managed=True))

    assert result["success"] is True
    assert room.add_message.await_args.kwargs["insert_immediately"] is False
    assert "注：" not in result["message"]

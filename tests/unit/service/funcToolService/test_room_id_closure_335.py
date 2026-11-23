"""#335（RC-3 / C-02）单元测试：`get_room_info` 的房间视图必须带 `room_id`。

判据来源：`shared/console-issues-20261123/ISSUES.md`（v2.1）§4 RC-3、§8 修复顺序第 3 项、
决策 D2 ①。断链的形态是：

    `room ls` 的列表项**没有 room_id** → 用户拿不到 ID → `room get <id>`（唯一详情入口）永远走不进去
    → 「查房间」这条用户流程死锁。

所以本文件钉住三件事（只钉**可观察返回体**，不钉实现细节 —— 内部抽不抽 `_room_view`
都不该让用例变红）：

1. `room ls`（省略定位参数）返回的**每个列表项**都含 `room_id`，且等于该房间的真实 ID；
2. `room get <id>`（给 `room_id`）返回的**详情项**也含同一个 `room_id`；
3. **顶层键契约不变**：列表仍是 `rooms`（数组）、单查仍是 `room`（单数对象）——
   文档 §9 已把「`rooms` → `room` 单数化」列为作废说法，谁改回去这里必须红。

第 4 组用例把闭环本身钉住：从 `room ls` 的返回体里**只**取 `room_id`，喂给
`get_room_info(room_id=…)`，必须查到同一个房间 —— 这是「用户能不能走通」的判据，
不是「字段存不存在」的判据。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import service.roomService as roomService
from constants import RoomType
from service.funcToolService import tools
from service.roomService import ToolCallContext

TEAM_ID = 1


def _ctx() -> ToolCallContext:
    return ToolCallContext(agent_id=1, team_id=TEAM_ID, tool_name="get_room_info")


def _agent(agent_id: int, name: str) -> SimpleNamespace:
    return SimpleNamespace(id=agent_id, name=name)


def _room(room_id: int, name: str, *, agent_ids: list[int] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=room_id,
        team_id=TEAM_ID,
        name=name,
        type=RoomType.PRIVATE,
        agent_ids=[1, 2] if agent_ids is None else agent_ids,
        tags=["t1"],
        initial_topic="",
        max_rounds=None,
        i18n={},
    )


def _patch(monkeypatch, rooms: list[SimpleNamespace]) -> None:
    """把三个房间查询入口 + 运行态/成员映射/消息计数全部换成替身。"""

    async def _by_id(room_id: int):
        return next((r for r in rooms if r.id == room_id), None)

    async def _by_name(team_id: int, name: str):
        return next((r for r in rooms if r.name == name), None)

    monkeypatch.setattr(tools.gtRoomManager, "get_rooms_by_team", AsyncMock(return_value=rooms))
    monkeypatch.setattr(tools.gtRoomManager, "get_room_by_id", AsyncMock(side_effect=_by_id))
    monkeypatch.setattr(tools.gtRoomManager, "get_room_by_team_and_name", AsyncMock(side_effect=_by_name))
    # 运行态房间不建：`state` 走 INIT 分支、`current_turn` 走 None 分支，判据不依赖它们。
    monkeypatch.setattr(roomService, "get_room", lambda room_id: None)
    monkeypatch.setattr(
        tools.gtRoomMessageManager, "count_room_messages", AsyncMock(return_value=0),
    )
    monkeypatch.setattr(
        tools.gtAgentManager, "get_team_all_agents",
        AsyncMock(return_value=[_agent(1, "alice"), _agent(2, "bob")]),
    )


class TestRoomViewCarriesRoomId:
    """列表项与详情项都要有 `room_id`（缺失即 C-02 复发）。"""

    @pytest.mark.asyncio
    async def test_list_items_carry_room_id(self, monkeypatch):
        """`room ls`：**每一项**都带 `room_id`，且与真实 ID 一致（不是占位/序号）。"""
        _patch(monkeypatch, [_room(11, "r_alpha"), _room(22, "r_beta")])

        result = await tools.get_room_info(_context=_ctx())

        assert result["success"] is True, result
        assert len(result["rooms"]) == 2, "夹具没喂进数据时下面的断言会变成恒真"
        assert [item["room_id"] for item in result["rooms"]] == [11, 22]
        assert all("room_id" in item for item in result["rooms"])

    @pytest.mark.asyncio
    async def test_detail_carries_room_id(self, monkeypatch):
        """`room get <id>`：详情项也带同一个 `room_id`。"""
        _patch(monkeypatch, [_room(11, "r_alpha")])

        result = await tools.get_room_info(room_id=11, _context=_ctx())

        assert result["success"] is True, result
        assert result["room"]["room_id"] == 11
        assert result["room"]["room_name"] == "r_alpha"

    @pytest.mark.asyncio
    async def test_list_and_detail_share_the_same_fields_except_detail_only(self, monkeypatch):
        """列表项与详情项的字段集合只差「详情专属」两项 —— 防止两份视图再次漂移。

        这是本次修复的**根因**：原先是两个分支各手搓字典，列表那份漏了 `room_id`。
        如果将来又有人只改一处，这里会红（而不是等用户在 `room ls` 上撞墙）。
        """
        _patch(monkeypatch, [_room(11, "r_alpha")])

        listed = (await tools.get_room_info(_context=_ctx()))["rooms"][0]
        detail = (await tools.get_room_info(room_id=11, _context=_ctx()))["room"]

        assert set(detail) - set(listed) == {"current_turn", "total_messages"}
        assert set(listed) - set(detail) == set()
        # 列表项不应顺手带上详情专属字段（少花字节、也不泄露没人要的信息）。
        assert "current_turn" not in listed and "total_messages" not in listed


class TestTopLevelKeysUnchanged:
    """顶层键契约：`rooms`（列表）/ `room`（单数）—— 不许单数化改名。"""

    @pytest.mark.asyncio
    async def test_ls_returns_rooms_list(self, monkeypatch):
        _patch(monkeypatch, [_room(11, "r_alpha")])

        result = await tools.get_room_info(_context=_ctx())

        assert set(result) == {"success", "rooms"}, result.keys()
        assert isinstance(result["rooms"], list)

    @pytest.mark.asyncio
    async def test_get_returns_room_object(self, monkeypatch):
        _patch(monkeypatch, [_room(11, "r_alpha")])

        result = await tools.get_room_info(room_id=11, _context=_ctx())

        assert set(result) == {"success", "room"}, result.keys()
        assert isinstance(result["room"], dict), "`room` 必须是单数对象，不是数组"


class TestClosure:
    """闭环判据：只从 `room ls` 拿 `room_id`，就能走通 `room get`。"""

    @pytest.mark.asyncio
    async def test_room_ls_id_feeds_room_get(self, monkeypatch):
        _patch(monkeypatch, [_room(11, "r_alpha"), _room(22, "r_beta")])

        listed = await tools.get_room_info(_context=_ctx())
        # 关键：只用返回体里的 room_id，不借助夹具的已知值 —— 否则测的是「我知道 ID」。
        picked = listed["rooms"][1]["room_id"]
        detail = await tools.get_room_info(room_id=picked, _context=_ctx())

        assert detail["success"] is True, detail
        assert detail["room"]["room_id"] == picked
        assert detail["room"]["room_name"] == "r_beta"

    @pytest.mark.asyncio
    async def test_empty_team_does_not_crash(self, monkeypatch):
        """空团队：`rooms` 仍是空数组（不是 None / 不是报错），闭环判据不因空数据假红。"""
        _patch(monkeypatch, [])

        result = await tools.get_room_info(_context=_ctx())

        assert result == {"success": True, "rooms": []}

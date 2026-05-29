from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from service import teamService


@pytest.mark.asyncio
async def test_clear_team_data_deletes_all_runtime_data(monkeypatch):
    """清空团队数据时应删除任务、历史、消息，并返回统计。"""
    monkeypatch.setattr(teamService, "stop_team_runtime", AsyncMock())
    monkeypatch.setattr(teamService.gtScheculeTaskManager, "delete_tasks_by_team", AsyncMock(return_value=3))
    monkeypatch.setattr(teamService.gtAgentTaskManager, "delete_tasks_by_team", AsyncMock(return_value=2))
    monkeypatch.setattr(teamService.gtAgentHistoryManager, "delete_history_by_team", AsyncMock(return_value=5))
    monkeypatch.setattr(teamService.gtRoomMessageManager, "delete_messages_by_team", AsyncMock(return_value=10))
    monkeypatch.setattr(teamService.gtAgentActivityManager, "delete_activities_by_team", AsyncMock(return_value=8))
    monkeypatch.setattr(teamService.gtRoomManager, "get_rooms_by_team", AsyncMock(return_value=[]))
    monkeypatch.setattr(teamService.gtRoomManager, "delete_rooms_by_ids", AsyncMock(return_value=0))
    reset_mock = AsyncMock()
    monkeypatch.setattr(teamService.gtRoomManager, "reset_room_runtime_state", reset_mock)

    result = await teamService.clear_team_data(1)

    assert result == {"tasks": 5, "histories": 5, "messages": 10, "rooms": 0, "activities": 8}
    teamService.gtRoomManager.delete_rooms_by_ids.assert_not_called()
    reset_mock.assert_awaited_once_with(1)


@pytest.mark.asyncio
async def test_clear_team_data_deletes_non_dept_rooms(monkeypatch):
    """清空团队数据时应删除所有非 DEPT 房间，保留 DEPT 房间。"""
    monkeypatch.setattr(teamService, "stop_team_runtime", AsyncMock())
    monkeypatch.setattr(teamService.gtScheculeTaskManager, "delete_tasks_by_team", AsyncMock(return_value=0))
    monkeypatch.setattr(teamService.gtAgentTaskManager, "delete_tasks_by_team", AsyncMock(return_value=0))
    monkeypatch.setattr(teamService.gtAgentHistoryManager, "delete_history_by_team", AsyncMock(return_value=0))
    monkeypatch.setattr(teamService.gtRoomMessageManager, "delete_messages_by_team", AsyncMock(return_value=0))
    monkeypatch.setattr(teamService.gtAgentActivityManager, "delete_activities_by_team", AsyncMock(return_value=8))

    rooms = [
        SimpleNamespace(id=1, tags=["DEPT"]),
        SimpleNamespace(id=2, tags=[]),
        SimpleNamespace(id=3, tags=None),
        SimpleNamespace(id=4, tags=["DEPT", "OTHER"]),
        SimpleNamespace(id=5, tags=["CUSTOM"]),
    ]
    monkeypatch.setattr(teamService.gtRoomManager, "get_rooms_by_team", AsyncMock(return_value=rooms))
    delete_mock = AsyncMock(return_value=3)
    monkeypatch.setattr(teamService.gtRoomManager, "delete_rooms_by_ids", delete_mock)
    reset_mock = AsyncMock()
    monkeypatch.setattr(teamService.gtRoomManager, "reset_room_runtime_state", reset_mock)

    result = await teamService.clear_team_data(1)

    assert result["rooms"] == 3
    assert result["activities"] == 8
    # 只删除非 DEPT 房间: id 2 (tags=[]), 3 (tags=None), 5 (tags=["CUSTOM"])
    delete_mock.assert_awaited_once()
    deleted_ids = delete_mock.await_args.args[0]
    assert set(deleted_ids) == {2, 3, 5}
    reset_mock.assert_awaited_once_with(1)

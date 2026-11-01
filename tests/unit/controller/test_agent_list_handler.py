"""Agent 列表接口：controller 只做参数解析与团队校验，组装交给 agentService。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from controller import agentController
from controller.agentController import AgentListInfo


async def test_list_handler_delegates_to_service(monkeypatch):
    """查询参数解析成 service 入参后，响应原样返回 service 的模型。"""
    monkeypatch.setattr(
        agentController.gtTeamManager,
        "get_team_by_id",
        AsyncMock(return_value=SimpleNamespace(id=1)),
    )

    received: dict = {}

    async def _list(team_id: int, *, include_special: bool = False):
        received["team_id"] = team_id
        received["include_special"] = include_special
        return []

    monkeypatch.setattr(agentController.agentService, "get_team_agent_list_info", _list)

    captured: list = []
    handler = object.__new__(agentController.AgentListHandler)
    handler.request = SimpleNamespace(
        query_arguments={"team_id": [b"1"], "include_special": [b"true"]}
    )
    handler.return_json = captured.append

    await handler.get()

    assert captured == [AgentListInfo()]
    assert received == {"team_id": 1, "include_special": True}


async def test_list_handler_returns_empty_when_team_id_missing():
    """没有 team_id 时直接返回空列表，不触发任何查询。"""
    captured: list = []
    handler = object.__new__(agentController.AgentListHandler)
    handler.request = SimpleNamespace(query_arguments={})
    handler.return_json = captured.append

    await handler.get()

    assert captured == [AgentListInfo()]

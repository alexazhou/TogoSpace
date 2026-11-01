"""Team 层部门树编排的单元测试。

部门树写入属于 deptService，在岗状态同步属于 agentService；两者由
teamService.overwrite_team_dept_tree 组合，避免 deptService 反向依赖 agentService。
"""

from unittest.mock import AsyncMock

import pytest

from service import teamService


@pytest.mark.asyncio
async def test_overwrite_team_dept_tree_syncs_agent_status_after_saving_tree(monkeypatch):
    """先覆盖部门树，再按新树同步成员在岗状态，顺序不可反。"""
    order: list[str] = []
    root = object()

    async def _overwrite(_team_id: int, _root) -> None:
        assert _root is root
        order.append("overwrite_tree")

    async def _sync(_team_id: int) -> None:
        order.append("sync_agent_status")

    monkeypatch.setattr(teamService.deptService, "overwrite_dept_tree", _overwrite)
    monkeypatch.setattr(teamService, "_sync_team_agent_status_with_dept_tree", _sync)

    await teamService.overwrite_team_dept_tree(7, root)  # type: ignore[arg-type]

    assert order == ["overwrite_tree", "sync_agent_status"]


@pytest.mark.asyncio
async def test_sync_agent_status_uses_all_agents_in_dept_tree(monkeypatch):
    """在岗状态以部门树的完整成员集合为准，不含树外成员。"""
    tree = AsyncMock()
    tree.collect_dept_and_agent_ids = lambda: ({11, 12}, {1})
    monkeypatch.setattr(teamService.deptService, "get_dept_tree", AsyncMock(return_value=tree))
    overwrite = AsyncMock(return_value=(2, 1))
    monkeypatch.setattr(teamService.agentService, "overwrite_team_agent_employ_status", overwrite)

    await teamService._sync_team_agent_status_with_dept_tree(7)

    overwrite.assert_awaited_once_with(7, {11, 12})

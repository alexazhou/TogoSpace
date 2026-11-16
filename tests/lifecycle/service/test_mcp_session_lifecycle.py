"""`mcpSessionService` 启停与资源清理（#291 / step3_tasks.md §2.2 §2.3 的 lifecycle 层）。

为什么放 lifecycle：这些判据关心的不是协议报文，而是**连接对象的身份与生命周期** ——
同一 session 内是否复用同一个连接、stop 后是否真的关掉、两个 Agent 是否各持一份、
归属清理是否只清理自己那一份。判据只依赖真 stdio 子进程与模块状态，不碰数据库与网络。

外部 MCP server 用 `mcp.server.fastmcp.FastMCP` 现场生成脚本（真子进程、真协议），
不是 mock —— 「连接是否真的建立/关闭」正是本文件要证明的东西。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from service import mcpSessionService as svc

_SERVER_TEMPLATE = '''
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("{name}")


@mcp.tool()
def add(a: int, b: int) -> int:
    """两数相加"""
    return a + b


mcp.run()
'''


def _server_command(tmp_path: Path, name: str = "life") -> str:
    """生成一个真 stdio MCP server 脚本，返回可直接启动的完整命令。"""
    script = tmp_path / f"mcp_server_{name}.py"
    script.write_text(_SERVER_TEMPLATE.format(name=name), encoding="utf-8")
    return f"{sys.executable} {script}"


@pytest.fixture(autouse=True)
async def _clean_sessions():
    """每个用例前后都清空模块态：这些用例共用进程级 `_sessions`。"""
    await svc.shutdown()
    svc.reset_for_tests()
    yield
    await svc.shutdown()
    svc.reset_for_tests()


@pytest.mark.asyncio
async def test_start_is_idempotent_and_opens_no_connection(tmp_path: Path):
    """§2.2：start 不需要名称/server；未访问服务时 status 显示已开启且没有连接。

    也覆盖「未开启时 status 正常返回」「未开启时 stop 成功」两条。
    """
    never = await svc.session_status(1, 1)
    assert never["active"] is False
    assert never["connections"] == []
    assert never["connection_count"] == 0

    first = await svc.start_session(1, 1)
    assert first["active"] is True
    assert first["connection_count"] == 0, "start 不得预建连接（step2 §4.2）"

    second = await svc.start_session(1, 1)
    assert second["active"] is True
    assert second["connection_count"] == 0

    assert (await svc.stop_session(1, 1))["active"] is False
    # 再 stop 一次仍成功（幂等），且不会把别的 Agent 拖下水
    again = await svc.stop_session(1, 1)
    assert again["active"] is False
    assert await svc.session_status(1, 2) == {
        "active": False,
        "team_id": 1,
        "agent_id": 2,
        "connections": [],
        "connection_count": 0,
    }


@pytest.mark.asyncio
async def test_session_reuses_one_connection_and_stop_closes_it(tmp_path: Path):
    """§2.2：同一 session 内首次访问建连、再次访问复用；stop 关闭全部所属连接。"""
    command = _server_command(tmp_path)
    ref = await svc.resolve_ref("", stdio_command=command)
    await svc.start_session(1, 7)

    lease1 = await svc.acquire_connection(1, 7, ref)
    assert lease1.ephemeral is False, "活动 session 内的连接应被保留"
    assert lease1.connection.ready
    tools = await lease1.connection.invoke("list_tools")
    assert [tool.name for tool in tools.tools] == ["add"]

    lease2 = await svc.acquire_connection(1, 7, ref)
    assert lease2.connection is lease1.connection, "再次访问必须复用同一连接（不重复拉子进程）"

    status = await svc.session_status(1, 7)
    assert status["connection_count"] == 1
    assert status["connections"][0]["state"] == "ready"

    await svc.stop_session(1, 7)
    assert lease1.connection.state is svc.ConnectionState.CLOSED
    after = await svc.session_status(1, 7)
    assert after["active"] is False and after["connection_count"] == 0


@pytest.mark.asyncio
async def test_two_agents_get_independent_connections(tmp_path: Path):
    """§2.3：同 Team 两个 Agent 访问同一 server ⇒ 连接独立；A stop 不影响 B。"""
    command = _server_command(tmp_path)
    ref = await svc.resolve_ref("", stdio_command=command)
    await svc.start_session(1, 7)
    await svc.start_session(1, 8)

    lease_a = await svc.acquire_connection(1, 7, ref)
    lease_b = await svc.acquire_connection(1, 8, ref)
    assert lease_a.connection is not lease_b.connection, "连接不得跨 Agent 共享（step2 §3.1）"

    await svc.stop_session(1, 7)
    assert lease_a.connection.state is svc.ConnectionState.CLOSED
    assert lease_b.connection.ready, "A 的 stop 不得关闭 B 的连接"
    again = await lease_b.connection.invoke("list_tools")
    assert [tool.name for tool in again.tools] == ["add"]

    await svc.stop_session(1, 8)
    assert lease_b.connection.state is svc.ConnectionState.CLOSED


@pytest.mark.asyncio
async def test_without_session_uses_ephemeral_connection(tmp_path: Path):
    """§2.2：没有活动 session 时用临时连接，操作完成即关闭。"""
    command = _server_command(tmp_path)
    ref = await svc.resolve_ref("", stdio_command=command)

    lease = await svc.acquire_connection(3, 9, ref)
    assert lease.ephemeral is True
    assert lease.connection.ready
    await lease.connection.invoke("list_tools")
    await lease.release()
    assert lease.connection.state is svc.ConnectionState.CLOSED
    assert (await svc.session_status(3, 9))["connection_count"] == 0


@pytest.mark.asyncio
async def test_ownership_cleanup_is_scoped(tmp_path: Path):
    """§2.3：Agent / Team / 后端三种归属清理只清自己那一份。"""
    command = _server_command(tmp_path)
    ref = await svc.resolve_ref("", stdio_command=command)
    for team, agent in ((1, 7), (1, 8), (2, 7)):
        await svc.start_session(team, agent)
        await svc.acquire_connection(team, agent, ref)

    assert await svc.close_agent_session(1, 7) is True
    assert (await svc.session_status(1, 7))["active"] is False
    assert (await svc.session_status(1, 8))["connection_count"] == 1
    assert (await svc.session_status(2, 7))["connection_count"] == 1

    assert await svc.close_team_sessions(1) == 1  # 只剩 (1, 8)
    assert (await svc.session_status(1, 8))["connection_count"] == 0
    assert (await svc.session_status(2, 7))["connection_count"] == 1

    assert await svc.shutdown() == 1  # (2, 7)
    assert svc.active_session_count() == 0

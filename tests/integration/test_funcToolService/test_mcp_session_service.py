"""`mcpSessionService` × 注册表 × 真 stdio 多 server 闭环（#291 / step3_tasks.md §2.2、§2.3）。

分层依据（step3 §3）：多 server 协议闭环放 integration。本文件把**真注册表（临时 SQLite）**
与**真 stdio MCP server 子进程**接起来，验证三件事：

1. 同一 session 访问两个不同 server ⇒ 各自建连、各自保留、再次访问各自复用；
2. 已注册项的 `resolve_ref` 走注册表（name 命中），禁用后**在建立连接之前**就被拒；
3. 配置变更（target 变了）⇒ 活动 session 里的旧连接关闭、下次访问按新配置重建；
   `invalidate_server` 主动失效也能关掉它。

这些都是「状态与归属」判据，不看报文内容，所以用真库 + 真子进程而不是 mock。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from container_db_schema import migrate_all
from model.dbModel.base import bind_database, unbind_database
from service import mcpRegistryService as registry
from service import mcpSessionService as svc
from service.ormService import AioSqliteDatabase

_SERVER = '''
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("{name}")


@mcp.tool()
def {tool}(x: str) -> str:
    """回显"""
    return "echo:" + x


mcp.run()
'''


def _server_command(tmp_path: Path, name: str, tool: str) -> str:
    script = tmp_path / f"server_{name}.py"
    script.write_text(_SERVER.format(name=name, tool=tool), encoding="utf-8")
    return f"{sys.executable} {script}"


@pytest.fixture
async def mcp_db(tmp_path: Path):
    db_path = tmp_path / "m291.db"
    applied = migrate_all(db_path, workspace_root=tmp_path)
    assert "0023_add_mcp_servers.sql" in applied
    db = AioSqliteDatabase(str(db_path), pool_params={"timeout": 30})
    bind_database(db)
    await db.aio_connect()
    try:
        yield db_path
    finally:
        await svc.shutdown()
        svc.reset_for_tests()
        await db.aio_close()
        unbind_database()


@pytest.mark.asyncio
async def test_one_session_keeps_two_servers_and_reuses_each(mcp_db, tmp_path: Path):
    """§2.2：同一 session 访问多个 server，分别按需建连；再次访问各自复用。"""
    cmd_a = _server_command(tmp_path, "alpha", "echo_a")
    cmd_b = _server_command(tmp_path, "beta", "echo_b")
    await registry.save_server(name="alpha", kind="mcp", protocol="stdio", target=cmd_a)
    await registry.save_server(name="beta", kind="mcp", protocol="stdio", target=cmd_b)

    ref_a = await svc.resolve_ref("alpha")
    ref_b = await svc.resolve_ref("beta")
    assert ref_a.kind == svc.REGISTERED and ref_a.name == "alpha"
    assert ref_a.protocol == "stdio"
    assert ref_b.key != ref_a.key

    await svc.start_session(5, 1)
    lease_a = await svc.acquire_connection(5, 1, ref_a)
    tools_a = await lease_a.connection.invoke("list_tools")
    assert [t.name for t in tools_a.tools] == ["echo_a"]

    lease_b = await svc.acquire_connection(5, 1, ref_b)
    tools_b = await lease_b.connection.invoke("list_tools")
    assert [t.name for t in tools_b.tools] == ["echo_b"]
    assert lease_b.connection is not lease_a.connection

    status = await svc.session_status(5, 1)
    assert status["connection_count"] == 2
    assert {c["server"] for c in status["connections"]} == {"alpha", "beta"}

    # 再访问两个 server：各自复用，不新建
    again_a = await svc.acquire_connection(5, 1, ref_a)
    again_b = await svc.acquire_connection(5, 1, ref_b)
    assert again_a.connection is lease_a.connection
    assert again_b.connection is lease_b.connection

    await svc.stop_session(5, 1)
    assert lease_a.connection.state is svc.ConnectionState.CLOSED
    assert lease_b.connection.state is svc.ConnectionState.CLOSED


@pytest.mark.asyncio
async def test_disabled_registered_server_rejected_before_connect(mcp_db, tmp_path: Path):
    """§2.3 / §4.1：禁用（≠删除）的 server 在解析阶段就被拒，不尝试建连。"""
    cmd = _server_command(tmp_path, "gamma", "echo_g")
    await registry.save_server(name="gamma", kind="mcp", protocol="stdio", target=cmd)
    await registry.set_enabled("gamma", False)

    with pytest.raises(Exception) as caught:
        await svc.resolve_ref("gamma")
    assert getattr(caught.value, "error_code", "") == "mcp_server_disabled"

    await svc.start_session(5, 2)
    assert (await svc.session_status(5, 2))["connection_count"] == 0, "被拒时不得留下连接"


@pytest.mark.asyncio
async def test_config_change_rebuilds_connection(mcp_db, tmp_path: Path):
    """§2.3 / §4.1：配置更新（target 变）⇒ 旧连接失效关闭，下次访问按新配置重建。"""
    cmd_old = _server_command(tmp_path, "delta_old", "echo_d")
    cmd_new = _server_command(tmp_path, "delta_new", "echo_d2")
    await registry.save_server(name="delta", kind="mcp", protocol="stdio", target=cmd_old)

    ref = await svc.resolve_ref("delta")
    await svc.start_session(5, 3)
    lease_old = await svc.acquire_connection(5, 3, ref)

    await registry.save_server(
        name="delta", kind="mcp", protocol="stdio", target=cmd_new, overwrite=True
    )
    ref_new = await svc.resolve_ref("delta")
    assert ref_new.signature != ref.signature, "覆盖登记后签名必须变化"

    lease_new = await svc.acquire_connection(5, 3, ref_new)
    assert lease_new.connection is not lease_old.connection, "配置变了必须重建连接"
    assert lease_old.connection.state is svc.ConnectionState.CLOSED
    tools = await lease_new.connection.invoke("list_tools")
    assert [t.name for t in tools.tools] == ["echo_d2"], "新连接必须用新配置的 server"

    # 主动失效入口：注册表写入路径可以关掉活动 session 里对应连接
    await registry.delete_server("delta")
    closed = await svc.invalidate_server("delta")
    assert closed == 1
    assert lease_new.connection.state is svc.ConnectionState.CLOSED


@pytest.mark.asyncio
async def test_removed_config_closes_connection_on_next_access(mcp_db, tmp_path: Path):
    """§4.1：删除配置后，活动 session 里该 server 的连接按「未找到」报错并关闭。"""
    cmd = _server_command(tmp_path, "epsilon", "echo_e")
    await registry.save_server(name="epsilon", kind="mcp", protocol="stdio", target=cmd)
    ref = await svc.resolve_ref("epsilon")

    await svc.start_session(5, 4)
    lease = await svc.acquire_connection(5, 4, ref)
    assert lease.connection.ready

    await registry.delete_server("epsilon")
    with pytest.raises(Exception) as caught:
        await svc.acquire_connection(5, 4, ref)
    assert getattr(caught.value, "error_code", "") == "mcp_registered_server_not_found"
    assert lease.connection.state is svc.ConnectionState.CLOSED
    assert (await svc.session_status(5, 4))["connection_count"] == 0


# --------------------------------------------------------------------------- #
# T5：生命周期钩子（team 卸载 / 进程关停）——真子进程，看子进程会不会留下
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_unload_team_closes_only_that_team_sessions(mcp_db, tmp_path: Path):
    """§2.2/§2.3 归属清理：`agentService.unload_team(t)` 只关 t 的会话。

    判据打在**可观察结果**上：team 7 的 session 变 inactive 且连接关闭；
    team 8 的 session 仍 active、连接仍 ready —— 清理不能跨 team 误伤。
    """
    import service.agentService.core as agent_core

    cmd = _server_command(tmp_path, "zeta", "echo_z")
    await registry.save_server(name="zeta", kind="mcp", protocol="stdio", target=cmd)
    ref = await svc.resolve_ref("zeta")

    await svc.start_session(7, 1)
    lease_7 = await svc.acquire_connection(7, 1, ref)
    await svc.start_session(8, 1)
    lease_8 = await svc.acquire_connection(8, 1, ref)
    assert lease_7.connection.ready and lease_8.connection.ready

    await agent_core.unload_team(7)

    status_7 = await svc.session_status(7, 1)
    assert status_7["active"] is False
    assert status_7["connection_count"] == 0
    assert lease_7.connection.state is svc.ConnectionState.CLOSED

    status_8 = await svc.session_status(8, 1)
    assert status_8["active"] is True, "别的 team 的 session 不能被连带关闭"
    assert status_8["connection_count"] == 1
    assert lease_8.connection.state is svc.ConnectionState.READY


@pytest.mark.asyncio
async def test_agent_service_reset_closes_all_sessions(mcp_db, tmp_path: Path):
    """`agentService` 的进程级清理（`_reset`）⇒ MCP 会话全关（stdio 子进程不留孤儿）。

    为什么打 `_reset()` 而不是 `shutdown()`：`shutdown` 被 `service_stop_wrapper` 包住，
    `Idle` 态直接 no-op（本测试不启动整套 agentService，起不来）。而 `_reset()` 正是
    「正常 shutdown 与启动失败回滚共用」的那份纯清理实现（serviceUtil 文档契约），
    `shutdown` 的 body 就是 `await _reset()`——钩子挂在真正释放资源的地方，测它就够。
    """
    import service.agentService.core as agent_core

    cmd = _server_command(tmp_path, "eta", "echo_eta")
    await registry.save_server(name="eta", kind="mcp", protocol="stdio", target=cmd)
    ref = await svc.resolve_ref("eta")

    await svc.start_session(9, 2)
    lease = await svc.acquire_connection(9, 2, ref)
    assert lease.connection.ready
    assert svc.active_session_count() == 1

    await agent_core._reset()

    assert svc.active_session_count() == 0
    assert lease.connection.state is svc.ConnectionState.CLOSED
    assert (await svc.session_status(9, 2))["active"] is False

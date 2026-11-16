"""MCP 原生 session service（#291，按 `docs/design/2026-11-16-mcp-native-session/step2_technical.md`）。

职责：维护 `(team_id, agent_id) -> AgentMcpSession` 的有状态会话 —— 长连接管理、并发锁、
配置快照校验、初始化/关闭清理。**不负责 CLI 解析与结果格式化**（那在
`funcToolService/console*`），也不接管 OpenAPI / GraphQL（它们继续走 `mcpTool` 的
mcp2cli 路径，见 step2 §1.3）。

## 归属与隔离（step2 §3.1）

- **会话键** `SessionKey = (team_id, agent_id)`，只能由可信 `ToolCallContext` 提供；
  CLI 入参无法覆盖身份，缺身份直接拒绝（不回退为全局会话）。
- **连接键** `ServerKey` 三种：已注册 `("registered", name)`、临时 URL
  `("adhoc_url", normalized_url)`、临时 stdio `("adhoc_stdio", command_str)`。
- **绝不跨 Agent 复用连接**：不同 Agent 访问同一 server 各自持有一份 transport
  （stdio 子进程 / HTTP 客户端）。

## 连接生命周期（step2 §4.1–4.2）

`mcp` SDK 的 transport 与 `ClientSession` 都是 anyio 上下文管理器，**进入与退出必须在
同一个 asyncio Task**。因此每个 `ManagedMcpConnection` 用一条**专用 owner task** 持有
`contextlib.AsyncExitStack`：任务内进入 transport + session、`initialize()`、置 ready，
然后等 `close_event`；关闭方只 set event 并等 owner 自然退出，超时才 cancel。
这样 stdio 子进程回收、HTTP 客户端关闭都发生在 owner task 内。

状态机：`CONNECTING -> READY -> CLOSING -> CLOSED`。`CONNECTING` 也被记录，`stop` /
归属清理会连初始化中的连接一起取消并等待收敛（不留残留子进程）。

## 配置快照与失效（step2 §3.2）

已注册连接建立时记下 `(kind, protocol, target, config)` 签名；每次从活动 session 取连接
前先 `mcpRegistryService.resolve_name` 复核：条目消失/被禁用 ⇒ 关闭并报错；签名变了 ⇒
关旧建新。`invalidate_server(name)` 供注册表写入路径主动失效（本模块只提供入口，接线
由调用方决定）。

## 与 mcp2cli 的关系

本服务只用 Python `mcp` SDK（`mcp==1.27.1`，requirements.txt:21），**不拉 daemon、
不读 stdin、不依赖 `sys.executable` 子进程**，所以打包产物里同样可用 —— 这正是
`mcpTool._SESSION_EXTRA_REASON` 里「打包产物没有 Python 解释器」那条限制的替代路径。
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import shlex
from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum
from collections.abc import Coroutine
from typing import Any, Optional

from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client

from exception import TogoException
from model.dbModel.gtMcpServer import mask_target
from service import mcpRegistryService

logger = logging.getLogger(__name__)

#: 建连 + `initialize()` 的上限。远端冷启动（npx 拉包）可能要几秒，30s 足够复现失败。
CONNECT_TIMEOUT = 30.0
#: 单次协议调用的上限（`list_tools` / `call_tool` / `list_resources` / `read_resource`）。
CALL_TIMEOUT = 120.0
#: 关闭一条连接时等 owner task 自然退出的上限，超时后 cancel。
CLOSE_TIMEOUT = 10.0

_ERROR_INVALID_IDENTITY = "mcp_session_invalid_identity"
_ERROR_NAME_NOT_FOUND = "mcp_name_not_found"
_ERROR_SERVER_DISABLED = "mcp_server_disabled"
_ERROR_CONNECT_FAILED = "mcp_session_connect_failed"
_ERROR_TIMEOUT = "mcp_session_timeout"
_ERROR_CLOSED = "mcp_session_connection_closed"
_ERROR_NOT_FOUND = "mcp_registered_server_not_found"
_ERROR_INVALID_TARGET = "mcp_invalid_target"


def _fail(error_code: str, message: str) -> TogoException:
    return TogoException(message, error_code=error_code)


def _require_identity(team_id: Any, agent_id: Any) -> tuple[int, int]:
    """会话键只从可信上下文来；缺任一段直接拒（step2 §3.1，不回退全局会话）。"""
    try:
        team = int(team_id)
        agent = int(agent_id)
    except (TypeError, ValueError):
        raise _fail(
            _ERROR_INVALID_IDENTITY,
            "MCP session 必须由带可信身份的调用发起（缺 team_id 或 agent_id）。",
        ) from None
    if team <= 0 or agent <= 0:
        raise _fail(
            _ERROR_INVALID_IDENTITY,
            f"MCP session 身份不合法（team_id={team_id!r}, agent_id={agent_id!r}）。",
        )
    return team, agent


# --------------------------------------------------------------------------- #
# 连接键与来源描述
# --------------------------------------------------------------------------- #

#: 连接键：`("registered", name)` / `("adhoc_url", url)` / `("adhoc_stdio", command)`。
ServerKey = tuple[str, str]

REGISTERED = "registered"
ADHOC_URL = "adhoc_url"
ADHOC_STDIO = "adhoc_stdio"


@dataclass(frozen=True)
class ServerRef:
    """一次访问的目标（已解析成传输层可直接用的形态）。

    `name` 只在 `kind == "registered"` 时有值；`signature` 是配置快照指纹，用于
    「地址/协议/凭证变了 ⇒ 关旧建新」（step2 §3.2）。
    """

    kind: str
    target: str
    protocol: str
    config: dict[str, Any] = field(default_factory=dict)
    name: Optional[str] = None

    @property
    def key(self) -> ServerKey:
        if self.kind == REGISTERED:
            return (REGISTERED, self.name or "")
        if self.kind == ADHOC_STDIO:
            return (ADHOC_STDIO, _normalize_command(self.target))
        return (ADHOC_URL, _normalize_url(self.target))

    @property
    def signature(self) -> str:
        payload = json.dumps(
            {"target": self.target, "protocol": self.protocol, "config": self.config},
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    @property
    def display(self) -> str:
        if self.kind == REGISTERED:
            return str(self.name)
        return mask_target(self.target)


def _normalize_url(url: str) -> str:
    return str(url or "").strip().rstrip("/")


def _normalize_command(command: str) -> str:
    return " ".join(str(command or "").split())


def _stdio_parts(command: str) -> tuple[str, list[str]]:
    """把「完整启动命令」拆成 command + args（`shlex` 口径，与 console 的引号规则一致）。"""
    parts = shlex.split(command, posix=True)
    if not parts:
        raise _fail(_ERROR_INVALID_TARGET, "stdio 启动命令不能为空。")
    return parts[0], parts[1:]


async def resolve_ref(
    target: str,
    *,
    protocol: Optional[str] = None,
    stdio_command: Optional[str] = None,
    config: Optional[dict[str, Any]] = None,
) -> ServerRef:
    """把 CLI 传入的选择器解析成 `ServerRef`（注册表优先，其次临时 URL / stdio）。

    解析点只有这一处：`mcp list` / `mcp call` / `mcp resource` 全部复用（同一语义两处
    实现必然漂移）。名字命中注册表的判断与 `tools.mcp_call` 一致，都走
    `mcpRegistryService.resolve_name`。
    """
    if stdio_command:
        return ServerRef(
            kind=ADHOC_STDIO,
            target=str(stdio_command),
            protocol="stdio",
            config=dict(config or {}),
        )
    text = str(target or "").strip()
    if not text:
        raise _fail(_ERROR_INVALID_TARGET, "缺少目标：给已配置的 server 名称、URL 或 --stdio 命令。")

    entry = await mcpRegistryService.resolve_name(text)
    if entry is not None:
        if not entry.enable:
            raise _fail(
                _ERROR_SERVER_DISABLED,
                f"server {entry.name!r} 已禁用（enable=0），不能使用。"
                f"让 leader 执行 `mcp enable {entry.name}` 后重试。",
            )
        if entry.kind != "mcp":
            raise _fail(
                _ERROR_INVALID_TARGET,
                f"{entry.name!r} 登记的 kind 是 {entry.kind!r}，不进入 MCP session；"
                "OpenAPI / GraphQL 请继续用既有 mcp2cli 入口（mcp call --kind …）。",
            )
        return ServerRef(
            kind=REGISTERED,
            name=entry.name,
            target=entry.target,
            protocol=entry.protocol or "streamable_http",
            config=mcpRegistryService.config_of(entry),
        )

    lowered = text.lower()
    if lowered.startswith("http://") or lowered.startswith("https://"):
        proto = str(protocol or "streamable_http").strip() or "streamable_http"
        if proto not in ("sse", "streamable_http"):
            raise _fail(
                _ERROR_INVALID_TARGET,
                f"URL 来源的 protocol 只能是 sse / streamable_http，收到 {proto!r}。",
            )
        return ServerRef(kind=ADHOC_URL, target=text, protocol=proto, config=dict(config or {}))

    raise _fail(
        _ERROR_NAME_NOT_FOUND,
        f"没有名为 {text!r} 的已配置 server；临时来源请给 http(s) URL 或 `--stdio '<命令>'`。"
        "`mcp config list` 可看现有配置。",
    )


# --------------------------------------------------------------------------- #
# 连接
# --------------------------------------------------------------------------- #


class ConnectionState(str, Enum):
    CONNECTING = "connecting"
    READY = "ready"
    CLOSING = "closing"
    CLOSED = "closed"


class ManagedMcpConnection:
    """一条被 owner task 持有的 MCP 连接。

    外部只用 `open()` / `close()` / `call()`；`owner_task` 之外**不得**直接操作
    transport 或 `ClientSession`（跨 task 退出 anyio 上下文会报错，step2 §4.1）。
    """

    def __init__(self, ref: ServerRef) -> None:
        self.ref = ref
        self.state = ConnectionState.CONNECTING
        self._session: Optional[ClientSession] = None
        self._owner: Optional[asyncio.Task[None]] = None
        self._ready = asyncio.Event()
        self._closed = asyncio.Event()
        self._close_event = asyncio.Event()
        self._error: Optional[BaseException] = None

    # -- 只读视图 ---------------------------------------------------------- #
    @property
    def ready(self) -> bool:
        return self.state is ConnectionState.READY and self._session is not None

    @property
    def session(self) -> ClientSession:
        if self._session is None:
            raise _fail(_ERROR_CLOSED, f"连接 {self.ref.display} 已关闭。")
        return self._session

    def status_dict(self) -> dict[str, Any]:
        return {
            "server": self.ref.display,
            "protocol": self.ref.protocol,
            "state": self.state.value,
            "registered": self.ref.kind == REGISTERED,
        }

    # -- 建连 -------------------------------------------------------------- #
    async def open(self) -> None:
        if self._owner is not None:
            raise RuntimeError("同一连接不能 open 两次")
        self._owner = asyncio.create_task(self._run(), name=f"mcp-conn:{self.ref.display}")
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=CONNECT_TIMEOUT)
        except asyncio.TimeoutError:
            await self.close()
            raise _fail(
                _ERROR_TIMEOUT,
                f"连接 {self.ref.display} 超时（{CONNECT_TIMEOUT:g}s 内未完成初始化）。",
            ) from None
        if self._error is not None:
            err = self._error
            await self.close()
            raise _fail(
                _ERROR_CONNECT_FAILED,
                f"连接 {self.ref.display} 失败：{type(err).__name__}: {err}",
            )

    def _transport(self) -> Any:
        headers = dict(self.ref.config.get("headers") or {})
        if self.ref.protocol == "stdio":
            command, args = _stdio_parts(self.ref.target)
            env = dict(os.environ)
            env.update({str(k): str(v) for k, v in (self.ref.config.get("env") or {}).items()})
            params = StdioServerParameters(command=command, args=args, env=env)
            return stdio_client(params)
        if self.ref.protocol == "sse":
            return sse_client(self.ref.target, headers=headers or None, timeout=CONNECT_TIMEOUT)
        return streamablehttp_client(
            self.ref.target, headers=headers or None, timeout=CONNECT_TIMEOUT
        )

    async def _run(self) -> None:
        """owner task：进入/退出 transport 与 session 都在本 task 内（step2 §4.1）。"""
        try:
            async with contextlib.AsyncExitStack() as stack:
                entered = await stack.enter_async_context(self._transport())
                read_stream, write_stream = entered[0], entered[1]
                session = await stack.enter_async_context(
                    ClientSession(
                        read_stream,
                        write_stream,
                        read_timeout_seconds=timedelta(seconds=CALL_TIMEOUT),
                    )
                )
                await session.initialize()
                self._session = session
                self.state = ConnectionState.READY
                self._ready.set()
                await self._close_event.wait()
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001 - 建连失败原因要原样带给调用方
            self._error = exc
            logger.info("mcp session: 连接 %s 退出：%s: %s", self.ref.display, type(exc).__name__, exc)
        finally:
            self.state = ConnectionState.CLOSED
            self._session = None
            self._ready.set()
            self._closed.set()

    # -- 关闭 -------------------------------------------------------------- #
    async def close(self) -> None:
        """幂等关闭；等 owner task 自然退出（超时则 cancel 并收敛）。"""
        if self.state is ConnectionState.CLOSED and self._owner is None:
            return
        if self.state is not ConnectionState.CLOSING and self.state is not ConnectionState.CLOSED:
            self.state = ConnectionState.CLOSING
        self._close_event.set()
        self._ready.set()
        owner = self._owner
        if owner is None:
            self.state = ConnectionState.CLOSED
            self._closed.set()
            return
        try:
            await asyncio.wait_for(self._closed.wait(), timeout=CLOSE_TIMEOUT)
        except asyncio.TimeoutError:
            owner.cancel()
            with contextlib.suppress(BaseException):
                await owner
            self.state = ConnectionState.CLOSED
            self._closed.set()
            logger.warning("mcp session: 连接 %s 关闭超时，已取消 owner task", self.ref.display)
        self.state = ConnectionState.CLOSED

    async def invoke(self, action: str, **kwargs: Any) -> Any:
        """在 ready 连接上执行一次协议调用（`asyncio.wait_for` 兜超时）。"""
        session = self.session
        coro: Coroutine[Any, Any, Any]
        if action == "list_tools":
            coro = session.list_tools(**kwargs)
        elif action == "call_tool":
            coro = session.call_tool(**kwargs)
        elif action == "list_resources":
            coro = session.list_resources(**kwargs)
        elif action == "read_resource":
            coro = session.read_resource(**kwargs)
        else:
            raise _fail(_ERROR_INVALID_TARGET, f"未知的 MCP 动作：{action!r}。")
        try:
            return await asyncio.wait_for(coro, timeout=CALL_TIMEOUT)
        except asyncio.TimeoutError:
            raise _fail(
                _ERROR_TIMEOUT,
                f"{self.ref.display} 的 {action} 超时（{CALL_TIMEOUT:g}s）；结果未知，不会自动重放。",
            ) from None


# --------------------------------------------------------------------------- #
# 会话
# --------------------------------------------------------------------------- #


class AgentMcpSession:
    """单个 Agent 的活动会话：`active` + 按 `ServerKey` 索引的连接 + 每键一把建连锁。"""

    def __init__(self, team_id: int, agent_id: int) -> None:
        self.team_id = team_id
        self.agent_id = agent_id
        self.active = False
        self.connections: dict[ServerKey, ManagedMcpConnection] = {}
        self._locks: dict[ServerKey, asyncio.Lock] = {}

    def lock_for(self, key: ServerKey) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def status_dict(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "team_id": self.team_id,
            "agent_id": self.agent_id,
            "connections": [conn.status_dict() for conn in self.connections.values()],
            "connection_count": len(self.connections),
        }

    async def close_all(self) -> None:
        conns = list(self.connections.values())
        self.connections.clear()
        self._locks.clear()
        if conns:
            await asyncio.gather(*(conn.close() for conn in conns), return_exceptions=True)


class ConnectionLease:
    """一次访问借到的连接。`ephemeral` 表示「无活动 session 时的临时连接」，用完即关。"""

    def __init__(self, connection: ManagedMcpConnection, *, ephemeral: bool) -> None:
        self.connection = connection
        self.ephemeral = ephemeral

    async def release(self) -> None:
        if self.ephemeral:
            await self.connection.close()


_sessions: dict[tuple[int, int], AgentMcpSession] = {}


def _session_of(key: tuple[int, int]) -> Optional[AgentMcpSession]:
    return _sessions.get(key)


def _get_or_create(key: tuple[int, int]) -> AgentMcpSession:
    session = _sessions.get(key)
    if session is None:
        session = AgentMcpSession(*key)
        _sessions[key] = session
    return session


# --------------------------------------------------------------------------- #
# 对外 API（session 三条命令 + 连接获取 + 归属清理）
# --------------------------------------------------------------------------- #


async def start_session(team_id: Any, agent_id: Any) -> dict[str, Any]:
    """`mcp session start`：**幂等**开启，不预建连接、不重置已有连接（产品 §3.1）。"""
    key = _require_identity(team_id, agent_id)
    session = _get_or_create(key)
    session.active = True
    logger.info("mcp session: 开启 team=%s agent=%s（已有连接 %d）", *key, len(session.connections))
    return session.status_dict()


async def session_status(team_id: Any, agent_id: Any) -> dict[str, Any]:
    """`mcp session status`：只返回**当前 Agent** 的状态；未开启也正常返回。"""
    key = _require_identity(team_id, agent_id)
    session = _session_of(key)
    if session is None:
        return {
            "active": False,
            "team_id": key[0],
            "agent_id": key[1],
            "connections": [],
            "connection_count": 0,
        }
    return session.status_dict()


async def stop_session(team_id: Any, agent_id: Any) -> dict[str, Any]:
    """`mcp session stop`：关闭全部连接（含初始化中）并结束会话；未开启也算成功。"""
    key = _require_identity(team_id, agent_id)
    session = _sessions.pop(key, None)
    if session is None:
        return {"active": False, "team_id": key[0], "agent_id": key[1], "connections": [], "connection_count": 0}
    was_active = session.active
    session.active = False
    await session.close_all()
    logger.info("mcp session: 停止 team=%s agent=%s（原 active=%s）", *key, was_active)
    return {
        "active": False,
        "team_id": key[0],
        "agent_id": key[1],
        "connections": [],
        "connection_count": 0,
    }


async def acquire_connection(team_id: Any, agent_id: Any, ref: ServerRef) -> ConnectionLease:
    """取一条可用连接：活动 session 内**复用/懒建**并按需保留；否则给一次性连接。

    并发安全：同一 `ServerKey` 的建连由该键的锁串行化（step2 §4.2），第二个调用者拿到
    的是第一个建好的连接，不会重复拉起 stdio 子进程。
    """
    key = _require_identity(team_id, agent_id)
    session = _get_or_create(key)
    conn_key = ref.key

    async with session.lock_for(conn_key):
        existing = session.connections.get(conn_key)
        if existing is not None:
            if not session.active:
                # 会话已停但连接还在：按一次性连接交出去，用完关。
                session.connections.pop(conn_key, None)
                if existing.ready:
                    return ConnectionLease(existing, ephemeral=True)
                await existing.close()
            else:
                error, stale = await _check_existing(ref, existing)
                if error is not None:
                    session.connections.pop(conn_key, None)
                    await existing.close()
                    raise _fail(*error)
                if stale or not existing.ready:
                    session.connections.pop(conn_key, None)
                    await existing.close()
                else:
                    return ConnectionLease(existing, ephemeral=False)

        connection = ManagedMcpConnection(ref)
        await connection.open()
        if session.active:
            session.connections[conn_key] = connection
            return ConnectionLease(connection, ephemeral=False)
        return ConnectionLease(connection, ephemeral=True)


async def _check_existing(
    ref: ServerRef, connection: ManagedMcpConnection
) -> tuple[Optional[tuple[str, str]], bool]:
    """配置快照懒校验（step2 §3.2）。

    返回 `(error, stale)`：
    - `error` 非空 ⇒ 条目已移除 / 已禁用，调用方**关连接并报错**；
    - `stale=True` ⇒ 已注册项的配置签名变了，调用方**关旧建新**（不报错）；
    - `(None, False)` ⇒ 可复用。

    三个状态必须分开：签名变化与「无变化」若都返回空值，配置更新后会被静默复用旧连接。
    """
    if ref.kind != REGISTERED:
        return None, False
    entry = await mcpRegistryService.resolve_name(ref.name or "")
    if entry is None:
        return (
            (
                _ERROR_NOT_FOUND,
                f"server {ref.name!r} 已从配置中移除；活动 session 里的连接已关闭。",
            ),
            False,
        )
    if not entry.enable:
        return (
            (
                _ERROR_SERVER_DISABLED,
                f"server {ref.name!r} 已被禁用；活动 session 里的连接已关闭。"
                f"让 leader 执行 `mcp enable {ref.name}` 后重试。",
            ),
            False,
        )
    latest = ServerRef(
        kind=REGISTERED,
        name=entry.name,
        target=entry.target,
        protocol=entry.protocol or "streamable_http",
        config=mcpRegistryService.config_of(entry),
    )
    if latest.signature != connection.ref.signature:
        logger.info("mcp session: %s 配置已变，关闭旧连接并重建", ref.name)
        return None, True
    return None, False


async def invalidate_server(name: str) -> int:
    """配置变更后主动失效：关闭所有活动 session 里该 server 的连接，返回关闭条数。"""
    text = str(name or "").strip()
    closed = 0
    for session in list(_sessions.values()):
        conn = session.connections.pop((REGISTERED, text), None)
        if conn is not None:
            await conn.close()
            closed += 1
    if closed:
        logger.info("mcp session: 配置变更，关闭 %s 的连接 %d 条", text, closed)
    return closed


async def close_agent_session(team_id: Any, agent_id: Any) -> bool:
    """Agent 卸载 / 重载 / 离职：关闭该 Agent 的会话（含初始化中连接）。"""
    try:
        key = _require_identity(team_id, agent_id)
    except TogoException:
        return False
    session = _sessions.pop(key, None)
    if session is None:
        return False
    session.active = False
    await session.close_all()
    logger.info("mcp session: Agent 归属清理 team=%s agent=%s", *key)
    return True


async def close_team_sessions(team_id: Any) -> int:
    """Team 停止 / 重载：关闭该 Team 名下全部 Agent 会话，返回关闭的会话数。"""
    try:
        team = int(team_id)
    except (TypeError, ValueError):
        return 0
    keys = [key for key in _sessions if key[0] == team]
    for key in keys:
        session = _sessions.pop(key, None)
        if session is not None:
            session.active = False
            await session.close_all()
    if keys:
        logger.info("mcp session: Team 归属清理 team=%s，会话 %d 个", team, len(keys))
    return len(keys)


async def shutdown() -> int:
    """后端退出：关闭全部会话（重启后不恢复，产品 §3.3）。"""
    keys = list(_sessions)
    for key in keys:
        session = _sessions.pop(key, None)
        if session is not None:
            session.active = False
            await session.close_all()
    if keys:
        logger.info("mcp session: 后端退出，清理会话 %d 个", len(keys))
    return len(keys)


def reset_for_tests() -> None:
    """测试钩子：清空模块级状态（不关闭连接——测试里应由用例自己 stop/shutdown）。"""
    _sessions.clear()


def active_session_count() -> int:
    return len(_sessions)

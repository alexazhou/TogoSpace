from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
import time
from datetime import datetime
from typing import AsyncIterator, Optional

import aiosqlite
import aiosqlite.core as _aiosqlite_core
import peewee
from peewee_async.connection import connection_context
from peewee_async.databases import AioDatabase
from peewee_async.pool import PoolBackend
from peewee_async.transactions import Transaction
from peewee_async.utils import ConnectionProtocol

import appPaths
from db import check_database_initialized, resolve_db_path
from model.coreModel.serviceStatus import ServiceStatusInfo
from model.dbModel.base import bind_database, unbind_database
from service import serviceUtil

logger = logging.getLogger(__name__)

s_status = ServiceStatusInfo(name="ormService")

# aiosqlite.Connection 继承 Thread 且默认 daemon=False，
# 若连接因 asyncio 任务取消等原因未被正常 close()，其工作线程会阻塞进程退出。
# 在 __init__ 阶段（线程 start 之前）将 daemon 设为 True，使泄漏的连接不阻塞退出。
_orig_aiosqlite_init = _aiosqlite_core.Connection.__init__

def _patched_aiosqlite_init(self, *args, **kwargs):
    _orig_aiosqlite_init(self, *args, **kwargs)
    self.daemon = True

_aiosqlite_core.Connection.__init__ = _patched_aiosqlite_init


class _SqlitePoolState:
    def __init__(self) -> None:
        self.closed = False


class SqlitePoolBackend(PoolBackend):
    """peewee-async 适配层：为 SQLite 提供异步连接获取/释放。"""

    def __init__(self, *, database: str, **kwargs) -> None:
        super().__init__(database=database, **kwargs)
        self._acquired_count = 0
        self._connections: dict[int, ConnectionProtocol] = {}  # id -> conn

    async def create(self) -> None:
        self.pool = _SqlitePoolState()

    async def acquire(self) -> ConnectionProtocol:
        if self.pool is None or self.pool.closed:
            await self.connect()
        connect_params = dict(self.connect_params)
        connect_params.setdefault("isolation_level", None)
        conn: ConnectionProtocol = await aiosqlite.connect(self.database, **connect_params)
        self._acquired_count += 1
        self._connections[id(conn)] = conn
        return conn

    async def release(self, conn: ConnectionProtocol) -> None:
        conn_id = id(conn)
        await conn.close()
        self._acquired_count = max(0, self._acquired_count - 1)
        self._connections.pop(conn_id, None)

    async def close(self) -> None:
        """关闭所有连接，确保 aiosqlite 后台线程正确退出。"""
        if self.pool is not None:
            self.pool.closed = True
        for conn_id, conn in list(self._connections.items()):
            try:
                await conn.close()
            except Exception:
                pass
        self._connections.clear()
        self._acquired_count = 0

    def has_acquired_connections(self) -> bool:
        return self._acquired_count > 0


_tx_logger = logging.getLogger(f"{__name__}.transaction")

# 超过该时长的事务升 WARNING（进 backend_warning.log），否则 INFO。
_tx_slow_ms = float(os.environ.get("TOGO_TX_SLOW_MS", "200"))


class _TxLogTransaction(Transaction):
    """根事务发 `BEGIN IMMEDIATE` 并记录等锁时长；嵌套事务仍走 `SAVEPOINT`。

    为什么必须是 IMMEDIATE：`peewee_async` 的根事务默认发裸 `BEGIN`
    （= `BEGIN DEFERRED`），延迟事务在「先读后写」升级锁时若撞上别的写者，
    SQLite 判定继续等待必然死锁，**不调用 busy handler 直接返回 SQLITE_BUSY**，
    表现为 `database is locked`，`busy_timeout` 对此无效。
    `BEGIN IMMEDIATE` 在事务开头（尚未持有任何锁时）就取 RESERVED，
    拿不到时可安全排队等待，busy handler 正常生效。
    """

    begin_sec = 0.0  # 根事务 BEGIN 等锁时长；嵌套事务不更新

    async def begin(self) -> None:
        if self.savepoint:
            await super().begin()
            return
        started = time.perf_counter()
        try:
            await self.execute("BEGIN IMMEDIATE")
        finally:
            self.begin_sec = time.perf_counter() - started


class AioSqliteDatabase(AioDatabase, peewee.SqliteDatabase):
    pool_backend_cls = SqlitePoolBackend

    @contextlib.asynccontextmanager
    async def _aio_atomic(self, use_savepoint: bool = False) -> AsyncIterator[None]:
        """覆写 peewee_async 的默认实现：根事务改 IMMEDIATE，并记录事务耗时。

        `total` 与 `begin` 相差很大 ⇒ 事务内穿插了非 DB 操作（网络/IO/等待）；
        `begin` 接近 `total` ⇒ 在等别的写者释放写锁。
        """
        async with self.aio_connection() as connection:
            ctx = connection_context.get()
            assert ctx is not None
            is_root = not ctx.transaction_is_opened
            if not is_root and not use_savepoint:
                raise peewee.OperationalError("Transaction already opened")

            tx = _TxLogTransaction(connection, is_savepoint=not is_root)
            started = time.perf_counter()
            failed = True
            try:
                async with tx:
                    if is_root:
                        ctx.transaction_is_opened = True
                    yield
                failed = False
            finally:
                if is_root:
                    ctx.transaction_is_opened = False
                    total_ms = (time.perf_counter() - started) * 1000
                    message = "DBTX %s total=%.1fms begin=%.1fms" % (
                        "ROLLBACK" if failed else "COMMIT",
                        total_ms,
                        tx.begin_sec * 1000,
                    )
                    if total_ms >= _tx_slow_ms:
                        _tx_logger.warning(message)
                    else:
                        _tx_logger.info(message)


_db: Optional[AioSqliteDatabase] = None
_db_path: Optional[str] = None


async def _reset() -> None:
    """纯资源清理：正常 shutdown 与启动失败回滚共用；幂等，可安全重复调用。"""
    global _db, _db_path
    if _db is not None:
        await _db.aio_close()
    _db = None
    _db_path = None
    unbind_database()  # 解除 DatabaseProxy 绑定，防「悬空绑定」


@serviceUtil.service_start_wrapper(s_status, on_error=_reset)
async def startup(db_path: str) -> None:
    global _db, _db_path
    # 方案 A：删除原「if _db is not None: return」静默早退；二次启动已被注解统一拦截。
    assert _db is None, "ormService.startup 时 _db 应为空（未 shutdown 的重复启动已被注解拦截）"

    # schema 迁移由 backend_main 在绑定前执行，这里只负责连接与绑定
    _db_path = db_path
    abs_path = os.path.abspath(db_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)

    # timeout 必须走 pool_params：peewee 的 timeout/pragmas 只在其同步 _connect() 生效，
    # 而异步连接池自己 aiosqlite.connect()，只认 connect_params（即 pool_params 展开）。
    database = AioSqliteDatabase(
        abs_path,
        pool_params={"timeout": 30},
    )
    bind_database(database)

    # 验证数据库是否已初始化
    if not check_database_initialized(abs_path):
        with database.allow_sync():
            database.close()
        raise RuntimeError(
            f"Database schema is not initialized. "
            f"Run '.venv/bin/python src/db.py migrate --db-path {abs_path}' first."
        )

    try:
        await database.aio_connect()
        _db = database
    except Exception:
        with database.allow_sync():
            database.close()
        raise

    logger.info("ORM service started: db=%s", abs_path)


@serviceUtil.service_stop_wrapper(s_status)
async def shutdown() -> None:
    await _reset()


def get_db() -> AioSqliteDatabase:
    if _db is None:
        raise RuntimeError("ormService not started")
    return _db


def is_ready() -> bool:
    return _db is not None and _db.is_connected


def get_db_path() -> Optional[str]:
    return _db_path


def backup_database() -> str:
    db_path = get_db_path()
    if db_path is None:
        raise RuntimeError("ormService not started")

    source_path = resolve_db_path(db_path)
    backup_dir = os.path.join(appPaths.DATA_DIR, "backups")
    os.makedirs(backup_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_path = os.path.join(
        backup_dir,
        f"{source_path.stem}_{timestamp}{source_path.suffix or '.db'}",
    )

    with sqlite3.connect(str(source_path)) as source_conn, sqlite3.connect(backup_path) as backup_conn:
        source_conn.backup(backup_conn)

    logger.info("Database backup created: source=%s, backup=%s", source_path, backup_path)
    return backup_path

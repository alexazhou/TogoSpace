from __future__ import annotations

import logging
import os
from typing import Optional

import aiosqlite
import aiosqlite.core as _aiosqlite_core
import peewee
from peewee_async.databases import AioDatabase
from peewee_async.pool import PoolBackend
from peewee_async.utils import ConnectionProtocol

from db import check_database_initialized, migrate_database
from model.dbModel.base import bind_database

logger = logging.getLogger(__name__)

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

    async def create(self) -> None:
        self.pool = _SqlitePoolState()

    async def acquire(self) -> ConnectionProtocol:
        if self.pool is None or self.pool.closed:
            await self.connect()
        connect_params = dict(self.connect_params)
        connect_params.setdefault("isolation_level", None)
        conn: ConnectionProtocol = await aiosqlite.connect(self.database, **connect_params)
        self._acquired_count += 1
        return conn

    async def release(self, conn: ConnectionProtocol) -> None:
        await conn.close()
        self._acquired_count = max(0, self._acquired_count - 1)

    async def close(self) -> None:
        if self.pool is not None:
            self.pool.closed = True

    def has_acquired_connections(self) -> bool:
        return self._acquired_count > 0


class AioSqliteDatabase(AioDatabase, peewee.SqliteDatabase):
    pool_backend_cls = SqlitePoolBackend


_db: Optional[AioSqliteDatabase] = None
_db_path: Optional[str] = None


def _needs_migration(db_path: str) -> bool:
    """检查是否需要执行迁移：数据库文件不存在。"""
    return not os.path.exists(db_path)


async def startup(db_path: str) -> None:
    global _db, _db_path
    if _db is not None:
        return

    _db_path = db_path
    abs_path = os.path.abspath(db_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)

    # 启动时始终检查并执行待处理迁移，保证已有库也能升级到最新 schema。
    if _needs_migration(abs_path):
        logger.info("Database not initialized, running migrations...")
    else:
        logger.info("Checking pending migrations for existing database...")
    applied = migrate_database(abs_path)
    if applied:
        logger.info("Applied %d migration(s): %s", len(applied), applied)
    else:
        logger.info("Database schema is up to date")

    database = AioSqliteDatabase(
        abs_path,
        timeout=30,
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


async def shutdown() -> None:
    global _db, _db_path
    if _db is not None:
        await _db.aio_close()
    _db = None
    _db_path = None


def get_db() -> AioSqliteDatabase:
    if _db is None:
        raise RuntimeError("ormService not started")
    return _db


def is_ready() -> bool:
    return _db is not None and _db.is_connected


def get_db_path() -> Optional[str]:
    return _db_path

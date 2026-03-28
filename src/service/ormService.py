from __future__ import annotations

import logging
import os
from typing import Optional

import aiosqlite
import peewee
from peewee_async.databases import AioDatabase
from peewee_async.pool import PoolBackend
from peewee_async.utils import ConnectionProtocol

from db import migrate_database
from model.dbModel.base import bind_database

logger = logging.getLogger(__name__)


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

    # 自动执行迁移
    if _needs_migration(abs_path):
        logger.info("Database not initialized, running migrations...")
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

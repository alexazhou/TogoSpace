from __future__ import annotations

import logging
import os
from typing import Optional

import aiosqlite
import peewee
from peewee_async.databases import AioDatabase
from peewee_async.pool import PoolBackend
from peewee_async.utils import ConnectionProtocol

from model.dbModel.gtAgentHistory import GtAgentHistory
from model.dbModel.gtAgent import GtAgent
from model.dbModel.base import bind_database
from model.dbModel.gtRoomMessage import GtRoomMessage
from model.dbModel.gtTeam import GtTeam
from model.dbModel.gtTeamMember import GtTeamMember
from model.dbModel.gtRoom import GtRoom
from model.dbModel.gtRoomMember import GtRoomMember

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


def _ensure_schema(database: AioSqliteDatabase) -> None:
    with database.allow_sync():
        database.create_tables(
            [GtRoomMessage, GtRoom, GtAgentHistory, GtTeam, GtTeamMember, GtRoomMember, GtAgent],
            safe=True,
        )


def _ensure_agent_model_column(database: AioSqliteDatabase) -> None:
    """兼容迁移：若 agents 表存在但缺少 model 列，自动补齐。"""
    with database.allow_sync():
        rows = database.execute_sql("PRAGMA table_info('agents')").fetchall()
        if not rows:
            return

        columns = {str(r[1]) for r in rows if len(r) > 1}
        if "model" in columns:
            return

        database.execute_sql(
            "ALTER TABLE agents ADD COLUMN model TEXT NOT NULL DEFAULT ''"
        )


def _ensure_agent_template_name_column(database: AioSqliteDatabase) -> None:
    with database.allow_sync():
        rows = database.execute_sql("PRAGMA table_info('agents')").fetchall()
        if not rows:
            return

        columns = {str(r[1]) for r in rows if len(r) > 1}
        if "template_name" in columns:
            return

        database.execute_sql(
            "ALTER TABLE agents ADD COLUMN template_name TEXT NOT NULL DEFAULT ''"
        )


def _ensure_team_working_directory_column(database: AioSqliteDatabase) -> None:
    with database.allow_sync():
        rows = database.execute_sql("PRAGMA table_info('teams')").fetchall()
        if not rows:
            return

        columns = {str(r[1]) for r in rows if len(r) > 1}
        if "working_directory" in columns:
            return

        database.execute_sql(
            "ALTER TABLE teams ADD COLUMN working_directory TEXT NOT NULL DEFAULT ''"
        )


def _ensure_team_config_column(database: AioSqliteDatabase) -> None:
    with database.allow_sync():
        rows = database.execute_sql("PRAGMA table_info('teams')").fetchall()
        if not rows:
            return

        columns = {str(r[1]) for r in rows if len(r) > 1}
        if "config" in columns:
            return

        database.execute_sql(
            "ALTER TABLE teams ADD COLUMN config TEXT NOT NULL DEFAULT '{}'"
        )


async def startup(db_path: str) -> None:
    global _db, _db_path
    if _db is not None:
        return

    _db_path = db_path
    abs_path = os.path.abspath(db_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)

    database = AioSqliteDatabase(
        abs_path,
        timeout=30,
    )
    bind_database(database)
    _ensure_schema(database)
    _ensure_agent_model_column(database)
    _ensure_agent_template_name_column(database)
    _ensure_team_working_directory_column(database)
    _ensure_team_config_column(database)
    await database.aio_connect()
    _db = database

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

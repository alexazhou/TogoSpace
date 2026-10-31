"""集成测试：`atomic()` 的事务模式与耗时日志（真库）。

覆盖：
  T1 根事务发 `BEGIN IMMEDIATE`（不是裸 BEGIN）；
  T2 嵌套事务走 `SAVEPOINT`，且只有根事务打一条日志；
  T3 事务内穿插非 DB 操作时，日志里的 `total` 明显大于 `begin`；
  T4 回滚记为 `ROLLBACK`。

背景见 `docs/tech/03_dal/sqlite_transaction_concurrency.md`。
"""
import asyncio
import contextlib
import logging
import os
import sys
from datetime import datetime
from typing import Iterator, List

import pytest

import service.ormService as ormService
from model.dbModel.base import atomic
from model.dbModel.gtRoomMessage import GtRoomMessage
from tests.base import ServiceTestCase


if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


_TX_LOGGER_NAME = "service.ormService.transaction"


class _LogCollector(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: List[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@contextlib.contextmanager
def _collect_tx_logs() -> Iterator[_LogCollector]:
    """捕获事务日志。

    单测环境没有走 `init_backend_logger`，root logger 仍是默认的 WARNING，
    必须显式把本 logger 的级别降到 INFO，否则正常快事务（INFO）会被过滤掉。
    """
    collector = _LogCollector()
    tx_logger = logging.getLogger(_TX_LOGGER_NAME)
    original_level = tx_logger.level
    tx_logger.addHandler(collector)
    tx_logger.setLevel(logging.DEBUG)
    try:
        yield collector
    finally:
        tx_logger.removeHandler(collector)
        tx_logger.setLevel(original_level)


@contextlib.contextmanager
def _spy_transaction_sql(seen: List[str]) -> Iterator[None]:
    """记录事务控制语句（BEGIN / SAVEPOINT / COMMIT / RELEASE / ROLLBACK）。

    事务控制语句不经过 `db.aio_execute_sql`，而是由 `Transaction.execute`
    直接走连接游标，所以只能在这一层打桩。
    """
    origin = ormService._TxLogTransaction.execute

    async def _spy(self, sql: str) -> None:
        seen.append(sql.strip())
        await origin(self, sql)

    ormService._TxLogTransaction.execute = _spy  # type: ignore[method-assign]
    try:
        yield
    finally:
        ormService._TxLogTransaction.execute = origin  # type: ignore[method-assign]


def _parse_fields(message: str) -> dict:
    return dict(
        item.split("=", 1) for item in message.split() if "=" in item
    )


class TestSqliteTransactionMode(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        db_path = cls._get_test_db_path()
        await ormService.startup(db_path)

    @classmethod
    async def async_teardown_class(cls):
        await ormService.shutdown()

    @staticmethod
    async def _write_message(content: str) -> None:
        await GtRoomMessage.aio_create(
            room_id=1,
            sender_id=1,
            content=content,
            send_time=datetime.now(),
        )

    async def test_root_transaction_uses_begin_immediate(self):
        """根事务必须是 BEGIN IMMEDIATE；否则「先读后写」会立即 database is locked。"""
        seen: List[str] = []
        with _spy_transaction_sql(seen):
            async with atomic():
                await self._write_message("tx-mode-probe")

        assert "BEGIN IMMEDIATE" in seen, f"根事务未使用 BEGIN IMMEDIATE，实际 SQL: {seen}"
        assert "BEGIN" not in seen, f"不应出现裸 BEGIN，实际 SQL: {seen}"

    async def test_nested_transaction_uses_savepoint(self):
        """嵌套事务走 SAVEPOINT，且只有根事务打一条耗时日志。"""
        seen: List[str] = []
        with _collect_tx_logs() as collector, _spy_transaction_sql(seen):
            async with atomic():
                await self._write_message("outer")
                async with atomic():
                    await self._write_message("inner")

        assert any(sql.startswith("SAVEPOINT") for sql in seen), f"嵌套事务未走 SAVEPOINT: {seen}"
        assert "BEGIN IMMEDIATE" in seen, f"根事务未使用 BEGIN IMMEDIATE，实际 SQL: {seen}"
        assert len(collector.messages) == 1, f"应只打一条根事务日志，实际: {collector.messages}"

    async def test_idle_time_shows_interleaved_work(self):
        """事务内穿插非 DB 操作时，total 明显大于 begin（用于定位事务里塞了耗时逻辑）。"""
        with _collect_tx_logs() as collector:
            async with atomic():
                await self._write_message("slow")
                await asyncio.sleep(0.25)

        assert len(collector.messages) == 1, f"应只打一条根事务日志，实际: {collector.messages}"
        fields = _parse_fields(collector.messages[0])
        total_ms = float(fields["total"].removesuffix("ms"))
        begin_ms = float(fields["begin"].removesuffix("ms"))
        assert total_ms >= 200, f"total 应包含穿插的 sleep，实际 {total_ms}ms"
        assert begin_ms < 150, f"begin 只记等锁时长，不应包含 sleep，实际 {begin_ms}ms"

    async def test_rollback_is_logged(self):
        """回滚也要留痕。"""
        with _collect_tx_logs() as collector:
            with pytest.raises(RuntimeError, match="boom"):
                async with atomic():
                    await self._write_message("rollback-probe")
                    raise RuntimeError("boom")

        assert len(collector.messages) == 1, f"应只打一条根事务日志，实际: {collector.messages}"
        assert "ROLLBACK" in collector.messages[0], f"回滚日志应标 ROLLBACK: {collector.messages[0]}"

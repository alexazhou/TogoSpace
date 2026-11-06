"""记忆变更广播（need_fix_1.md P1-3 a 段）。

**为什么单独一个文件**：`test_memory_service.py` 里的用例全部聚焦「校验 / 事务 /
配额」这些 SQL 语义，不应该被事件总线的订阅表污染（那是进程级全局状态）。
这里用真实的 SQLite 跑通 create / update，但把 `messageBus.publish` 换成收集器，
只钉两件事：

1. **提交之后才 publish**——事务内 publish 会让前端先于落库去查，查到旧数据后
   不会再有第二次推送，界面永久停在旧值（比不推更糟）。
2. **payload 字段齐全**——前端按 `agent_id` 过滤、按 `memory_id` 定位。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from dal.db import gtAgentMemoryManager
from exception import TogoException
from model.dbModel.base import bind_database, unbind_database
from service import memoryService, messageBus
from service.ormService import AioSqliteDatabase

_MIGRATION_SQL = (
    Path(__file__).resolve().parents[3] / "assets" / "migrate" / "0020_add_agent_memories.sql"
)

_TEAM_ID = 2
_AGENT_ID = 7


@pytest.fixture
async def memory_db(tmp_path):
    assert _MIGRATION_SQL.exists(), f"迁移文件不存在：{_MIGRATION_SQL}"

    db_path = tmp_path / "agent_memories.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(_MIGRATION_SQL.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()

    db = AioSqliteDatabase(str(db_path), pool_params={"timeout": 30})
    bind_database(db)
    await db.aio_connect()
    try:
        yield db
    finally:
        await db.aio_close()
        unbind_database()


@pytest.fixture
def published(monkeypatch):
    """拦下 `messageBus.publish`，记录 (topic, payload)。"""
    calls: list[tuple] = []
    monkeypatch.setattr(
        messageBus,
        "publish",
        lambda topic, **payload: calls.append((topic, payload)),
    )
    return calls


async def test_create_publishes_after_commit(memory_db, published):
    """新建：广播一次，字段齐全，且此时记忆**已经落库**。"""
    detail = await memoryService.create_memory(
        _TEAM_ID, _AGENT_ID, title="变更广播", summary="概要", content="正文",
    )

    assert len(published) == 1
    topic, payload = published[0]
    assert topic.name == "AGENT_MEMORY_CHANGED"
    assert payload == {
        "team_id": _TEAM_ID,
        "agent_id": _AGENT_ID,
        "memory_id": detail.id,
        "action": "created",
    }

    # 关键断言：publish 的时候数据已经可读（事件消费方立刻重拉不会拿到旧值/空）。
    assert await gtAgentMemoryManager.get_memory(_TEAM_ID, _AGENT_ID, detail.id) is not None


async def test_update_publishes_after_commit(memory_db, published):
    """更新：广播 action=updated，同样在提交之后。"""
    detail = await memoryService.create_memory(
        _TEAM_ID, _AGENT_ID, title="待更新", summary="旧概要", content="正文",
    )
    published.clear()

    await memoryService.update_memory(
        _TEAM_ID, _AGENT_ID, detail.id, summary="新概要",
    )

    assert len(published) == 1
    topic, payload = published[0]
    assert topic.name == "AGENT_MEMORY_CHANGED"
    assert payload["action"] == "updated"
    assert payload["memory_id"] == detail.id

    row = await gtAgentMemoryManager.get_memory(_TEAM_ID, _AGENT_ID, detail.id)
    assert row is not None and row.summary == "新概要"


async def test_failed_create_publishes_nothing(memory_db, published):
    """校验失败（重名）不得广播：否则前端会为一个不存在的变更白拉一次。"""
    await memoryService.create_memory(
        _TEAM_ID, _AGENT_ID, title="重名主题", summary="概要", content="正文",
    )
    published.clear()

    with pytest.raises(TogoException):
        await memoryService.create_memory(
            _TEAM_ID, _AGENT_ID, title="重名主题", summary="概要2", content="正文2",
        )

    assert published == []


async def test_archive_update_also_publishes(memory_db, published):
    """归档走 update 路径，同样要广播——否则「已归档」Tab 也只能重开弹窗才刷。"""
    detail = await memoryService.create_memory(
        _TEAM_ID, _AGENT_ID, title="将归档", summary="概要", content="正文",
    )
    published.clear()

    await memoryService.update_memory(_TEAM_ID, _AGENT_ID, detail.id, status="archived")

    assert len(published) == 1
    assert published[0][1]["action"] == "updated"

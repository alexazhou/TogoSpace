"""`agent_memories` DAL 回归测试（Agent 长期记忆系统，任务 #134 段0）。

**用真实迁移 SQL 建表**：本表的索引、默认值（`star_level='NONE'` / `status='ACTIVE'` /
`tags='[]'`）都写在 `assets/migrate/0020_add_agent_memories.sql` 里，模型 `Meta`
不重复声明。用 `create_tables` 建出来的表会缺这些 DDL，测试就变成在验证自己的假设。
所以直接 `executescript` 迁移文件，让每个用例跑在和线上一致的 schema 上。

**用文件库不用 `:memory:`**：本 DAL 全异步走 `peewee_async` 连接池，每次
`acquire()` 都是新连接；`":memory:"` 在 SQLite 里每连接独立，第二个连接看不到表。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from constants import MemoryStarLevel, MemoryStatus
from dal.db import gtAgentMemoryManager
from model.dbModel.base import bind_database, unbind_database
from model.dbModel.gtAgentMemory import GtAgentMemory
from service.ormService import AioSqliteDatabase

# tests/unit/dal/<file> → parents[3] 是仓库根
_MIGRATION_SQL = (
    Path(__file__).resolve().parents[3] / "assets" / "migrate" / "0020_add_agent_memories.sql"
)

_TEAM_ID = 2
_AGENT_ID = 7


@pytest.fixture
async def memory_db(tmp_path):
    """把模型代理临时绑到「已跑过 0020 迁移」的临时 SQLite 文件上，结束后一定解绑。"""
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


async def _create(
    title: str,
    *,
    team_id: int = _TEAM_ID,
    agent_id: int = _AGENT_ID,
    summary: str = "概要",
    content: str = "正文",
    star_level: MemoryStarLevel = MemoryStarLevel.NONE,
    status: MemoryStatus = MemoryStatus.ACTIVE,
    tags: list[str] | None = None,
) -> GtAgentMemory:
    memory = GtAgentMemory(
        team_id=team_id,
        agent_id=agent_id,
        title=title,
        summary=summary,
        content=content,
        star_level=star_level,
        status=status,
        tags=tags or [],
    )
    return await gtAgentMemoryManager.save_memory(memory, force_insert=True)


# ---------------------------------------------------------------------------
# 默认值与落库口径
# ---------------------------------------------------------------------------


async def test_defaults_applied_on_insert(memory_db):
    """不显式传 star_level / status / tags 时，走 DDL 默认值（NONE / ACTIVE / []）。"""
    memory = GtAgentMemory(
        team_id=_TEAM_ID, agent_id=_AGENT_ID,
        title="默认值用例", summary="概要", content="正文",
    )
    saved = await gtAgentMemoryManager.save_memory(memory, force_insert=True)

    assert saved.id is not None
    assert saved.star_level == MemoryStarLevel.NONE
    assert saved.status == MemoryStatus.ACTIVE
    assert saved.tags == []


async def test_enum_persists_name_in_db_but_python_value_is_enum(memory_db, tmp_path):
    """DB 里存枚举 **name**（大写），Python 侧读回来是枚举成员。

    这是 `EnumField.db_value()` 的既定口径（`src/model/dbModel/base.py`），
    前端与工具 JSON 的**对外**小写 value 由序列化层单独处理，不在 DAL 这一层。
    """
    saved = await _create(
        "枚举口径", star_level=MemoryStarLevel.SUMMARY, status=MemoryStatus.ARCHIVED,
    )

    # Python 侧：枚举成员
    fetched = await gtAgentMemoryManager.get_memory(_TEAM_ID, _AGENT_ID, saved.id)
    assert fetched is not None
    assert fetched.star_level is MemoryStarLevel.SUMMARY
    assert fetched.status is MemoryStatus.ARCHIVED

    # DB 侧：name（大写）。必须绕开 ORM 的 `python_value` 反序列化，直接读原始行。
    raw_conn = sqlite3.connect(str(tmp_path / "agent_memories.db"))
    try:
        raw_row = raw_conn.execute(
            "SELECT star_level, status FROM agent_memories WHERE id = ?", (saved.id,)
        ).fetchone()
    finally:
        raw_conn.close()
    assert raw_row is not None
    assert raw_row[0] == "SUMMARY"
    assert raw_row[1] == "ARCHIVED"


async def test_tags_roundtrip_json(memory_db):
    """tags 以 JSON 数组字符串落库，读回来是 list[str]。"""
    saved = await _create("标签用例", tags=["输出风格", "交付规范"])
    fetched = await gtAgentMemoryManager.get_memory(_TEAM_ID, _AGENT_ID, saved.id)
    assert fetched is not None
    assert fetched.tags == ["输出风格", "交付规范"]


# ---------------------------------------------------------------------------
# 按 ID 归属查询
# ---------------------------------------------------------------------------


async def test_get_memory_scoped_by_team_and_agent(memory_db):
    """按 ID 查必须同时校验 team_id / agent_id —— 跨 Agent / 跨团队一律查不到。"""
    saved = await _create("归属校验")

    assert await gtAgentMemoryManager.get_memory(_TEAM_ID, _AGENT_ID, saved.id) is not None
    # 同团队另一个 Agent
    assert await gtAgentMemoryManager.get_memory(_TEAM_ID, 8, saved.id) is None
    # 另一个团队
    assert await gtAgentMemoryManager.get_memory(99, _AGENT_ID, saved.id) is None


async def test_get_memory_returns_archived(memory_db):
    """`memory_read` 支持读已归档：归档记忆按 ID 仍可查到。"""
    saved = await _create("归档可读", status=MemoryStatus.ARCHIVED)
    fetched = await gtAgentMemoryManager.get_memory(_TEAM_ID, _AGENT_ID, saved.id)
    assert fetched is not None
    assert fetched.status is MemoryStatus.ARCHIVED


# ---------------------------------------------------------------------------
# 同名防重
# ---------------------------------------------------------------------------


async def test_get_active_by_title_only_active(memory_db):
    """同名查询只看 ACTIVE：已归档同名不参与防重。"""
    await _create("同名主题", status=MemoryStatus.ARCHIVED)
    assert await gtAgentMemoryManager.get_active_by_title(_TEAM_ID, _AGENT_ID, "同名主题") is None

    active = await _create("同名主题", status=MemoryStatus.ACTIVE)
    found = await gtAgentMemoryManager.get_active_by_title(_TEAM_ID, _AGENT_ID, "同名主题")
    assert found is not None
    assert found.id == active.id


async def test_get_active_by_title_excludes_self(memory_db):
    """更新场景排除自身：否则「标题不变只改概要」会被自己判成重名。"""
    saved = await _create("自身排除")

    found = await gtAgentMemoryManager.get_active_by_title(
        _TEAM_ID, _AGENT_ID, "自身排除", exclude_id=saved.id,
    )
    assert found is None


async def test_get_active_by_title_scoped_by_agent(memory_db):
    """同名判定按 Agent 隔离：别的 Agent 有同名记忆不影响本 Agent。"""
    await _create("跨 Agent 同名", agent_id=8)
    assert await gtAgentMemoryManager.get_active_by_title(_TEAM_ID, _AGENT_ID, "跨 Agent 同名") is None


# ---------------------------------------------------------------------------
# 列表与排序
# ---------------------------------------------------------------------------


async def test_list_memories_star_first(memory_db):
    """列表排序：SUMMARY > TITLE > NONE（与 updated_at 无关，优先级压过时间）。"""
    none_mem = await _create("普通记忆", star_level=MemoryStarLevel.NONE)
    title_mem = await _create("标题星标", star_level=MemoryStarLevel.TITLE)
    summary_mem = await _create("概要星标", star_level=MemoryStarLevel.SUMMARY)

    listed = await gtAgentMemoryManager.list_memories(_TEAM_ID, _AGENT_ID)
    assert [m.id for m in listed] == [summary_mem.id, title_mem.id, none_mem.id]


async def test_list_memories_same_level_updated_desc(memory_db):
    """同星标级别内按 `updated_at` 倒序（时间戳显式指定，不依赖插入顺序）。"""
    import datetime

    older = await _create("较早")
    newer = await _create("较新")
    older.updated_at = datetime.datetime(2026, 11, 1, 10, 0, 0)
    newer.updated_at = datetime.datetime(2026, 11, 2, 10, 0, 0)
    await gtAgentMemoryManager.save_memory(older, only=[GtAgentMemory.updated_at])
    await gtAgentMemoryManager.save_memory(newer, only=[GtAgentMemory.updated_at])

    listed = await gtAgentMemoryManager.list_memories(_TEAM_ID, _AGENT_ID)
    assert [m.id for m in listed] == [newer.id, older.id]


async def test_list_memories_excludes_archived_by_default(memory_db):
    """默认只返回 ACTIVE；`include_archived=True` 时一并返回。"""
    active = await _create("活跃记忆")
    archived = await _create("归档记忆", status=MemoryStatus.ARCHIVED)

    default_ids = [m.id for m in await gtAgentMemoryManager.list_memories(_TEAM_ID, _AGENT_ID)]
    assert default_ids == [active.id]

    with_archived = [
        m.id
        for m in await gtAgentMemoryManager.list_memories(
            _TEAM_ID, _AGENT_ID, include_archived=True,
        )
    ]
    assert set(with_archived) == {active.id, archived.id}


async def test_list_memories_scoped_by_agent_and_team(memory_db):
    """列表严格按 Agent 隔离，不串团队也不串 Agent。"""
    mine = await _create("我的记忆")
    await _create("别人的记忆", agent_id=8)
    await _create("别队的记忆", team_id=99)

    listed = await gtAgentMemoryManager.list_memories(_TEAM_ID, _AGENT_ID)
    assert [m.id for m in listed] == [mine.id]


async def test_list_starred_memories_only_active_non_none(memory_db):
    """星标快照只取 ACTIVE 且 star_level != NONE。"""
    await _create("普通记忆", star_level=MemoryStarLevel.NONE)
    await _create("归档星标", star_level=MemoryStarLevel.SUMMARY, status=MemoryStatus.ARCHIVED)
    title_mem = await _create("标题星标", star_level=MemoryStarLevel.TITLE)
    summary_mem = await _create("概要星标", star_level=MemoryStarLevel.SUMMARY)

    starred = await gtAgentMemoryManager.list_starred_memories(_TEAM_ID, _AGENT_ID)
    assert [m.id for m in starred] == [summary_mem.id, title_mem.id]


async def test_list_active_for_search_only_active(memory_db):
    """检索候选集只含 ACTIVE。"""
    active = await _create("活跃")
    await _create("归档", status=MemoryStatus.ARCHIVED)

    candidates = await gtAgentMemoryManager.list_active_for_search(_TEAM_ID, _AGENT_ID)
    assert [m.id for m in candidates] == [active.id]


# ---------------------------------------------------------------------------
# 配额统计
# ---------------------------------------------------------------------------


async def test_count_active_starred_total_and_summary(memory_db):
    """总星标数含 TITLE + SUMMARY；`star_level=SUMMARY` 时只数概要级；归档不计入。"""
    await _create("星标1", star_level=MemoryStarLevel.SUMMARY)
    await _create("星标2", star_level=MemoryStarLevel.SUMMARY)
    await _create("星标3", star_level=MemoryStarLevel.TITLE)
    await _create("普通", star_level=MemoryStarLevel.NONE)
    await _create("归档星标", star_level=MemoryStarLevel.SUMMARY, status=MemoryStatus.ARCHIVED)

    assert await gtAgentMemoryManager.count_active_starred(_TEAM_ID, _AGENT_ID) == 3
    assert await gtAgentMemoryManager.count_active_starred(
        _TEAM_ID, _AGENT_ID, star_level=MemoryStarLevel.SUMMARY,
    ) == 2


async def test_count_active_starred_excludes_self(memory_db):
    """统计「除本条外」：升级 / 同级别重复保存时不能把自己算进去。"""
    first = await _create("星标1", star_level=MemoryStarLevel.TITLE)

    assert await gtAgentMemoryManager.count_active_starred(
        _TEAM_ID, _AGENT_ID, exclude_id=first.id,
    ) == 0
    assert await gtAgentMemoryManager.count_active_starred(
        _TEAM_ID, _AGENT_ID, star_level=MemoryStarLevel.TITLE, exclude_id=first.id,
    ) == 0


async def test_count_active_starred_scoped_by_agent(memory_db):
    """配额统计不串 Agent：别人的星标不占我的额度。"""
    await _create("别人的星标", agent_id=8, star_level=MemoryStarLevel.SUMMARY)
    assert await gtAgentMemoryManager.count_active_starred(_TEAM_ID, _AGENT_ID) == 0


# ---------------------------------------------------------------------------
# 清理
# ---------------------------------------------------------------------------


async def test_delete_memories_by_agent(memory_db):
    """按 Agent 清理：只删该 Agent 的，别人的原样保留。"""
    mine = await _create("我的1")
    await _create("我的2", status=MemoryStatus.ARCHIVED)
    other = await _create("别人的", agent_id=8)

    deleted = await gtAgentMemoryManager.delete_memories_by_agent(_AGENT_ID)
    assert deleted == 2
    remaining = await gtAgentMemoryManager.list_memories(_TEAM_ID, 8, include_archived=True)
    assert [m.id for m in remaining] == [other.id]
    assert await gtAgentMemoryManager.get_memory(_TEAM_ID, _AGENT_ID, mine.id) is None


async def test_delete_memories_by_team(memory_db):
    """按团队清理：删该团队全部记忆，不波及其他团队。"""
    await _create("本队1")
    await _create("本队2", agent_id=8)
    other_team = await _create("别队", team_id=99)

    deleted = await gtAgentMemoryManager.delete_memories_by_team(_TEAM_ID)
    assert deleted == 2
    assert await gtAgentMemoryManager.list_memories(99, _AGENT_ID, include_archived=True) == [
        other_team
    ]

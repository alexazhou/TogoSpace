"""`agent_containers` DAL 回归测试（Docker 容器化 Agent 沙箱，任务 #109 第 2 步）。

**为什么用真实迁移 SQL 建表，而不是 `db.create_tables(...)`**：
本表的关键约束有一半写在 `assets/migrate/0017_agent_containers.sql` 里、模型 `Meta`
里并没有（`docker_id` 全局唯一、`container_name` 的**部分**唯一索引
`WHERE deleted = 0`）。用 `create_tables` 建出来的表会缺这些索引，测试就变成
「在测试自己的假设上验证」，而不是验证生产 DDL。所以这里直接 `executescript`
执行迁移文件，让下面每个用例都跑在**和线上一致的 schema** 上。

**为什么用文件库而不是 `:memory:`**：本 DAL 全是 `async` 接口，走
`peewee_async` 的连接池（`SqlitePoolBackend`），每次 `acquire()` 都是新开的
`aiosqlite` 连接。`":memory:"` 在 SQLite 里是**每连接独立**的库，第二个连接看不
到第一个连接建的表，测试会以「表不存在」这种看不懂的方式挂掉。用 `tmp_path` 下的
临时文件库，iper 连接共享同一份数据，接近生产。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import peewee
import pytest

from dal.db import gtAgentContainerManager
from model.dbModel.base import bind_database, unbind_database
from model.dbModel.gtAgentContainer import GtAgentContainer
from service.ormService import AioSqliteDatabase

# tests/unit/dal/<file> → parents[3] 是仓库根
_MIGRATION_SQL = (
    Path(__file__).resolve().parents[3] / "assets" / "migrate" / "0017_agent_containers.sql"
)


@pytest.fixture
async def container_db(tmp_path):
    """把模型代理临时绑到「已跑过 0017 迁移」的临时 SQLite 文件上，结束后一定解绑。"""
    assert _MIGRATION_SQL.exists(), f"迁移文件不存在：{_MIGRATION_SQL}"

    db_path = tmp_path / "agent_containers.db"
    # 用同步 sqlite3 建 schema：executescript 能吃多语句，且不干扰下面异步池的连接。
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


async def _create(agent_id: int, name: str, image: str = "ubuntu:24.04") -> GtAgentContainer:
    """插一条预记录（`docker_id = NULL`），返回带主键的对象。"""
    return await gtAgentContainerManager.create(
        agent_id=agent_id, container_name=name, image=image
    )


async def _create_with_docker_id(
    agent_id: int, name: str, docker_id: str, image: str = "ubuntu:24.04"
) -> GtAgentContainer:
    """插一条已回填 `docker_id` 的记录。"""
    container = await _create(agent_id, name, image)
    return await gtAgentContainerManager.set_docker_id(container, docker_id)


def _names(rows: list[GtAgentContainer]) -> list[str]:
    return [row.container_name for row in rows]


def _ids(rows: list[GtAgentContainer]) -> list[int]:
    return [row.id for row in rows]


async def _fetch_all(cursor):
    """`AioDatabase.aio_execute` 的取结果回调（裸 SQL 默认不返回任何行）。"""
    return await cursor.fetchall()


async def _raw_rows(db, sql: str, params: tuple = ()) -> list[tuple]:
    """跑一条裸 SQL 并取回结果。

    注意两点（都踩过）：
    1. `peewee.SQL` 的第二个参数必须是**可迭代**的参数序列，传裸 `int` 会在
       `__sql__` 里 `for param in self.params` 抛 `TypeError`；
    2. 不传 `fetch_results` 时 `aio_execute` 只执行不取结果（返回 `None`）。
    """
    return await db.aio_execute(peewee.SQL(sql, params), fetch_results=_fetch_all)


# ---------------------------------------------------------------------------
# 迁移 DDL 本身：部分唯一索引是「软删除后名称可复用」的唯一保证
# ---------------------------------------------------------------------------


async def test_migration_creates_table_and_all_three_indexes(container_db):
    """0017 必须建出表 + 3 个索引（含 2 个唯一索引），防迁移被改坏后无人察觉。"""
    rows = await _raw_rows(
        container_db, "SELECT name, [unique], partial FROM pragma_index_list('agent_containers')"
    )
    index_map = {row[0]: (bool(row[1]), bool(row[2])) for row in rows}

    assert set(index_map) == {
        "idx_agent_containers_agent",
        "idx_agent_containers_docker_id",
        "idx_agent_containers_name_active",
    }
    assert index_map["idx_agent_containers_docker_id"] == (True, False)
    # 名称唯一索引必须是**部分**索引，否则软删除后名称无法复用
    assert index_map["idx_agent_containers_name_active"] == (True, True)


async def test_docker_id_is_not_null_uniqueness_for_pre_records(container_db):
    """`docker_id` 唯一索引**不约束 NULL**：同一 Agent 可以有多条创建中的预记录。

    这是「先落预记录占配额、create 成功再回填」能成立的前提——若 NULL 被当作
    重复值，第二次创建必然失败。
    """
    await _create(1, "agent-a-1")
    await _create(1, "agent-a-2")

    rows = await gtAgentContainerManager.list_active_by_agent(1)
    assert _names(rows) == ["agent-a-1", "agent-a-2"]
    assert [row.docker_id for row in rows] == [None, None]


async def test_duplicate_docker_id_across_agents_raises(container_db):
    """`docker_id` 全局唯一：即使分属不同 Agent 也不许重复（索引冲突要能冒到调用方补偿）。"""
    await _create_with_docker_id(1, "agent-a-1", "deadbeef0001")

    with pytest.raises(peewee.IntegrityError):
        await _create_with_docker_id(2, "agent-b-1", "deadbeef0001")


async def test_active_container_name_must_be_unique(container_db):
    """活跃记录内 `container_name` 唯一（部分唯一索引）：并发创建同名容器必须被库里拦住。"""
    await _create(1, "agent-a-1")

    with pytest.raises(peewee.IntegrityError):
        await _create(2, "agent-a-1")


async def test_container_name_is_reusable_after_soft_delete(container_db):
    """软删除后名称可复用：`deleted = 1` 的行不参与部分唯一索引。

    若这条挂了，说明索引退化成了普通唯一索引（`WHERE deleted = 0` 被去掉），
    删容器后重建同名容器会永久失败。
    """
    first = await _create_with_docker_id(1, "agent-a-1", "deadbeef0001")
    assert await gtAgentContainerManager.mark_deleted(first) is True

    second = await _create(1, "agent-a-1")

    assert second.id != first.id
    assert second.docker_id is None
    assert _ids(await gtAgentContainerManager.list_active_by_agent(1)) == [second.id]


# ---------------------------------------------------------------------------
# create / set_docker_id：预记录 → 回填两段式
# ---------------------------------------------------------------------------


async def test_create_inserts_pre_record_with_null_docker_id(container_db):
    """预记录必须 `docker_id = NULL`、`deleted = 0`，并立刻带出主键与时间戳。"""
    container = await _create(7, "agent-x-1", image="ubuntu:22.04")

    assert container.id is not None and container.id > 0
    assert container.agent_id == 7
    assert container.docker_id is None
    assert container.container_name == "agent-x-1"
    assert container.image == "ubuntu:22.04"
    assert container.deleted == 0
    # `created_at/updated_at` 由 `AutoTimestampMixin` 注入，不是 int（见设计文档冲突说明）
    from datetime import datetime

    assert isinstance(container.created_at, datetime)
    assert isinstance(container.updated_at, datetime)


async def test_set_docker_id_backfills_short_id_and_persists(container_db):
    """回填 12 位短 ID 后必须落库（重新查出来的值一致，不是只改了内存对象）。"""
    container = await _create(7, "agent-x-1")
    await gtAgentContainerManager.set_docker_id(container, "c0ffee123456")

    reloaded = await gtAgentContainerManager.get_active_by_id(container.id)
    assert reloaded is not None
    assert reloaded.docker_id == "c0ffee123456"
    assert reloaded.updated_at >= reloaded.created_at


async def test_set_docker_id_conflict_leaves_record_unbackfilled(container_db):
    """回填撞唯一索引时抛错、且原记录保持 `docker_id = NULL`（供调用方硬删除补偿）。"""
    taken = await _create_with_docker_id(1, "agent-a-1", "deadbeef0001")
    pending = await _create(2, "agent-b-1")

    with pytest.raises(peewee.IntegrityError):
        await gtAgentContainerManager.set_docker_id(pending, "deadbeef0001")

    reloaded = await gtAgentContainerManager.get_active_by_id(pending.id)
    assert reloaded is not None and reloaded.docker_id is None
    assert taken.docker_id == "deadbeef0001"


# ---------------------------------------------------------------------------
# 删除语义：硬删除（创建失败补偿） vs 软删除（正常删除）
# ---------------------------------------------------------------------------


async def test_hard_delete_removes_row_entirely(container_db):
    """硬删除用于创建失败补偿：行必须真没了，不能留下占配额的残留。"""
    container = await _create(7, "agent-x-1")

    await gtAgentContainerManager.hard_delete(container)

    assert await gtAgentContainerManager.get_active_by_id(container.id) is None
    assert await gtAgentContainerManager.count_active_by_agent(7) == 0
    # 连软删除视角也查不到：确实是物理删除
    raw = await _raw_rows(
        container_db, "SELECT COUNT(*) FROM agent_containers WHERE id = ?", (container.id,)
    )
    assert raw[0][0] == 0


async def test_hard_delete_without_primary_key_is_noop(container_db):
    """未落库的对象（`id is None`）调硬删除不能抛错（补偿路径可能拿到半成品对象）。"""
    await gtAgentContainerManager.hard_delete(GtAgentContainer(agent_id=1, container_name="n", image="i"))

    assert await gtAgentContainerManager.list_active_by_agent(1) == []


async def test_mark_deleted_keeps_row_and_frees_quota(container_db):
    """软删除保留行（留痕）但立刻让出配额，且第二次调用返回 False（幂等）。"""
    container = await _create_with_docker_id(7, "agent-x-1", "c0ffee123456")
    assert await gtAgentContainerManager.count_active_by_agent(7) == 1

    assert await gtAgentContainerManager.mark_deleted(container) is True
    assert container.deleted == 1
    assert await gtAgentContainerManager.count_active_by_agent(7) == 0
    assert await gtAgentContainerManager.get_active_by_id(container.id) is None

    # 行还在库里，只是 deleted = 1
    raw = await _raw_rows(
        container_db, "SELECT deleted FROM agent_containers WHERE id = ?", (container.id,)
    )
    assert raw[0][0] == 1
    # 幂等：重复软删除不报错，也不假装删了一次
    assert await gtAgentContainerManager.mark_deleted(container) is False


async def test_soft_deleted_docker_id_can_be_reused_by_new_container(container_db):
    """软删除后 `docker_id` 同样可复用（唯一索引不区分 deleted，但行已让位）。

    注意 `docker_id` 的唯一索引**没有** `WHERE deleted = 0`：软删除的行仍占着
    这个 ID。这里断言的是「删掉容器后，同一个 Docker 容器被重新纳管」这条真实
    路径——它需要先把旧行清掉，因此本用例只验证**活跃记录里查不到旧映射**。
    """
    old = await _create_with_docker_id(1, "agent-a-1", "deadbeef0001")
    await gtAgentContainerManager.mark_deleted(old)

    assert await gtAgentContainerManager.get_active_by_docker_id(1, "deadbeef0001") is None
    # 旧行仍占着 docker_id，所以换个 ID 才是重纳管的正常姿势
    new = await _create_with_docker_id(1, "agent-a-1", "deadbeef0002")
    assert (await gtAgentContainerManager.get_active_by_docker_id(1, "deadbeef0002")).id == new.id


# ---------------------------------------------------------------------------
# 查询：活跃过滤、排序、Agent 隔离、批量统计
# ---------------------------------------------------------------------------


async def test_list_active_by_agent_excludes_deleted_and_orders_by_id(container_db):
    """只返 `deleted = 0`，按 id 升序（列表接口的展示顺序，也是「最早创建在前」）。"""
    first = await _create(1, "agent-a-1")
    second = await _create(1, "agent-a-2")
    third = await _create(1, "agent-a-3")
    await gtAgentContainerManager.mark_deleted(second)

    rows = await gtAgentContainerManager.list_active_by_agent(1)

    assert _ids(rows) == [first.id, third.id]
    assert _names(rows) == ["agent-a-1", "agent-a-3"]


async def test_list_active_by_agent_is_scoped_to_the_agent(container_db):
    """跨 Agent 不串数据（配额与列表都按 agent 隔离）。"""
    await _create(1, "agent-a-1")
    await _create(2, "agent-b-1")

    assert _names(await gtAgentContainerManager.list_active_by_agent(1)) == ["agent-a-1"]
    assert _names(await gtAgentContainerManager.list_active_by_agent(2)) == ["agent-b-1"]
    assert await gtAgentContainerManager.list_active_by_agent(3) == []


async def test_count_active_by_agent_counts_pre_records(container_db):
    """配额口径含预记录、也不排除已停止（运行状态不落库，停止的容器照样占位）。"""
    await _create(1, "agent-a-1")  # 预记录（docker_id = NULL）
    await _create_with_docker_id(1, "agent-a-2", "deadbeef0002")
    await _create(1, "agent-a-3")
    await _create(2, "agent-b-1")

    assert await gtAgentContainerManager.count_active_by_agent(1) == 3
    assert await gtAgentContainerManager.count_active_by_agent(2) == 1
    assert await gtAgentContainerManager.count_active_by_agent(99) == 0


async def test_count_active_by_agent_uses_aio_count_not_sync_count(container_db):
    """回归：`count()` 是同步立即执行，在 `AioDatabase` 下必抛 `AssertionError`。

    这条用例的价值在于它跑在 `AioDatabase`（`_allow_sync = False`）上——若实现
    退回 `.count()`，这里直接红，而不是等到线上报「sync query is not allowed」。
    """
    await _create(1, "agent-a-1")

    assert container_db._allow_sync is False
    assert await gtAgentContainerManager.count_active_by_agent(1) == 1


async def test_count_map_by_agent_ids_batches_and_skips_empty(container_db):
    """批量统计：返回 `{agent_id: count}`，缺省 Agent 不出现在 dict 里（调用方自己补 0）。"""
    await _create(1, "agent-a-1")
    await _create(1, "agent-a-2")
    await _create(3, "agent-c-1")
    deleted = await _create(3, "agent-c-2")
    await gtAgentContainerManager.mark_deleted(deleted)

    assert await gtAgentContainerManager.count_map_by_agent_ids([1, 2, 3]) == {1: 2, 3: 1}
    # 空入参不查库（返回空 dict，而不是全表统计）
    assert await gtAgentContainerManager.count_map_by_agent_ids([]) == {}


async def test_list_active_by_agents_filters_deleted_and_empty_input(container_db):
    """批量查询同样只返活跃记录；空入参返回空列表（`IN ()` 是坏 SQL，不能生成）。"""
    await _create(1, "agent-a-1")
    removed = await _create(1, "agent-a-2")
    await gtAgentContainerManager.mark_deleted(removed)
    await _create(2, "agent-b-1")

    assert sorted(_names(await gtAgentContainerManager.list_active_by_agents([1, 2]))) == [
        "agent-a-1",
        "agent-b-1",
    ]
    assert await gtAgentContainerManager.list_active_by_agents([]) == []


async def test_list_active_by_team_agents_matches_batch_query(container_db):
    """Team 删除/改名走的是同一套批量查询（口径必须一致，防两处各写一份过滤）。"""
    await _create(1, "agent-a-1")
    await _create(2, "agent-b-1")
    removed = await _create(2, "agent-b-2")
    await gtAgentContainerManager.mark_deleted(removed)

    team_rows = await gtAgentContainerManager.list_active_by_team_agents([1, 2])
    batch_rows = await gtAgentContainerManager.list_active_by_agents([1, 2])

    assert sorted(_names(team_rows)) == sorted(_names(batch_rows)) == ["agent-a-1", "agent-b-1"]


async def test_get_active_by_docker_id_requires_matching_agent(container_db):
    """`docker_id` 全局唯一，但查询必须带 `agent_id`：不是自己的容器一律查不到。

    这条是「绝不删别人容器」的 DAL 侧护栏——即使拿到了别人的 docker_id，
    `get_active_by_docker_id` 也不会返回记录，调用方就没有删除依据。
    """
    container = await _create_with_docker_id(1, "agent-a-1", "deadbeef0001")

    assert (await gtAgentContainerManager.get_active_by_docker_id(1, "deadbeef0001")).id == container.id
    assert await gtAgentContainerManager.get_active_by_docker_id(2, "deadbeef0001") is None
    assert await gtAgentContainerManager.get_active_by_docker_id(1, "ffffffffffff") is None


async def test_get_active_by_docker_id_ignores_deleted_and_null(container_db):
    """软删除的记录查不到；预记录的 `NULL` 也不能被任何字符串命中。"""
    removed = await _create_with_docker_id(1, "agent-a-1", "deadbeef0001")
    await gtAgentContainerManager.mark_deleted(removed)
    await _create(1, "agent-a-2")  # docker_id = NULL

    assert await gtAgentContainerManager.get_active_by_docker_id(1, "deadbeef0001") is None
    assert await gtAgentContainerManager.get_active_by_docker_id(1, "") is None


async def test_get_active_by_name_finds_pre_record(container_db):
    """按名查用于「预记录按名清理」：必须能查到 `docker_id = NULL` 的那条。"""
    container = await _create(1, "agent-a-1")

    found = await gtAgentContainerManager.get_active_by_name(1, "agent-a-1")
    assert found is not None and found.id == container.id and found.docker_id is None
    assert await gtAgentContainerManager.get_active_by_name(2, "agent-a-1") is None
    assert await gtAgentContainerManager.get_active_by_name(1, "agent-a-9") is None


async def test_get_active_by_name_ignores_deleted(container_db):
    """软删除后按名查不到（否则会拿已删记录去删同名的新容器）。"""
    old = await _create(1, "agent-a-1")
    await gtAgentContainerManager.mark_deleted(old)
    new = await _create(1, "agent-a-1")

    found = await gtAgentContainerManager.get_active_by_name(1, "agent-a-1")
    assert found is not None and found.id == new.id


async def test_get_active_by_id_ignores_deleted(container_db):
    """按主键查也只返活跃记录（`record_id` 是前端行 key，不能指向已删行）。"""
    container = await _create(1, "agent-a-1")

    assert (await gtAgentContainerManager.get_active_by_id(container.id)).id == container.id
    await gtAgentContainerManager.mark_deleted(container)
    assert await gtAgentContainerManager.get_active_by_id(container.id) is None
    assert await gtAgentContainerManager.get_active_by_id(999999) is None


# ---------------------------------------------------------------------------
# 配额上限（`MAX_CONTAINERS_PER_AGENT = 3`）在 DAL 层只是「数得准」，拦截在 service
# ---------------------------------------------------------------------------


async def test_quota_count_reaches_limit_after_three_active_records(container_db):
    """DAL 不做拦截，但要保证数出来的值能让 service 正确挡住第 4 个。"""
    for index in range(1, 4):
        await _create(1, f"agent-a-{index}")

    assert await gtAgentContainerManager.count_active_by_agent(1) == 3
    assert len(await gtAgentContainerManager.list_active_by_agent(1)) == 3

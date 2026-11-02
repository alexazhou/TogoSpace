"""0018 迁移（schecule_tasks → schedule_jobs）的生命周期测试。

本迁移只做三件事：表改名、索引改名、列 task_type → job_type。
枚举值名不变，因此**没有任何存量数据 UPDATE** —— 这组用例的核心就是钉住这一点，
外加钉住「中途失败必须整体回滚」。

为什么「整体回滚」是硬要求（见 `assets/migrate/0018_*.sql` 与 `db.py:apply_migration`）：
`apply_migration` 用 `conn.executescript(sql)` 执行 SQL，`executescript` 进入时会
先**隐式 COMMIT**，`with conn:` 的异常回滚兜不住 DDL。若 SQL 不带显式事务且中途失败，
库会停在「schedule_jobs 已存在 + job_type 不存在」的脏状态；重跑时第一条
`ALTER TABLE schecule_tasks` 直接 `no such table`，而
`db._is_ignorable_migration_error` 只放行 `duplicate column name` / `already exists`，
不放行 `no such table` ⇒ 迁移永不记账、**应用每次启动都失败**。

覆盖场景：
1. 正常迁移：表/索引/列名都改到新名字，旧名一个不剩；
2. 数据完整：条数与 job_type 分布迁移前后逐项一致（枚举值名不变）；
3. 中途失败回滚：人为让最后一句报错，断言库**完全**回到迁移前（表名、列名、索引、数据都不变）；
4. 幂等/重跑：迁移成功后再跑一次，不会因为旧表不存在而失败（已记账 → 不重跑）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import db

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "assets/migrate"
MIGRATION_0018 = "0018_rename_schecule_tasks_to_schedule_jobs.sql"

# 迁移前的存量分布，覆盖两个 job_type × 多个 status（枚举值名迁移前后必须一字不差）
_SEED_ROWS = [
    (1, "ROOM_MESSAGE", '{"room_id": 11}', "COMPLETED"),
    (1, "ROOM_MESSAGE", '{"room_id": 12}', "RUNNING"),
    (2, "ROOM_MESSAGE", '{"room_id": 21}', "CANCELLED"),
    (2, "TODO_TASK", '{"agent_task_id": 31}', "COMPLETED"),
    (3, "TODO_TASK", '{"agent_task_id": 32}', "PENDING"),
]


def _migrate_to_0017(db_path: Path) -> None:
    """把数据库迁移到 0017 为止（up_to 是前缀开区间，18 表示 < 0018）。"""
    db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR, up_to="18")


def _seed(db_path: Path) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.executemany(
            "INSERT INTO schecule_tasks (agent_id, task_type, task_data, status)"
            " VALUES (?, ?, ?, ?)",
            _SEED_ROWS,
        )


def _snapshot(db_path: Path) -> dict:
    """取一份「库结构 + 数据」快照，用于断言迁移失败后库完全未变。"""
    with sqlite3.connect(str(db_path)) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
                " AND (name LIKE '%task%' OR name LIKE '%job%')"
            )
        }
        columns = [row[1] for row in conn.execute("PRAGMA table_info(schecule_tasks)")]
        indexes = sorted(
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='schecule_tasks'"
                " AND name NOT LIKE 'sqlite_%'"
            )
        )
        rows = sorted(
            conn.execute(
                "SELECT agent_id, task_type, task_data, status FROM schecule_tasks"
            ).fetchall()
        )
        applied = sorted(
            row[0] for row in conn.execute(f"SELECT name FROM {db.MIGRATIONS_TABLE}")
        )
    return {
        "tables": tables,
        "columns": columns,
        "indexes": indexes,
        "rows": rows,
        "applied": applied,
    }


def test_0018_renames_table_index_and_column(tmp_path: Path) -> None:
    """正常迁移：表名、索引名、列名全部改到新名字，旧名一个不剩。"""
    db_path = tmp_path / "test.db"
    _migrate_to_0017(db_path)
    _seed(db_path)

    applied = db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR, up_to="19")

    assert applied == [MIGRATION_0018]

    with sqlite3.connect(str(db_path)) as conn:
        table_names = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        index_names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
            )
        }
        column_names = [
            row[1] for row in conn.execute("PRAGMA table_info(schedule_jobs)")
        ]

    assert "schedule_jobs" in table_names, "新表 schedule_jobs 应存在"
    assert "schecule_tasks" not in table_names, "旧表 schecule_tasks 应已不存在（不做降级兼容视图）"
    assert "idx_schedule_jobs_agent_status" in index_names, "索引应改到新名字"
    assert "idx_schecule_tasks_agent_status" not in index_names, "旧索引名应已不存在"
    assert "job_type" in column_names, "列应改名为 job_type"
    assert "task_type" not in column_names, "旧列 task_type 应已不存在"
    # 保留原名的字段必须原样还在（§3.7 task_data 不改）
    assert "task_data" in column_names, "task_data 按决策保留原名"


def test_0018_preserves_row_count_and_type_distribution(tmp_path: Path) -> None:
    """存量数据条数与 job_type/status 分布迁移前后逐项一致（不做任何 UPDATE）。"""
    db_path = tmp_path / "test.db"
    _migrate_to_0017(db_path)
    _seed(db_path)

    with sqlite3.connect(str(db_path)) as conn:
        before = sorted(
            conn.execute(
                "SELECT agent_id, task_type, task_data, status FROM schecule_tasks"
            ).fetchall()
        )

    db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR, up_to="19")

    with sqlite3.connect(str(db_path)) as conn:
        after = sorted(
            conn.execute(
                "SELECT agent_id, job_type, task_data, status FROM schedule_jobs"
            ).fetchall()
        )
        total = conn.execute("SELECT COUNT(*) FROM schedule_jobs").fetchone()[0]

    assert total == len(_SEED_ROWS)
    assert after == before, "除列名外，四列取值必须逐行一致（枚举值名未变，不应有 UPDATE）"


def test_0018_rolls_back_entirely_when_last_statement_fails(tmp_path: Path) -> None:
    """中途失败必须整体回滚：人为让最后一句报错，断言库完全回到迁移前。

    这是本迁移最关键的一条：`executescript` 会隐式 COMMIT，没有显式 BEGIN/COMMIT 时
    失败会留下「表已改名、列没改」的脏状态，导致后续每次启动都 `no such table`。
    """
    db_path = tmp_path / "test.db"
    _migrate_to_0017(db_path)
    _seed(db_path)
    before = _snapshot(db_path)

    broken_dir = tmp_path / "broken_migrations"
    broken_dir.mkdir()
    (broken_dir / MIGRATION_0018).write_text(
        _make_broken_sql(), encoding="utf-8"
    )

    with pytest.raises(sqlite3.OperationalError):
        db.migrate_database(db_path, migrations_dir=broken_dir)

    after = _snapshot(db_path)
    assert after == before, (
        "迁移中途失败后库必须完全未变（表名/列名/索引/数据全同），"
        f"实际差异：{_diff(before, after)}"
    )


def test_0018_declares_explicit_transaction() -> None:
    """迁移必须自带显式 `BEGIN; … COMMIT;`，不能依赖 `with conn:`。

    `db.py:apply_migration` 用 `executescript()` 执行 SQL，它在进入时会先隐式提交，
    `with conn:` 的异常回滚兜不住 DDL。这条用例把「SQL 里必须自己开事务」这个
    前置约定钉在迁移文件上，避免以后有人「优化」掉这两行。
    """
    statements = [ln.strip() for ln in _sql_statement_lines()]
    assert statements.count("BEGIN;") == 1, (
        f"迁移必须恰好有一条 BEGIN; 语句，实际 {statements.count('BEGIN;')} 条"
    )
    assert statements.count("COMMIT;") == 1, (
        f"迁移必须恰好有一条 COMMIT; 语句，实际 {statements.count('COMMIT;')} 条"
    )
    assert statements.index("BEGIN;") == 0, "BEGIN; 必须是第一条语句"
    assert statements.index("COMMIT;") == len(statements) - 1, "COMMIT; 必须是最后一条语句"


def test_0018_is_not_reapplied_after_success(tmp_path: Path) -> None:
    """迁移成功后再次启动不会重跑（已记账），也就不会因旧表不存在而报错。"""
    db_path = tmp_path / "test.db"
    _migrate_to_0017(db_path)
    _seed(db_path)

    first = db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR, up_to="19")
    second = db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR, up_to="19")

    assert first == [MIGRATION_0018]
    assert second == [], "已记账的迁移不应重复执行"

    with sqlite3.connect(str(db_path)) as conn:
        total = conn.execute("SELECT COUNT(*) FROM schedule_jobs").fetchone()[0]
    assert total == len(_SEED_ROWS), "重跑不得动数据"


def _sql_statement_lines() -> list[str]:
    """取迁移文件里真正的 SQL 语句行（去掉空行与 `--` 注释行）。"""
    return [
        ln for ln in (MIGRATIONS_DIR / MIGRATION_0018).read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("--")
    ]


def _make_broken_sql() -> str:
    """在**最后一条**真实语句之前插一句必然失败的 SQL，其余逐字不动。

    两个刻意的设计：

    1. **按行注入，不用 `str.replace("COMMIT;", ...)`**。文件头部注释里也出现了
       `COMMIT;` 字样，全局替换会把注释行生生截断成「半截注释 + 一条真语句」，
       结果脚本在第一条语句就语法错、什么都没执行 —— 库「未变」是因为**根本没开始**，
       这条用例就退化成永远绿的装饰品（测试自己失灵，比测不到更糟）。
    2. **注入点不绑定 `COMMIT`**。若绑定，去掉显式事务的变异版会因「找不到 COMMIT」
       直接红在 helper 里，而不是红在「库被改脏」这个真正要守的断言上 ——
       那样变异验证证明的是「helper 依赖 COMMIT」，不是「事务真的在保护库」。
    """
    raw_lines = (MIGRATIONS_DIR / MIGRATION_0018).read_text(encoding="utf-8").splitlines()
    stmt_idx = [
        i for i, ln in enumerate(raw_lines)
        if ln.strip() and not ln.strip().startswith("--")
    ]
    assert stmt_idx, "迁移文件里没有可执行的 SQL 语句"
    at = stmt_idx[-1]
    return "\n".join(
        raw_lines[:at]
        + ["ALTER TABLE not_exist_table RENAME COLUMN a TO b;", ""]
        + raw_lines[at:]
    ) + "\n"


def _diff(before: dict, after: dict) -> str:
    """给出快照差异的可读描述，失败时直接看这里。"""
    parts = []
    for key in before:
        if before[key] != after[key]:
            parts.append(f"{key}: before={before[key]!r} after={after[key]!r}")
    return "; ".join(parts) if parts else "(无差异)"

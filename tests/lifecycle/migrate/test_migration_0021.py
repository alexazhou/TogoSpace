"""0021 迁移（`agent_containers.note`）的生命周期测试（#153 判据 C5）。

本迁移只做一件事：给 `agent_containers` 加一列可空的 `note`（容器说明）。
本文件钉住四件事：

1. **存量行不受影响**：升级到 0021 后老行 `note` 为 `NULL`，其它列逐列不变；
2. **原有约束与索引一个不少**：`ALTER TABLE ADD COLUMN` 不该动 `idx_agent_containers_agent`、
   `container_id` 唯一索引（NULL 不参与唯一，预记录仍可并存）；
3. **幂等**：已记账后再跑不会报 `duplicate column name`；
4. **全新库一次到位**：从空库跑完所有迁移，`note` 列直接存在（不是「先建表再补列」
   之外的另一条路径）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import db

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "assets/migrate"
MIGRATION_0021 = "0021_add_note_to_agent_containers.sql"


def _migrate_up_to_0020(db_path: Path) -> None:
    """迁移到 0020 为止（`up_to` 是前缀开区间，21 表示 < 0021）。"""
    db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR, up_to="21")


def _seed_pre_0021_rows(db_path: Path) -> None:
    """在 0020 的 schema 上插入存量行（此时还没有 note 列）。"""
    with sqlite3.connect(str(db_path)) as conn:
        conn.executemany(
            "INSERT INTO agent_containers (agent_id, container_id, container_name, image, deleted)"
            " VALUES (?, ?, ?, ?, 0)",
            [
                # 已回填 container_id 的普通记录
                (1, "c0ffee123456", "agent-t1-a-1", "ubuntu:24.04"),
                # 创建中的预记录（container_id 为 NULL）
                (1, None, "agent-t1-a-2", "ubuntu:24.04"),
            ],
        )


def _columns(db_path: Path) -> dict[str, bool]:
    """{列名: 是否 NOT NULL}"""
    with sqlite3.connect(str(db_path)) as conn:
        return {
            row[1]: bool(row[3])
            for row in conn.execute("PRAGMA table_info(agent_containers)")
        }


def _indexes(db_path: Path) -> dict[str, bool]:
    """{索引名: 是否唯一}"""
    with sqlite3.connect(str(db_path)) as conn:
        return {
            row[1]: bool(row[2])
            for row in conn.execute("PRAGMA index_list('agent_containers')")
        }


def _rows(db_path: Path) -> list[tuple]:
    with sqlite3.connect(str(db_path)) as conn:
        return list(
            conn.execute(
                "SELECT agent_id, container_id, container_name, image, deleted, note"
                " FROM agent_containers ORDER BY id"
            )
        )


def test_0021_adds_nullable_note_to_existing_table(tmp_path: Path) -> None:
    """存量库升到 0021：`note` 列存在且可空，老行 `note` 为 NULL，其它列不变。"""
    db_path = tmp_path / "test.db"
    _migrate_up_to_0020(db_path)
    _seed_pre_0021_rows(db_path)
    assert "note" not in _columns(db_path), "0020 及以前不该有 note 列"

    applied = db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR)

    assert applied == [MIGRATION_0021]
    columns = _columns(db_path)
    assert "note" in columns
    assert columns["note"] is False, "note 必须可空：存量行与预记录都没有说明"
    # 其它列的可空性没被 ALTER 改掉
    assert columns["container_id"] is False
    assert columns["container_name"] is True
    assert columns["image"] is True
    assert _rows(db_path) == [
        (1, "c0ffee123456", "agent-t1-a-1", "ubuntu:24.04", 0, None),
        (1, None, "agent-t1-a-2", "ubuntu:24.04", 0, None),
    ]


def test_0021_keeps_existing_indexes_and_uniqueness(tmp_path: Path) -> None:
    """`ALTER TABLE ADD COLUMN` 不该动索引：两个索引还在，`container_id` 仍然唯一。"""
    db_path = tmp_path / "test.db"
    _migrate_up_to_0020(db_path)
    _seed_pre_0021_rows(db_path)

    db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR)

    indexes = _indexes(db_path)
    assert indexes["idx_agent_containers_agent"] is False
    assert indexes["idx_agent_containers_container_id"] is True

    # 预记录的 container_id 为 NULL，仍可并存多条（NULL 不参与唯一）
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO agent_containers (agent_id, container_id, container_name, image, deleted)"
            " VALUES (1, NULL, 'agent-t1-a-3', 'ubuntu:24.04', 0)"
        )
        assert conn.execute("SELECT COUNT(*) FROM agent_containers").fetchone()[0] == 3


def test_0021_is_idempotent_when_already_recorded(tmp_path: Path) -> None:
    """已记账后再跑迁移是空操作：不会 `duplicate column name`，也不会重复记账。"""
    db_path = tmp_path / "test.db"
    db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR)

    applied = db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR)

    assert applied == []
    with sqlite3.connect(str(db_path)) as conn:
        recorded = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM _migrations WHERE name LIKE '0021%'"
            )
        ]
    assert recorded == [MIGRATION_0021]


def test_fresh_database_has_note_from_full_migration_chain(tmp_path: Path) -> None:
    """空库跑完整个迁移链就有 `note`：新装用户与升级用户拿到同一个 schema。"""
    db_path = tmp_path / "test.db"

    db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR)

    assert "note" in _columns(db_path)

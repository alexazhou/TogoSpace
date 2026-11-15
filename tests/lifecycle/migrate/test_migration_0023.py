"""0023 迁移（全局 MCP server 注册表）的生命周期测试（#273）。

本迁移只建一张表：`mcp_servers`（全局、无 team_id、`name` 全局唯一、带
`enable INTEGER NOT NULL DEFAULT 1`）。原 #224 半成品的 `0022_add_mcp_servers.sql`
从未在任何环境执行过（该分支未合并），无存量兼容负担。本文件钉住：

1. **表结构与索引就在位**：列集与 #273 口径一致、无 `team_id` / `alias` 列、
   `ux_mcp_servers_name` 唯一索引存在且真拦得住重名并发写；
2. **编号契约**：主干 `0022` 是 `0022_add_agent_task_stages.sql`（任务系统 v3），
   `0022_add_mcp_servers.sql` 必须不存在（防「把 v3 的 0022 顶掉」回归）；
3. **可在已执行 0022 的库上跑**（交付标准 1 的测试形态）：先迁到 0022 为止、
   再补跑 0023，账本连续且两表都在；
4. **幂等**：已记账后再跑不报错、`applied == []`、数据原样。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import db

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "assets/migrate"
MIGRATION_0023 = "0023_add_mcp_servers.sql"


def _columns(db_path: Path) -> dict[str, bool]:
    """{列名: 是否可空}（PRAGMA 表列信息）。"""
    with sqlite3.connect(str(db_path)) as conn:
        rows = list(conn.execute("PRAGMA table_info(mcp_servers)"))
    return {row[1]: not row[3] for row in rows}


def _indexes(db_path: Path) -> list[str]:
    with sqlite3.connect(str(db_path)) as conn:
        return [row[1] for row in conn.execute("PRAGMA index_list(mcp_servers)")]


def test_0023_schema_columns_and_unique_index(tmp_path: Path) -> None:
    """全新库跑完全部迁移：列集 / 非空约束 / 全局唯一索引全部在位。"""
    db_path = tmp_path / "mcp.db"
    applied = db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR)
    assert MIGRATION_0023 in applied
    assert applied.index(MIGRATION_0023) == len(applied) - 1, "0023 必须是最后一个迁移"

    columns = _columns(db_path)
    assert set(columns) == {
        "id", "name", "kind", "protocol", "target", "note", "config",
        "enable", "created_by", "created_at", "updated_at",
    }
    assert "team_id" not in columns, "#273：注册表不分 team，不得再建 team_id 列"
    assert "alias" not in columns, "#273：alias 已改名 name，不得保留旧列"
    assert columns["name"] is False, "name 必须 NOT NULL"
    assert columns["enable"] is False, "enable 必须 NOT NULL（默认 1）"
    assert columns["protocol"] is True, "protocol 必须可空（非 mcp kind 存 NULL，不用空串）"
    assert "ux_mcp_servers_name" in _indexes(db_path)


def test_0023_unique_index_actually_blocks_duplicate_name(tmp_path: Path) -> None:
    """唯一约束在数据库层兜底：绕过任何应用层预检，重名 insert 必须撞 IntegrityError。"""
    db_path = tmp_path / "mcp.db"
    db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO mcp_servers (name, kind, target) VALUES ('ctx7', 'mcp', 'https://a')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO mcp_servers (name, kind, target) VALUES ('ctx7', 'mcp', 'https://b')"
            )
        # enable 默认 1
        row = conn.execute("SELECT name, enable FROM mcp_servers").fetchone()
        assert row == ("ctx7", 1)


def test_0023_runs_on_db_that_already_executed_0022(tmp_path: Path) -> None:
    """交付标准 1 的测试形态：先迁到 0022 为止，再补跑 0023，账本连续、两表都在。"""
    db_path = tmp_path / "mcp.db"
    applied = db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR, up_to="23")
    assert "0022_add_agent_task_stages.sql" in applied
    assert "0023_add_mcp_servers.sql" not in applied, "up_to=23 不得提前执行 0023"

    applied2 = db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR)
    assert applied2 == [MIGRATION_0023]

    with sqlite3.connect(str(db_path)) as conn:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    assert "mcp_servers" in tables
    assert "agent_task_stages" in tables, "0022 的表不得被 0023 破坏"


def test_0022_task_stages_file_untouched_and_no_mcp_0022(tmp_path: Path) -> None:
    """编号契约：0022 只属于任务系统 v3；MCP 的迁移只能是 0023。"""
    names = {p.name for p in MIGRATIONS_DIR.iterdir()}
    assert "0022_add_agent_task_stages.sql" in names
    assert "0022_add_mcp_servers.sql" not in names, (
        "原 #224 半成品的 0022_add_mcp_servers.sql 必须改名 0023，不许与任务 v3 的 0022 同号"
    )
    assert MIGRATION_0023 in names


def test_0023_idempotent(tmp_path: Path) -> None:
    """幂等：已记账后再跑不报错、applied 为空、表内容原样。"""
    db_path = tmp_path / "mcp.db"
    db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO mcp_servers (name, kind, target, note)"
            " VALUES ('ctx7', 'mcp', 'https://mcp.context7.com/mcp', 'seed')"
        )
    again = db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR)
    assert again == []
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute("SELECT name, note FROM mcp_servers").fetchone()
    assert row == ("ctx7", "seed")

"""启动迁移校验：**数据库比代码新时必须拒绝启动**（#270）。

需求出处：Operator 2026-11-15 17:19（单 #270）。事故背景：`0022_add_agent_task_stages.sql`
已于 05:16 入库，但 15:39–16:34 后端跑的是旧代码 ⇒ 那段时间创建的任务没有节点链与
`current_owner_id`，界面用 fallback 合成假节点链，把「数据不存在」掩盖成「看起来有」。
本单要求：账本里有、代码 `assets/migrate/` 里没有的迁移 ⇒ 拒绝启动（fail fast），
不许只打 warning 继续跑、不许自动跳过、不许自动回滚数据库。

本文件钉住四类行为：

1. **库比代码新 ⇒ 拒绝启动**：抛 `db.DatabaseAheadOfCodeError`，消息里必须**点名**
   缺失的迁移（可指认），并给出可操作提示（升级代码，不要用旧版本连该库）；
2. **代码与库一致 ⇒ 正常**（`applied == []`，不抛）；
3. **库落后于代码 ⇒ 照旧自动补执行**（`applied` 非空，不抛）；
4. **空库首次启动 ⇒ 全部执行成功**（不抛）。

另有一条 `backend_main` 层判据：启动编排（`_run_database_migrations`）遇到「库比代码新」
必须抛出，**不得进入服务状态**，且不得往库里写入任何新迁移记录。
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

import backend_main
import db

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "assets/migrate"
UNKNOWN_MIGRATION = "9999_migration_from_newer_code.sql"


def _all_migration_names() -> list[str]:
    """迁移文件名的**全局排序**（与 db.list_migration_files 的执行顺序同口径）。"""
    return sorted(
        p.name for p in MIGRATIONS_DIR.iterdir() if p.is_file() and p.suffix in (".sql", ".py")
    )


def _fully_migrated_db(tmp_path: Path) -> Path:
    """跑完全部迁移的库（代码与库一致的基线态）。"""
    db_path = tmp_path / "data" / "data.db"
    db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR)
    return db_path


def _ledger_names(db_path: Path) -> list[str]:
    with sqlite3.connect(str(db_path)) as conn:
        return sorted(r[0] for r in conn.execute("SELECT name FROM _migrations"))


def _insert_unknown_ledger_rows(db_path: Path, names: list[str]) -> None:
    """模拟「库被更新的代码迁移过」：账本里写入代码中不存在的迁移名。"""
    with sqlite3.connect(str(db_path)) as conn:
        conn.executemany("INSERT INTO _migrations (name) VALUES (?)", [(n,) for n in names])


# ── 1. 库比代码新 ⇒ 拒绝启动 ─────────────────────────────────────────────────


def test_rejects_when_ledger_has_migration_missing_in_code(tmp_path: Path) -> None:
    """库比代码新：必须抛 DatabaseAheadOfCodeError，且消息点名缺失的迁移。"""
    db_path = _fully_migrated_db(tmp_path)
    _insert_unknown_ledger_rows(db_path, [UNKNOWN_MIGRATION])

    with pytest.raises(db.DatabaseAheadOfCodeError) as excinfo:
        db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR)

    message = str(excinfo.value)
    assert UNKNOWN_MIGRATION in message, f"错误信息没点名缺失的迁移：{message}"
    # 可操作提示：既要告诉人「升级代码」，也要给旧 worktree 一条退路（STORAGE_ROOT 独立数据目录）
    assert "升级" in message, f"错误信息缺少「升级代码」提示：{message}"
    assert "STORAGE_ROOT" in message, f"错误信息缺少旧 worktree 的自救路径：{message}"
    # 拒绝启动不等于「顺手改库」：账本与数据都不许被动过
    assert _ledger_names(db_path) == sorted(_all_migration_names() + [UNKNOWN_MIGRATION])


def test_rejects_and_lists_every_missing_migration(tmp_path: Path) -> None:
    """缺失多条时全部列出（不能只报第一条，否则升级者要试错多轮）。"""
    db_path = _fully_migrated_db(tmp_path)
    missing = [
        "9998_migration_from_newer_code.sql",
        "9999_migration_from_newer_code.sql",
    ]
    _insert_unknown_ledger_rows(db_path, missing)

    with pytest.raises(db.DatabaseAheadOfCodeError) as excinfo:
        db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR)

    message = str(excinfo.value)
    for name in missing:
        assert name in message, f"缺失迁移 {name} 未出现在错误信息里：{message}"


# ── 2/3/4. 正常路径不得被误伤 ────────────────────────────────────────────────


def test_no_error_when_code_and_db_in_sync(tmp_path: Path) -> None:
    """代码与库一致：不抛，且没有迁移被重复执行。"""
    db_path = _fully_migrated_db(tmp_path)
    assert db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR) == []


def test_applies_pending_when_db_behind_code(tmp_path: Path) -> None:
    """库落后于代码：照旧自动补执行（本单不允许改这个行为）。"""
    db_path = _fully_migrated_db(tmp_path)
    names = _all_migration_names()
    newest = names[-1]
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("DELETE FROM _migrations WHERE name = ?", (newest,))

    applied = db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR)
    assert applied == [newest], f"库落后的迁移没有被补执行：{applied}"


def test_empty_db_first_start_runs_all_migrations(tmp_path: Path) -> None:
    """空库首次启动：全部执行成功，不抛。"""
    db_path = tmp_path / "fresh" / "data.db"
    applied = db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR)
    assert applied == _all_migration_names()


# ── 5. 启动编排层：不得进入服务状态、不得写库 ────────────────────────────────


def test_backend_startup_refuses_when_db_ahead_of_code(tmp_path: Path) -> None:
    """启动编排层遇到「库比代码新」必须抛出，且不得往库里补写任何迁移记录。"""
    db_path = _fully_migrated_db(tmp_path)
    _insert_unknown_ledger_rows(db_path, [UNKNOWN_MIGRATION])
    ledger_before = _ledger_names(db_path)

    with pytest.raises(db.DatabaseAheadOfCodeError) as excinfo:
        backend_main._run_database_migrations(str(db_path), str(tmp_path / "workspace"), None)

    assert UNKNOWN_MIGRATION in str(excinfo.value)
    assert _ledger_names(db_path) == ledger_before, "拒绝启动时不应改动迁移账本"


# ── 6. 子集迁移目录同样拒绝（运行时恒为 assets/migrate，子集入参视为代码不全）────


def test_subset_migrations_dir_also_rejected(tmp_path: Path) -> None:
    """迁移目录只含一条迁移（= 代码缺历史迁移）也要拒绝，不放行。

    判据是裸反向差集 `applied - code`：只要库里有代码目录外的迁移就 fail fast，
    不做「版本号是否更大」的放行判断 —— 一旦放行，人为删掉历史迁移文件就能让旧代码
    连上更新的库，保护就漏了。代价是「只想单独重放某条迁移」的人必须用完整目录：
    这种做法（`tests/lifecycle/migrate/test_migration_0018.py` 构造 broken_dir）
    已在本单里改为「完整迁移目录副本 + 把 0018 换成坏脚本」。
    """
    db_path = tmp_path / "data" / "data.db"
    db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR, up_to=18)

    subset_dir = tmp_path / "subset_migrations"
    subset_dir.mkdir()
    target = next(p for p in MIGRATIONS_DIR.iterdir() if p.name.startswith("0018"))
    shutil.copy(target, subset_dir / target.name)

    with pytest.raises(db.DatabaseAheadOfCodeError) as excinfo:
        db.migrate_database(db_path, migrations_dir=subset_dir)
    assert "0017_agent_containers.sql" in str(excinfo.value), "要能点名代码缺失的历史迁移"

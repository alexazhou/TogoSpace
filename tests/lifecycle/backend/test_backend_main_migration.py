"""backend_main 启动迁移编排的测试。

三块行为：
1. 迁移在 ORM 绑定前执行，回调只在有待执行迁移时触发；
2. **迁移前自动备份数据库**（方案 §6.4 项 2）：有待迁移 + 库已存在才备份，且备份内容与源库一致；
3. 备份失败必须中止启动（不调用 `migrate_database`）—— 不支持降级兼容时，备份是唯一回滚路径。
"""

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import appPaths
import backend_main


def _callbacks(events: list) -> backend_main.StartupCallbacks:
    return backend_main.StartupCallbacks(
        on_migration_start=lambda: events.append("start"),
        on_migration_end=lambda: events.append("end"),
    )


def test_run_database_migrations_triggers_callbacks_when_pending(monkeypatch, tmp_path: Path) -> None:
    """有待执行迁移时：先回调 start，再执行迁移，最后回调 end。"""
    events: list = []
    db_path = tmp_path / "data" / "data.db"

    def _fake_migrate_database(path, **kwargs) -> None:
        events.append(("migrate", path, kwargs))

    monkeypatch.setattr(backend_main, "has_pending_migrations", lambda _path: True)
    monkeypatch.setattr(backend_main, "migrate_database", _fake_migrate_database)

    backend_main._run_database_migrations(str(db_path), str(tmp_path / "workspace"), _callbacks(events))

    assert db_path.parent.is_dir()
    assert events == [
        "start",
        ("migrate", str(db_path), {"workspace_root": str(tmp_path / "workspace"), "verbose": True}),
        "end",
    ]


def test_run_database_migrations_skips_callbacks_when_up_to_date(monkeypatch, tmp_path: Path) -> None:
    """没有待执行迁移时仍会调用迁移检查，但不触发开始/结束回调。"""
    events: list = []
    db_path = tmp_path / "data.db"

    monkeypatch.setattr(backend_main, "has_pending_migrations", lambda _path: False)
    monkeypatch.setattr(
        backend_main,
        "migrate_database",
        lambda path, **kwargs: events.append(("migrate", path, kwargs)),
    )

    backend_main._run_database_migrations(str(db_path), str(tmp_path / "workspace"), _callbacks(events))

    assert events == [
        ("migrate", str(db_path), {"workspace_root": str(tmp_path / "workspace"), "verbose": True}),
    ]


def test_run_database_migrations_works_without_callbacks(monkeypatch, tmp_path: Path) -> None:
    """非桌面入口不传回调时，迁移照常执行且不报错。"""
    migrate_database = MagicMock()
    monkeypatch.setattr(backend_main, "has_pending_migrations", lambda _path: True)
    monkeypatch.setattr(backend_main, "migrate_database", migrate_database)

    backend_main._run_database_migrations(str(tmp_path / "data.db"), "", None)

    migrate_database.assert_called_once()


# ── 迁移前自动备份（方案 §6.4 项 2）──────────────────────────────────────────


def _make_sqlite(path: Path, rows: list[tuple]) -> None:
    """造一个内容可校验的小库，供「备份是否与源库一致」比对。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(path)) as conn:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
        conn.executemany("INSERT INTO t (id, name) VALUES (?, ?)", rows)


def _read_rows(path: Path) -> list[tuple]:
    with sqlite3.connect(str(path)) as conn:
        return sorted(conn.execute("SELECT id, name FROM t").fetchall())


def _redirect_backup_dir(monkeypatch, tmp_path: Path) -> Path:
    """把 `appPaths.DATA_DIR` 指到临时目录，避免测试往真实 `dev_storage_root/data/backups` 写垃圾。"""
    data_dir = tmp_path / "storage_data"
    monkeypatch.setattr(appPaths, "DATA_DIR", str(data_dir))
    return data_dir / "backups"


def test_backup_created_before_migration_when_pending(monkeypatch, tmp_path: Path) -> None:
    """有待迁移且库已存在：先备份，备份内容与源库逐行一致，再执行迁移。"""
    backup_dir = _redirect_backup_dir(monkeypatch, tmp_path)
    db_path = tmp_path / "data" / "data.db"
    seeded = [(1, "alice"), (2, "bob")]
    _make_sqlite(db_path, seeded)

    order: list[str] = []
    monkeypatch.setattr(backend_main, "has_pending_migrations", lambda _path: True)

    def _fake_migrate_database(path, **kwargs) -> None:
        # 迁移时断言备份已经落盘（备份必须在 migrate_database 之前）
        backups = sorted(backup_dir.glob("data_*.db"))
        assert backups, "迁移执行时备份文件应已存在"
        order.append("migrate")

    monkeypatch.setattr(backend_main, "migrate_database", _fake_migrate_database)

    backend_main._run_database_migrations(str(db_path), str(tmp_path / "workspace"), None)

    assert order == ["migrate"]
    backups = sorted(backup_dir.glob("data_*.db"))
    assert len(backups) == 1, f"应恰好生成 1 个备份，实际 {[p.name for p in backups]}"
    # 命名沿用既有惯例：{stem}_{YYYYmmdd_HHMMSS_ffffff}{suffix}
    name = backups[0].name
    assert name.startswith("data_") and name.endswith(".db")
    stem = name[len("data_") : -len(".db")]
    assert len(stem) == len("20260705_194824_132501"), f"时间戳段格式不符：{stem}"
    assert _read_rows(backups[0]) == seeded, "备份内容必须与源库一致"


def test_no_backup_when_no_pending_migration(monkeypatch, tmp_path: Path) -> None:
    """无待迁移：不备份（避免每次启动都堆备份），迁移检查照常执行。"""
    backup_dir = _redirect_backup_dir(monkeypatch, tmp_path)
    db_path = tmp_path / "data.db"
    _make_sqlite(db_path, [(1, "alice")])

    monkeypatch.setattr(backend_main, "has_pending_migrations", lambda _path: False)
    migrate_database = MagicMock()
    monkeypatch.setattr(backend_main, "migrate_database", migrate_database)

    backend_main._run_database_migrations(str(db_path), "", None)

    migrate_database.assert_called_once()
    assert not backup_dir.exists() or not list(backup_dir.glob("*.db")), "无待迁移时不应生成备份"


def test_no_backup_when_db_file_absent(monkeypatch, tmp_path: Path) -> None:
    """有待迁移但库文件还不存在（全新安装）：不备份，也不该因此报错。

    ⚠️ 这条用例把 `has_pending_migrations` mock 掉了，**绕过了它的建库副作用**，
    因此只能证明「`db_existed` 为 False 时不备份」，证不了真实路径。
    真实路径由 `test_no_backup_on_fresh_install_with_real_has_pending` 守。
    """
    backup_dir = _redirect_backup_dir(monkeypatch, tmp_path)
    db_path = tmp_path / "fresh" / "data.db"  # 故意不创建

    monkeypatch.setattr(backend_main, "has_pending_migrations", lambda _path: True)
    migrate_database = MagicMock()
    monkeypatch.setattr(backend_main, "migrate_database", migrate_database)

    backend_main._run_database_migrations(str(db_path), "", None)

    migrate_database.assert_called_once()
    assert not backup_dir.exists() or not list(backup_dir.glob("*.db")), "库不存在时不应生成备份"


def test_no_backup_on_fresh_install_with_real_has_pending(monkeypatch, tmp_path: Path) -> None:
    """全新安装：**不 mock `has_pending_migrations`**，走真实调用。

    `has_pending_migrations()` → `migration_status()` → `connect_sqlite()` 里
    `os.makedirs` + `sqlite3.connect` + `ensure_migrations_table()` 会把库文件创建出来，
    所以 `os.path.exists` 若在它**之后**判断就恒为 True。这条用例专门抓这个顺序错误：
    只要 `db_existed` 取错了位置，`backups/` 下就会多出一个只有 `_migrations` 表的空备份。

    ⚠️ **本用例刻意不断言 `db_path.exists()`。** `has_pending_migrations()` 当前会建库，
    但那是它的实现细节，不是这里要守的行为 —— 本用例要守的是「全新安装不生成备份」，
    由末尾 `backups == []` 覆盖。
    若将来有人把它改造成只读（`db.py` docstring 里的长期建议），本用例**应当仍然绿**，
    不该因为它不再建库就误红、挡住一次正确的重构。
    """
    backup_dir = _redirect_backup_dir(monkeypatch, tmp_path)
    db_path = tmp_path / "fresh" / "data.db"
    assert not db_path.exists(), "前置条件：全新安装时库文件不存在"

    migrate_database = MagicMock()
    monkeypatch.setattr(backend_main, "migrate_database", migrate_database)

    backend_main._run_database_migrations(str(db_path), "", None)

    migrate_database.assert_called_once()
    backups = sorted(backup_dir.glob("*.db")) if backup_dir.exists() else []
    assert backups == [], f"全新安装不应生成备份，实际：{[p.name for p in backups]}"


def test_fresh_install_survives_unwritable_backup_dir(monkeypatch, tmp_path: Path) -> None:
    """真实危害场景：全新安装 + `backups/` 不可写（只读卷/磁盘满/权限），启动不能失败。

    用户此时没有任何数据可丢，却因为一个空备份写不进去而启动失败 —— 这就是必须先取
    `db_existed` 的理由。这里用「同名路径被一个普通文件占位」来构造不可写。
    """
    data_dir = tmp_path / "storage_data"
    data_dir.mkdir(parents=True)
    (data_dir / "backups").write_text("occupied")  # 占位：既不是目录，makedirs 会失败
    monkeypatch.setattr(appPaths, "DATA_DIR", str(data_dir))

    db_path = tmp_path / "fresh" / "data.db"
    assert not db_path.exists()

    migrate_database = MagicMock()
    monkeypatch.setattr(backend_main, "migrate_database", migrate_database)

    backend_main._run_database_migrations(str(db_path), "", None)

    migrate_database.assert_called_once()


def test_backup_failure_aborts_startup(monkeypatch, tmp_path: Path) -> None:
    """备份失败必须抛异常且**不执行迁移** —— 不允许「没备份就迁移」。"""
    _redirect_backup_dir(monkeypatch, tmp_path)
    db_path = tmp_path / "data.db"
    _make_sqlite(db_path, [(1, "alice")])

    monkeypatch.setattr(backend_main, "has_pending_migrations", lambda _path: True)

    def _boom(_abs_db_path):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(backend_main, "_backup_database_before_migration", _boom)
    migrate_database = MagicMock()
    monkeypatch.setattr(backend_main, "migrate_database", migrate_database)

    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        backend_main._run_database_migrations(str(db_path), "", None)

    migrate_database.assert_not_called()


def test_failed_backup_leaves_no_partial_file(monkeypatch, tmp_path: Path) -> None:
    """备份中途失败时不能留下半截文件 —— 那会被当成可用的回滚点。"""
    backup_dir = _redirect_backup_dir(monkeypatch, tmp_path)
    source_dir = tmp_path / "src"
    source_path = source_dir / "data.db"
    source_dir.mkdir(parents=True)
    # 源库不是一个合法的 sqlite 文件：`sqlite3.connect` 能开，但 `backup()` 会失败
    source_path.write_bytes(b"not a database")

    with pytest.raises(sqlite3.DatabaseError):
        backend_main._backup_database_before_migration(str(source_path))

    leftovers = list(backup_dir.glob("*.db")) if backup_dir.exists() else []
    assert leftovers == [], f"失败后不应留下任何备份文件，实际：{[p.name for p in leftovers]}"

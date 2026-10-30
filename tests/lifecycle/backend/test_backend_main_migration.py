"""backend_main 启动迁移编排的测试：迁移在 ORM 绑定前执行，回调只在有待执行迁移时触发。"""

from pathlib import Path
from unittest.mock import MagicMock

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

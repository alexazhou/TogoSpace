from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import db

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "assets/migrate"
MIGRATION_0016 = "0016_team_workspace_layout.py"


def _migrate_to_0015(db_path: Path) -> None:
    db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR, up_to="16")


def _migrate_0016(db_path: Path, workspace_root: Path) -> list[str]:
    return db.migrate_database(
        db_path,
        migrations_dir=MIGRATIONS_DIR,
        up_to="17",
        workspace_root=str(workspace_root),
    )


def _applied_migrations(db_path: Path) -> set[str]:
    with sqlite3.connect(str(db_path)) as conn:
        return {name for (name,) in conn.execute("SELECT name FROM _migrations")}


def test_0016_moves_team_entries_into_shared(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    workspace = tmp_path / "workspace"
    team_dir = workspace / "demo"
    (team_dir / "notes").mkdir(parents=True)
    (team_dir / "notes" / "plan.md").write_text("plan", encoding="utf-8")
    (team_dir / "todo.txt").write_text("todo", encoding="utf-8")
    _migrate_to_0015(db_path)

    applied = _migrate_0016(db_path, workspace)

    assert applied == [MIGRATION_0016]
    assert sorted(path.name for path in team_dir.iterdir()) == ["shared"]
    assert (team_dir / "shared" / "todo.txt").read_text(encoding="utf-8") == "todo"
    assert (team_dir / "shared" / "notes" / "plan.md").read_text(encoding="utf-8") == "plan"


def test_0016_is_idempotent(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    team_dir = workspace / "demo"
    (team_dir / "shared").mkdir(parents=True)
    (team_dir / "shared" / "todo.txt").write_text("todo", encoding="utf-8")
    (team_dir / "agent" / "1").mkdir(parents=True)

    first_db = tmp_path / "first.db"
    _migrate_to_0015(first_db)
    _migrate_0016(first_db, workspace)

    second_db = tmp_path / "second.db"
    _migrate_to_0015(second_db)
    applied = _migrate_0016(second_db, workspace)

    assert applied == [MIGRATION_0016]
    assert sorted(path.name for path in team_dir.iterdir()) == ["agent", "shared"]
    assert (team_dir / "shared" / "todo.txt").read_text(encoding="utf-8") == "todo"


def test_0016_conflict_aborts_without_moving_or_recording(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    workspace = tmp_path / "workspace"
    team_dir = workspace / "demo"
    (team_dir / "shared").mkdir(parents=True)
    (team_dir / "shared" / "todo.txt").write_text("existing", encoding="utf-8")
    (team_dir / "todo.txt").write_text("new", encoding="utf-8")
    _migrate_to_0015(db_path)

    with pytest.raises(RuntimeError, match="迁移冲突"):
        _migrate_0016(db_path, workspace)

    assert (team_dir / "todo.txt").read_text(encoding="utf-8") == "new"
    assert (team_dir / "shared" / "todo.txt").read_text(encoding="utf-8") == "existing"
    assert MIGRATION_0016 not in _applied_migrations(db_path)


def test_0016_missing_workspace_root_is_noop(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    _migrate_to_0015(db_path)

    applied = _migrate_0016(db_path, tmp_path / "missing")

    assert applied == [MIGRATION_0016]
    assert not (tmp_path / "missing").exists()


def test_0016_handles_multiple_teams_and_skips_root_files(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("root", encoding="utf-8")
    for team_name in ("alpha", "beta"):
        team_dir = workspace / team_name
        team_dir.mkdir()
        (team_dir / "data.txt").write_text(team_name, encoding="utf-8")

    db_path = tmp_path / "test.db"
    _migrate_to_0015(db_path)
    _migrate_0016(db_path, workspace)

    assert (workspace / "README.md").read_text(encoding="utf-8") == "root"
    for team_name in ("alpha", "beta"):
        moved = workspace / team_name / "shared" / "data.txt"
        assert moved.read_text(encoding="utf-8") == team_name

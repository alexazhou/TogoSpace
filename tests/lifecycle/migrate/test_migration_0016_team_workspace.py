"""0016 迁移（团队工作目录重构）的生命周期测试。

0016 是 Python 迁移：把 workspace/<team>/ 下的存量内容搬到 workspace/<team>/shared/。
它不访问数据库，只接收 workspace_root，因此用例的核心是构造真实的磁盘目录，
再把数据库迁移到"停在 0015"的状态，最后触发 0016 验证搬迁结果。

覆盖场景：
1. 正常搬迁：文件与子目录都进入 shared/；
2. 幂等：已迁移的目录重跑不报错、不破坏结构；
3. 冲突：shared/ 下已有同名条目时整体中止，不动文件、不记账；
4. 全新安装：workspace_root 不存在时静默跳过；
5. 多团队：root 下的杂项文件不受影响，每个团队独立迁移。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import db

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "assets/migrate"
MIGRATION_0016 = "0016_team_workspace_layout.py"


def _migrate_to_0015(db_path: Path) -> None:
    """把数据库迁移到 0015 为止（up_to 是前缀开区间，16 表示 < 0016）。"""
    db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR, up_to="16")


def _migrate_0016(db_path: Path, workspace_root: Path) -> list[str]:
    """只执行 0016 这一步，并传入测试用的 workspace_root。"""
    return db.migrate_database(
        db_path,
        migrations_dir=MIGRATIONS_DIR,
        up_to="17",
        workspace_root=str(workspace_root),
    )


def _applied_migrations(db_path: Path) -> set[str]:
    """读取 _migrations 账本，用于确认失败时没有把 0016 记成已执行。"""
    with sqlite3.connect(str(db_path)) as conn:
        return {name for (name,) in conn.execute("SELECT name FROM _migrations")}


def test_0016_moves_team_entries_into_shared(tmp_path: Path) -> None:
    """团队目录下的普通文件和子目录都应原样搬进 shared/。"""
    db_path = tmp_path / "test.db"
    workspace = tmp_path / "workspace"
    team_dir = workspace / "demo"
    # 同时覆盖「目录」和「文件」两种顶层条目
    (team_dir / "notes").mkdir(parents=True)
    (team_dir / "notes" / "plan.md").write_text("plan", encoding="utf-8")
    (team_dir / "todo.txt").write_text("todo", encoding="utf-8")
    _migrate_to_0015(db_path)

    applied = _migrate_0016(db_path, workspace)

    assert applied == [MIGRATION_0016]
    # 团队根目录下只剩 shared/，说明顶层条目已全部搬走
    assert sorted(path.name for path in team_dir.iterdir()) == ["shared"]
    assert (team_dir / "shared" / "todo.txt").read_text(encoding="utf-8") == "todo"
    assert (team_dir / "shared" / "notes" / "plan.md").read_text(encoding="utf-8") == "plan"


def test_0016_is_idempotent(tmp_path: Path) -> None:
    """已经迁移过的目录再执行一次，应为空操作且不破坏现有结构。"""
    workspace = tmp_path / "workspace"
    team_dir = workspace / "demo"
    # 直接构造「迁移完成后」的布局：只有 shared/ 和 agent/，没有待搬条目
    (team_dir / "shared").mkdir(parents=True)
    (team_dir / "shared" / "todo.txt").write_text("todo", encoding="utf-8")
    (team_dir / "agent" / "alice").mkdir(parents=True)

    first_db = tmp_path / "first.db"
    _migrate_to_0015(first_db)
    _migrate_0016(first_db, workspace)

    # 同一个库里 0016 已记账，会被跳过；换一个全新的库才能让脚本真正重跑一遍
    second_db = tmp_path / "second.db"
    _migrate_to_0015(second_db)
    applied = _migrate_0016(second_db, workspace)

    assert applied == [MIGRATION_0016]
    assert sorted(path.name for path in team_dir.iterdir()) == ["agent", "shared"]
    assert (team_dir / "shared" / "todo.txt").read_text(encoding="utf-8") == "todo"


def test_0016_conflict_aborts_without_moving_or_recording(tmp_path: Path) -> None:
    """shared/ 下已有同名条目时整体中止：源文件、目标文件都不动，0016 不记账。"""
    db_path = tmp_path / "test.db"
    workspace = tmp_path / "workspace"
    team_dir = workspace / "demo"
    # 人为制造冲突：共享区里已经有 todo.txt，同时团队根目录也存在 todo.txt
    (team_dir / "shared").mkdir(parents=True)
    (team_dir / "shared" / "todo.txt").write_text("existing", encoding="utf-8")
    (team_dir / "todo.txt").write_text("new", encoding="utf-8")
    _migrate_to_0015(db_path)

    with pytest.raises(RuntimeError, match="迁移冲突"):
        _migrate_0016(db_path, workspace)

    # 两边内容都保持原样，没有发生覆盖或半迁移
    assert (team_dir / "todo.txt").read_text(encoding="utf-8") == "new"
    assert (team_dir / "shared" / "todo.txt").read_text(encoding="utf-8") == "existing"
    # 失败不记账，下次启动会重试；用户手动解决冲突后即可继续
    assert MIGRATION_0016 not in _applied_migrations(db_path)


def test_0016_missing_workspace_root_is_noop(tmp_path: Path) -> None:
    """全新安装时 workspace_root 还不存在，迁移应静默跳过并正常记账。"""
    db_path = tmp_path / "test.db"
    _migrate_to_0015(db_path)

    applied = _migrate_0016(db_path, tmp_path / "missing")

    assert applied == [MIGRATION_0016]
    # 迁移不应顺手创建目录，目录由运行时按需创建
    assert not (tmp_path / "missing").exists()


def test_0016_handles_multiple_teams_and_skips_root_files(tmp_path: Path) -> None:
    """workspace_root 下的杂项文件不受影响，多个团队目录各自独立迁移。"""
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

    # 根目录文件不属于任何团队，保持原位
    assert (workspace / "README.md").read_text(encoding="utf-8") == "root"
    for team_name in ("alpha", "beta"):
        moved = workspace / team_name / "shared" / "data.txt"
        assert moved.read_text(encoding="utf-8") == team_name

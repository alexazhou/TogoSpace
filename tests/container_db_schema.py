"""容器相关测试共用的「临时库建库」入口：**跑完整迁移到最新**，不硬编码单个迁移文件。

## 为什么要有这个模块

`tests/unit/service/containerService/test_container_core.py`、
`tests/unit/dal/test_gt_agent_container_manager.py`、
`tests/e2e/test_container_happy_path.py`、`tests/e2e/test_container_negative_cases.py`
四处曾各自 `sqlite3.executescript("0017_agent_containers.sql")` 手写建表，理由是
「全量跑迁移太慢」。任务 #153 加 `0021_add_note_to_agent_containers.sql`（`note` 列）
时，这条理由的直接后果出现了：模型带上 `note` 去 insert，四处全报
`no such column: note`。

更根本的问题不是「这次红了」，而是**测试自己的 schema 与生产 schema 分叉**：
以后每加一个迁移，这四处都要人工再改一遍，漏一处就是「测试绿着，生产列不存在」
（或者反过来，测试一直红到有人注意到）。

实测全量迁移（0017 之前的 20 个文件全跑）耗时 **0.037 秒**，慢这个理由本身不成立。

## 用法

    from container_db_schema import migrate_all

    migrate_all(db_path, workspace_root=tmp_path)

跑完之后 `teams` / `agents` / `agent_containers` 等全部表都在，可以直接
`bind_database(AioSqliteDatabase(str(db_path)))` 后用 peewee 模型读写。

**新增迁移不需要改本模块、也不需要改上面四个调用点**——这正是把它抽出来的目的。
"""

from __future__ import annotations

import os
from pathlib import Path

# tests/container_db_schema.py → parents[1] 是仓库根
REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = REPO_ROOT / "assets" / "migrate"


def migrate_all(db_path: str | os.PathLike[str], *, workspace_root: str | os.PathLike[str] = "") -> list[str]:
    """把 `db_path` 迁移到 `assets/migrate/` 里的最新版本，返回本次执行的迁移文件名。

    Args:
        db_path: 目标 SQLite 文件路径（不存在会被创建）
        workspace_root: 传给 Python 迁移（如 `0016_team_workspace_layout.py`）的工作区根目录；
            临时库场景传 `tmp_path` 即可，避免迁移去动真实 workspace

    Returns:
        本次实际执行的迁移文件名列表（按顺序）。

    Note:
        `db.migrate_database` 自己用同步 sqlite3 连接执行迁移，与后面
        `AioSqliteDatabase` 的异步连接池互不干扰（迁移先跑完再 bind）。
    """
    import db as db_tool

    assert MIGRATIONS_DIR.is_dir(), f"迁移目录不存在：{MIGRATIONS_DIR}"
    return db_tool.migrate_database(
        str(db_path),
        migrations_dir=str(MIGRATIONS_DIR),
        workspace_root=str(workspace_root),
    )

"""Team 工作目录重构：workspace/<team>/ 下的内容迁移到 workspace/<team>/shared/。

Python 迁移不访问数据库，只接收 workspace_root：
- 迁移前先做冲突预检，shared/ 下已存在同名条目则整体中止，不移动任何文件；
- 文件系统操作不在数据库事务内，因此实现必须幂等，失败后可以被重跑；
- agent/ 目录由运行时按需创建，这里不处理。
"""

from __future__ import annotations

import os
import shutil

RESERVED_DIRS = {"shared", "agent"}


def migrate(workspace_root: str) -> None:
    if not workspace_root or not os.path.isdir(workspace_root):
        return

    for team_name in sorted(os.listdir(workspace_root)):
        team_dir = os.path.join(workspace_root, team_name)
        if os.path.islink(team_dir) or not os.path.isdir(team_dir):
            continue
        _split_team_dir(team_dir)


def _split_team_dir(team_dir: str) -> None:
    entries = sorted(
        name for name in os.listdir(team_dir)
        if name not in RESERVED_DIRS
    )
    if not entries:
        return

    shared_dir = os.path.join(team_dir, "shared")
    if os.path.lexists(shared_dir) and not os.path.isdir(shared_dir):
        raise RuntimeError(f"Team 目录迁移失败，shared 不是目录: {shared_dir}")

    conflicts = [
        os.path.join(shared_dir, name) for name in entries
        if os.path.lexists(os.path.join(shared_dir, name))
    ]
    if conflicts:
        raise RuntimeError(
            f"Team 目录迁移冲突，shared/ 下已存在同名条目: {conflicts}（目录: {team_dir}）"
        )

    os.makedirs(shared_dir, exist_ok=True)
    for name in entries:
        shutil.move(os.path.join(team_dir, name), os.path.join(shared_dir, name))

"""Team 工作目录重构：workspace/<team>/ 下的内容迁移到 workspace/<team>/shared/。

迁移后的布局：
    workspace/<team>/shared/         # 原团队目录下的全部内容
    workspace/<team>/agent/<工号>/   # Agent 私有目录，由运行时按需创建

Python 迁移不访问数据库，只接收 workspace_root：
- 迁移前先做冲突预检，shared/ 下已存在同名条目则整体中止，不移动任何文件；
- 文件系统操作不在数据库事务内，因此实现必须幂等，失败后可以被重跑；
- agent/ 目录由运行时按需创建，这里不处理。
"""

from __future__ import annotations

import os
import shutil

# 团队目录下的保留子目录：shared 是迁移目标，agent 存放 Agent 私有目录
RESERVED_DIRS = {"shared", "agent"}


def migrate(workspace_root: str) -> None:
    # 全新安装时 workspace_root 可能尚未创建，视为没有存量数据
    if not workspace_root or not os.path.isdir(workspace_root):
        return

    # workspace_root 下的每个子目录对应一个托管团队；
    # 配置了自定义 working_directory 的团队目录不在这里，天然不会被迁移
    for team_name in sorted(os.listdir(workspace_root)):
        team_dir = os.path.join(workspace_root, team_name)
        # 只处理真实目录：普通文件（如 .DS_Store）和符号链接直接跳过
        if os.path.islink(team_dir) or not os.path.isdir(team_dir):
            continue
        _split_team_dir(team_dir)


def _split_team_dir(team_dir: str) -> None:
    # 已迁移过的团队只剩 shared/ 和 agent/，entries 为空即幂等空操作
    entries = sorted(
        name for name in os.listdir(team_dir)
        if name not in RESERVED_DIRS
    )
    if not entries:
        return

    shared_dir = os.path.join(team_dir, "shared")
    # shared 若是普通文件则无法作为目标目录，直接报错交给用户处理
    if os.path.lexists(shared_dir) and not os.path.isdir(shared_dir):
        raise RuntimeError(f"Team 目录迁移失败，shared 不是目录: {shared_dir}")

    # 先预检、后移动：任一目标已存在就整体中止，
    # 避免出现「部分文件已移动」的中间态（重跑时无法自动判断该保留哪一份）
    conflicts = [
        os.path.join(shared_dir, name) for name in entries
        if os.path.lexists(os.path.join(shared_dir, name))
    ]
    if conflicts:
        raise RuntimeError(
            f"Team 目录迁移冲突，shared/ 下已存在同名条目: {conflicts}（目录: {team_dir}）"
        )

    os.makedirs(shared_dir, exist_ok=True)
    # 同盘时 shutil.move 内部就是 os.rename（原子、不复制数据），跨盘才会退化为复制
    for name in entries:
        shutil.move(os.path.join(team_dir, name), os.path.join(shared_dir, name))

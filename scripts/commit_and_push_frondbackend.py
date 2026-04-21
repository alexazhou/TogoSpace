#!/usr/bin/env python3
"""提交前后端代码并推送。

背景:
    本项目包含前端 submodule (frontend/)，提交代码时需要分别处理：
    - 前端必须在 master 分支提交（避免 detached HEAD 状态下提交丢失）
    - 后端需要同步更新 frontend submodule 指针
    - 提交前应先拉取远端代码，避免冲突

    此脚本封装上述流程，简化提交操作。

用法:
    python scripts/commit_and_push_frondbackend.py "commit message"

示例:
    python scripts/commit_and_push_frondbackend.py "fix: improve SPA cache strategy"

流程:
    1. 前端: 切换 master → pull → add/commit → push
    2. 后端: pull → add/commit（含 submodule 指针）→ push

注意:
    - 前后端使用相同的 commit message
    - 遇到冲突或切换失败时，脚本会提示手动处理方式
"""

import subprocess
import sys
from pathlib import Path


def run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    """执行命令，失败时抛异常。"""
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True)


def has_changes(repo: Path) -> bool:
    """检查仓库是否有未提交的改动。"""
    result = run(["git", "status", "--porcelain"], cwd=repo)
    return bool(result.stdout.strip())


def get_current_branch(repo: Path) -> str:
    """获取当前分支名。"""
    result = run(["git", "branch", "--show-current"], cwd=repo)
    return result.stdout.strip()


def safe_checkout_master(frontend: Path) -> None:
    """安全切换到 master 分支，失败时提示用户手动处理。"""
    try:
        run(["git", "checkout", "master"], cwd=frontend)
    except subprocess.CalledProcessError as e:
        print(f"前端切换 master 失败: {e.stderr.strip()}")
        print("请手动处理后再运行此脚本，例如:")
        print("  cd frontend && git stash  # 暂存改动")
        print("  cd frontend && git checkout master")
        print("  cd frontend && git stash pop  # 恢复改动")
        sys.exit(1)


def pull_origin_master(repo: Path, name: str) -> None:
    """拉取远端代码，失败时提示用户处理冲突。"""
    print(f"{name}: 拉取远端代码...")
    try:
        run(["git", "pull", "origin", "master"], cwd=repo)
    except subprocess.CalledProcessError as e:
        print(f"{name}: 拉取失败，可能存在冲突")
        print(e.stderr.strip())
        print("请手动处理冲突后再运行此脚本:")
        print(f"  cd {repo.relative_to(Path.cwd()) if repo != Path.cwd() else repo}")
        print("  git status  # 查看冲突文件")
        print("  # 手动合并冲突")
        print("  git add . && git commit")
        sys.exit(1)


def main() -> None:
    if len(sys.argv) < 2:
        print("用法: python scripts/commit_and_push_frondbackend.py \"commit message\"")
        sys.exit(1)

    commit_msg = sys.argv[1]
    repo_root = Path(__file__).resolve().parent.parent
    frontend = repo_root / "frontend"

    # 前端 submodule
    if frontend.exists():
        branch = get_current_branch(frontend)

        if branch != "master":
            print(f"前端不在 master 分支 (当前: {branch})，切换到 master")
            safe_checkout_master(frontend)

        if has_changes(frontend):
            pull_origin_master(frontend, "前端")
            print("提交前端改动...")
            try:
                run(["git", "add", "-A"], cwd=frontend)
                run(["git", "commit", "-m", commit_msg], cwd=frontend)
                run(["git", "push", "origin", "master"], cwd=frontend)
            except subprocess.CalledProcessError as e:
                print(f"前端提交失败: {e.stderr.strip()}")
                sys.exit(1)
        else:
            print("前端无改动")

    # 后端仓库
    if has_changes(repo_root):
        pull_origin_master(repo_root, "后端")
        print("提交后端改动...")
        try:
            run(["git", "add", "-A"], cwd=repo_root)
            run(["git", "commit", "-m", commit_msg], cwd=repo_root)
            run(["git", "push", "origin", "master"], cwd=repo_root)
        except subprocess.CalledProcessError as e:
            print(f"后端提交失败: {e.stderr.strip()}")
            sys.exit(1)
    else:
        print("后端无改动")

    print("完成")


if __name__ == "__main__":
    main()
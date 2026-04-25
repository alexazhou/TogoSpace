#!/usr/bin/env python3
"""按显式 action 执行前后端状态查看、提交、同步、推送。

背景:
    本项目包含前端 submodule (frontend/)，提交代码时需要分别处理：
    - 前端必须在 master 分支提交（避免 detached HEAD 状态下提交丢失）
    - 后端需要同步更新 frontend submodule 指针
    - sync / push 前需要确认和远端的 ahead / behind 状态，避免误操作

用法:
    python scripts/commit_and_push_frondbackend.py --action status
    python scripts/commit_and_push_frondbackend.py --action commit -m "fix: description"
    python scripts/commit_and_push_frondbackend.py --action push
    python scripts/commit_and_push_frondbackend.py --action sync,commit,push --target all -m "fix: description"

说明:
    - --action 必填，使用逗号分隔动作
    - --target 默认 all，可选 frontend / backend / all
    - 包含 commit 时必须传 -m/--message
    - sync 仅做 fast-forward，不自动 merge
    - status 为独立动作，不与其他 action 混用
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


REMOTE_NAME = "origin"
TARGET_BRANCH = "master"
VALID_ACTIONS = ("status", "sync", "commit", "push")
VALID_ACTION_SEQUENCES = {
    ("status",),
    ("sync",),
    ("commit",),
    ("push",),
    ("sync", "commit"),
    ("sync", "push"),
    ("commit", "push"),
    ("sync", "commit", "push"),
}
VALID_TARGETS = ("frontend", "backend", "all")


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


def safe_switch_master(frontend: Path) -> None:
    """安全切换到 master 分支，失败时提示用户手动处理。"""
    try:
        run(["git", "switch", TARGET_BRANCH], cwd=frontend)
    except subprocess.CalledProcessError as e:
        print(f"前端切换 {TARGET_BRANCH} 失败: {e.stderr.strip()}")
        print("请手动处理后再运行此脚本，例如:")
        print("  cd frontend && git stash  # 暂存改动")
        print(f"  cd frontend && git switch {TARGET_BRANCH}")
        print("  cd frontend && git stash pop  # 恢复改动")
        sys.exit(1)


def fetch_origin_master(repo: Path, name: str) -> None:
    """获取远端分支状态。"""
    print(f"{name}: 获取远端状态...")
    try:
        run(["git", "fetch", REMOTE_NAME, TARGET_BRANCH], cwd=repo)
    except subprocess.CalledProcessError as e:
        print(f"{name}: 获取远端状态失败")
        print(e.stderr.strip())
        sys.exit(1)


def try_fetch_origin_master(repo: Path) -> tuple[bool, str]:
    """尽量获取远端状态；失败时返回错误信息，但不退出。"""
    try:
        run(["git", "fetch", REMOTE_NAME, TARGET_BRANCH], cwd=repo)
        return True, ""
    except subprocess.CalledProcessError as e:
        return False, e.stderr.strip()


def get_ahead_behind(repo: Path) -> tuple[int, int]:
    """返回 (behind, ahead)。"""
    result = run(
        ["git", "rev-list", "--left-right", "--count", f"{REMOTE_NAME}/{TARGET_BRANCH}...HEAD"],
        cwd=repo,
    )
    behind_raw, ahead_raw = result.stdout.strip().split()
    return int(behind_raw), int(ahead_raw)


def get_latest_commit(repo: Path) -> str:
    result = run(["git", "log", "-1", "--oneline"], cwd=repo)
    return result.stdout.strip()


def pull_ff_only(repo: Path, name: str) -> None:
    """仅在可 fast-forward 时拉取远端。"""
    print(f"{name}: fast-forward 拉取远端代码...")
    try:
        run(["git", "pull", "--ff-only", REMOTE_NAME, TARGET_BRANCH], cwd=repo)
    except subprocess.CalledProcessError as e:
        print(f"{name}: 拉取失败，可能需要手动处理")
        print(e.stderr.strip())
        print("请手动处理后再运行此脚本:")
        print(f"  cd {repo}")
        print("  git status")
        print(f"  git pull --ff-only {REMOTE_NAME} {TARGET_BRANCH}")
        sys.exit(1)


def push_origin_master(repo: Path, name: str) -> None:
    """推送到远端 master。"""
    print(f"{name}: 推送到远端...")
    try:
        run(["git", "push", REMOTE_NAME, TARGET_BRANCH], cwd=repo)
    except subprocess.CalledProcessError as e:
        print(f"{name}: 推送失败")
        print(e.stderr.strip())
        sys.exit(1)


def commit_all(repo: Path, name: str, commit_msg: str) -> None:
    """提交当前仓库的全部改动。"""
    print(f"{name}: 提交本地改动...")
    try:
        run(["git", "add", "-A"], cwd=repo)
        run(["git", "commit", "-m", commit_msg], cwd=repo)
    except subprocess.CalledProcessError as e:
        print(f"{name}: 提交失败")
        print(e.stderr.strip())
        sys.exit(1)


def parse_actions(raw: str) -> list[str]:
    """解析并校验 action 列表。"""
    seen: set[str] = set()
    actions: list[str] = []

    for token in raw.split(","):
        action = token.strip().lower()
        if not action:
            continue
        if action not in VALID_ACTIONS:
            print(f"❌ 未知 action: '{action}'（可选: {', '.join(VALID_ACTIONS)}）", file=sys.stderr)
            sys.exit(1)
        if action in seen:
            print(f"❌ 重复 action: '{action}'", file=sys.stderr)
            sys.exit(1)
        seen.add(action)
        actions.append(action)

    if not actions:
        print("❌ --action 不能为空", file=sys.stderr)
        sys.exit(1)

    if tuple(actions) not in VALID_ACTION_SEQUENCES:
        valid_examples = ", ".join(",".join(seq) for seq in VALID_ACTION_SEQUENCES)
        print(f"❌ 非法 action 顺序: '{raw}'", file=sys.stderr)
        print(f"   仅支持: {valid_examples}", file=sys.stderr)
        sys.exit(1)

    return actions


def ensure_message_requirements(actions: list[str], message: str | None) -> None:
    if "commit" in actions and not message:
        print("❌ action 包含 commit 时，必须传 -m/--message", file=sys.stderr)
        sys.exit(1)
    if "commit" not in actions and message:
        print("❌ 未执行 commit 时，不需要传 -m/--message", file=sys.stderr)
        sys.exit(1)


def print_repo_status(repo: Path, name: str) -> None:
    branch = get_current_branch(repo)
    dirty = has_changes(repo)
    latest_commit = get_latest_commit(repo)
    fetched, fetch_error = try_fetch_origin_master(repo)

    print(f"[{name}]")
    print(f"  branch: {branch}")
    print(f"  worktree: {'dirty' if dirty else 'clean'}")
    print(f"  latest: {latest_commit}")

    if fetched:
        behind, ahead = get_ahead_behind(repo)
        print(f"  remote: {REMOTE_NAME}/{TARGET_BRANCH}")
        print(f"  behind: {behind}")
        print(f"  ahead: {ahead}")
    else:
        print(f"  remote: {REMOTE_NAME}/{TARGET_BRANCH} (unavailable)")
        print(f"  fetch_error: {fetch_error}")

    print()


def load_remote_state(repo: Path, name: str) -> tuple[int, int]:
    fetch_origin_master(repo, name)
    return get_ahead_behind(repo)


def ensure_can_sync_or_push(repo: Path, name: str, dirty: bool, behind: int, ahead: int) -> None:
    if dirty and behind > 0:
        print(f"{name}: 存在未提交改动，且本地落后远端 {behind} 个提交，无法安全自动同步")
        print("请先手动处理冲突/同步后再运行脚本")
        print(f"  cd {repo}")
        print("  git status")
        sys.exit(1)

    if behind > 0 and ahead > 0:
        print(f"{name}: 本地与远端已分叉 (behind={behind}, ahead={ahead})，请手动处理")
        print(f"  cd {repo}")
        print("  git status")
        print(f"  git log --oneline --left-right {REMOTE_NAME}/{TARGET_BRANCH}...HEAD")
        sys.exit(1)


def process_repo(
    repo: Path,
    name: str,
    actions: list[str],
    commit_msg: str | None,
    *,
    switch_master: bool = False,
) -> None:
    """按显式 actions 处理单个仓库。"""
    if switch_master:
        branch = get_current_branch(repo)
        if branch != TARGET_BRANCH:
            print(f"{name}: 当前不在 {TARGET_BRANCH} 分支 (当前: {branch})，准备切换")
            safe_switch_master(repo)

    dirty = has_changes(repo)
    behind = 0
    ahead = 0

    if "sync" in actions or "push" in actions:
        behind, ahead = load_remote_state(repo, name)
        ensure_can_sync_or_push(repo, name, dirty, behind, ahead)

    if "sync" in actions:
        if behind > 0:
            pull_ff_only(repo, name)
            behind, ahead = load_remote_state(repo, name)
        else:
            print(f"{name}: 无需同步")

    if "commit" in actions:
        dirty = has_changes(repo)
        if dirty:
            commit_all(repo, name, commit_msg or "")
        else:
            print(f"{name}: 无未提交改动，跳过 commit")

    if "push" in actions:
        behind, ahead = load_remote_state(repo, name)
        ensure_can_sync_or_push(repo, name, has_changes(repo), behind, ahead)
        if ahead > 0:
            push_origin_master(repo, name)
        else:
            print(f"{name}: 无需推送")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TogoAgent 前后端提交/同步/推送脚本")
    parser.add_argument(
        "--action",
        type=str,
        required=True,
        help="要执行的动作，使用逗号分隔，例如: sync,commit,push",
    )
    parser.add_argument(
        "--target",
        type=str,
        default="all",
        choices=VALID_TARGETS,
        help="目标仓库：frontend / backend / all，默认 all",
    )
    parser.add_argument(
        "-m",
        "--message",
        type=str,
        default=None,
        help="commit message；仅在 action 包含 commit 时必填",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    actions = parse_actions(args.action)
    ensure_message_requirements(actions, args.message)

    repo_root = Path(__file__).resolve().parent.parent
    frontend = repo_root / "frontend"

    print(f"ℹ️  action: {','.join(actions)}")
    print(f"ℹ️  target: {args.target}")

    if actions == ["status"]:
        if args.target in ("frontend", "all"):
            print_repo_status(frontend, "前端")
        if args.target in ("backend", "all"):
            print_repo_status(repo_root, "后端")
        print("完成")
        return

    if args.target in ("frontend", "all"):
        process_repo(frontend, "前端", actions, args.message, switch_master=True)

    if args.target in ("backend", "all"):
        process_repo(repo_root, "后端", actions, args.message, switch_master=False)

    print("完成")


if __name__ == "__main__":
    main()

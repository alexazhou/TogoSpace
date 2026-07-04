#!/usr/bin/env python3
"""按显式 action 执行前后端状态查看、提交、同步、推送。

背景:
    本项目包含前端 submodule (frontend/)，提交代码时需要分别处理：
    - 前端必须在有效分支上提交（避免 detached HEAD 状态下提交丢失）
    - 后端需要同步更新 frontend submodule 指针
    - sync / push 前需要确认和远端的 ahead / behind 状态，避免误操作

用法:
    python scripts/commit_and_push_frondbackend.py --action status --frontend-branch llm-settings-redesign
    python scripts/commit_and_push_frondbackend.py --action commit --frontend-branch llm-settings-redesign -m "fix: description"
    python scripts/commit_and_push_frondbackend.py --action push --frontend-branch llm-settings-redesign
    python scripts/commit_and_push_frondbackend.py --action sync,commit,push --target all --frontend-branch llm-settings-redesign -m "fix: description"

说明:
    - --action 必填，使用逗号分隔动作
    - --frontend-branch 必填，指定前端子模块的工作分支
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
MAIN_REPO_DISPLAY_NAME = "主仓库"
FRONTEND_REPO_DISPLAY_NAME = "子模块【前端仓库】"
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


def get_status_lines(repo: Path) -> list[str]:
    """返回 git status --porcelain 的非空行。"""
    result = run(["git", "status", "--porcelain"], cwd=repo)
    return [line for line in result.stdout.splitlines() if line.strip()]


def has_changes(repo: Path) -> bool:
    """检查仓库是否有未提交的改动。"""
    return bool(get_status_lines(repo))


def has_only_path_changes(repo: Path, path: str) -> bool:
    """检查未提交改动是否仅限于指定路径。"""
    lines = get_status_lines(repo)
    if not lines:
        return False
    for line in lines:
        if len(line) < 4:
            return False
        changed_path = line[3:]
        if changed_path != path:
            return False
    return True


def get_current_branch(repo: Path) -> str:
    """获取当前分支名。"""
    result = run(["git", "branch", "--show-current"], cwd=repo)
    return result.stdout.strip()


def safe_switch_branch(frontend: Path, branch: str) -> None:
    """安全切换到指定分支，失败时提示用户手动处理。"""
    try:
        run(["git", "switch", branch], cwd=frontend)
    except subprocess.CalledProcessError as e:
        print(f"{FRONTEND_REPO_DISPLAY_NAME}切换 {branch} 失败: {e.stderr.strip()}")
        print("请手动处理后再运行此脚本，例如:")
        print("  cd frontend && git stash  # 暂存改动")
        print(f"  cd frontend && git switch {branch}")
        print("  cd frontend && git stash pop  # 恢复改动")
        sys.exit(1)


def get_tracking_target(repo: Path, fallback_branch: str | None = None) -> tuple[str, str]:
    """返回当前仓库用于同步/推送的 (remote, branch)。

    优先使用当前分支 upstream；若未设置 upstream，则回退到:
    - fallback_branch（若显式传入）
    - 否则回退到当前分支同名远端分支
    """
    current_branch = get_current_branch(repo)
    try:
        result = run(["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], cwd=repo)
        upstream = result.stdout.strip()
        remote, branch = upstream.split("/", 1)
        return remote, branch
    except subprocess.CalledProcessError:
        return REMOTE_NAME, (fallback_branch or current_branch)


def fetch_remote_branch(repo: Path, name: str, remote: str, branch: str) -> None:
    """获取远端分支状态。"""
    print(f"{name}: 获取远端状态...")
    try:
        run(["git", "fetch", remote, branch], cwd=repo)
    except subprocess.CalledProcessError as e:
        print(f"{name}: 获取远端状态失败")
        print(e.stderr.strip())
        sys.exit(1)


def try_fetch_remote_branch(repo: Path, remote: str, branch: str) -> tuple[bool, str]:
    """尽量获取远端状态；失败时返回错误信息，但不退出。"""
    try:
        run(["git", "fetch", remote, branch], cwd=repo)
        return True, ""
    except subprocess.CalledProcessError as e:
        return False, e.stderr.strip()


def get_ahead_behind(repo: Path, remote: str, branch: str) -> tuple[int, int]:
    """返回 (behind, ahead)。"""
    result = run(
        ["git", "rev-list", "--left-right", "--count", f"{remote}/{branch}...HEAD"],
        cwd=repo,
    )
    behind_raw, ahead_raw = result.stdout.strip().split()
    return int(behind_raw), int(ahead_raw)


def get_latest_commit(repo: Path) -> str:
    result = run(["git", "log", "-1", "--oneline"], cwd=repo)
    return result.stdout.strip()


def get_rev_sha(repo: Path, rev: str) -> str:
    """返回指定 rev 的完整 SHA。"""
    result = run(["git", "rev-parse", rev], cwd=repo)
    return result.stdout.strip()


def get_submodule_recorded_sha(repo: Path, rev: str, submodule_path: str) -> str:
    """读取指定提交中 submodule 记录的 commit SHA。"""
    result = run(["git", "ls-tree", rev, submodule_path], cwd=repo)
    parts = result.stdout.strip().split()
    if len(parts) < 3:
        raise RuntimeError(f"无法读取 {rev}:{submodule_path} 的 submodule 指针")
    return parts[2]


def report_sync_state(repo_root: Path, frontend: Path) -> None:
    """完成后检查前后端子模块指针同步状态，若存在不一致则输出提示。"""
    try:
        frontend_head = get_rev_sha(frontend, "HEAD")
    except Exception:
        return  # submodule 未初始化，跳过检查

    issues: list[str] = []

    # 1. 主仓库工作区是否有未提交的前端指针变更
    status_lines = get_status_lines(repo_root)
    frontend_in_status = any("frontend" in line for line in status_lines)
    if frontend_in_status:
        issues.append("后端主仓库存在未提交的前端指针变更（working tree dirty）")

    # 2. 已提交的指针是否与前端 HEAD 一致
    backend_recorded: str | None = None
    try:
        backend_recorded = get_submodule_recorded_sha(repo_root, "HEAD", "frontend")
        if not frontend_in_status and frontend_head != backend_recorded:
            issues.append(
                f"前端 HEAD ({frontend_head[:12]}) 与后端记录指针 ({backend_recorded[:12]}) 不一致"
            )
    except Exception:
        pass

    # 3. 后端主仓库是否有未推送到远端的提交
    try:
        origin_recorded = get_submodule_recorded_sha(repo_root, "origin/master", "frontend")
        recorded = backend_recorded or get_submodule_recorded_sha(repo_root, "HEAD", "frontend")
        if recorded != origin_recorded:
            issues.append(
                f"后端主仓库有未推送的指针提交（本地: {recorded[:12]}，远端: {origin_recorded[:12]}）"
            )
    except Exception:
        pass

    if issues:
        print()
        print("⚠️  前后端子模块指针同步状态存在问题:")
        for issue in issues:
            print(f"   · {issue}")
        print()
        print("   建议执行:")
        print("   python scripts/commit_and_push_frondbackend.py \\")
        print('       --action commit,push --target backend -m "chore: sync frontend submodule pointer"')


def pull_ff_only(repo: Path, name: str, remote: str, branch: str) -> None:
    """仅在可 fast-forward 时拉取远端。"""
    print(f"{name}: fast-forward 拉取远端代码...")
    try:
        run(["git", "pull", "--ff-only", remote, branch], cwd=repo)
    except subprocess.CalledProcessError as e:
        print(f"{name}: 拉取失败，可能需要手动处理")
        print(e.stderr.strip())
        print("请手动处理后再运行此脚本:")
        print(f"  cd {repo}")
        print("  git status")
        print(f"  git pull --ff-only {remote} {branch}")
        sys.exit(1)


def push_remote_branch(repo: Path, name: str, remote: str, branch: str) -> None:
    """推送到指定远端分支。"""
    print(f"{name}: 推送到远端...")
    try:
        run(["git", "push", remote, f"HEAD:{branch}"], cwd=repo)
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


def print_repo_status(repo: Path, name: str, *, fallback_branch: str | None = None) -> None:
    branch = get_current_branch(repo)
    dirty = has_changes(repo)
    latest_commit = get_latest_commit(repo)
    remote, remote_branch = get_tracking_target(repo, fallback_branch=fallback_branch)
    fetched, fetch_error = try_fetch_remote_branch(repo, remote, remote_branch)

    print(f"[{name}]")
    print(f"  branch: {branch}")
    print(f"  worktree: {'dirty' if dirty else 'clean'}")
    print(f"  latest: {latest_commit}")

    if fetched:
        behind, ahead = get_ahead_behind(repo, remote, remote_branch)
        print(f"  remote: {remote}/{remote_branch}")
        print(f"  behind: {behind}")
        print(f"  ahead: {ahead}")
    else:
        print(f"  remote: {remote}/{remote_branch} (unavailable)")
        print(f"  fetch_error: {fetch_error}")

    print()


def load_remote_state(repo: Path, name: str, remote: str, branch: str) -> tuple[int, int]:
    fetch_remote_branch(repo, name, remote, branch)
    return get_ahead_behind(repo, remote, branch)


def update_submodule_to_recorded_commit(repo: Path, submodule_path: str) -> None:
    """将 submodule 更新到主仓库当前提交记录的指针。"""
    print(f"{MAIN_REPO_DISPLAY_NAME}: 更新{submodule_path}到记录指针...")
    try:
        run(["git", "submodule", "update", "--init", "--recursive", submodule_path], cwd=repo)
    except subprocess.CalledProcessError as e:
        print(f"{MAIN_REPO_DISPLAY_NAME}: 更新{submodule_path}失败")
        print(e.stderr.strip())
        sys.exit(1)


def sync_all_repos(repo_root: Path, frontend: Path, frontend_branch: str) -> None:
    """按主仓库指针统一同步主仓库与前端子模块。"""
    backend_remote, backend_branch = get_tracking_target(repo_root)
    frontend_remote, frontend_remote_branch = get_tracking_target(frontend, fallback_branch=frontend_branch)

    fetch_remote_branch(repo_root, MAIN_REPO_DISPLAY_NAME, backend_remote, backend_branch)
    fetch_remote_branch(frontend, FRONTEND_REPO_DISPLAY_NAME, frontend_remote, frontend_remote_branch)

    backend_dirty = has_changes(repo_root)
    backend_behind, backend_ahead = get_ahead_behind(repo_root, backend_remote, backend_branch)
    frontend_dirty = has_changes(frontend)
    frontend_remote_sha = get_rev_sha(frontend, f"{frontend_remote}/{frontend_remote_branch}")

    if frontend_dirty:
        print(f"{FRONTEND_REPO_DISPLAY_NAME}: 存在未提交改动，无法安全按主仓库指针同步")
        print(f"  cd {frontend}")
        print("  git status")
        sys.exit(1)

    if backend_behind > 0 and backend_ahead > 0:
        print(f"{MAIN_REPO_DISPLAY_NAME}: 本地与远端已分叉 (behind={backend_behind}, ahead={backend_ahead})，请手动处理")
        print(f"  cd {repo_root}")
        print("  git status")
        print(f"  git log --oneline --left-right {backend_remote}/{backend_branch}...HEAD")
        sys.exit(1)

    if backend_dirty and backend_behind > 0:
        if not has_only_path_changes(repo_root, "frontend"):
            print(f"{MAIN_REPO_DISPLAY_NAME}: 存在未提交改动，且本地落后远端 {backend_behind} 个提交，无法安全自动同步")
            print(f"  cd {repo_root}")
            print("  git status")
            sys.exit(1)

        target_frontend_sha = get_submodule_recorded_sha(repo_root, f"{backend_remote}/{backend_branch}", "frontend")
        current_frontend_sha = get_rev_sha(frontend, "HEAD")
        if current_frontend_sha != target_frontend_sha:
            print(f"{MAIN_REPO_DISPLAY_NAME}: 仅 frontend 子模块指针有本地变化，但与远端目标指针不一致，无法安全自动同步")
            print(f"  当前 frontend HEAD: {current_frontend_sha}")
            print(f"  远端目标指针:      {target_frontend_sha}")
            sys.exit(1)

    if backend_behind > 0:
        pull_ff_only(repo_root, MAIN_REPO_DISPLAY_NAME, backend_remote, backend_branch)
    else:
        print(f"{MAIN_REPO_DISPLAY_NAME}: 无需同步")

    recorded_frontend_sha = get_submodule_recorded_sha(repo_root, "HEAD", "frontend")
    update_submodule_to_recorded_commit(repo_root, "frontend")

    current_frontend_sha = get_rev_sha(frontend, "HEAD")
    if current_frontend_sha != recorded_frontend_sha:
        print(f"{FRONTEND_REPO_DISPLAY_NAME}: 更新后 HEAD 与主仓库记录指针不一致，请手动检查")
        print(f"  记录指针: {recorded_frontend_sha}")
        print(f"  当前 HEAD: {current_frontend_sha}")
        sys.exit(1)

    if current_frontend_sha == frontend_remote_sha:
        current_branch = get_current_branch(frontend)
        if current_branch != frontend_branch:
            print(f"{FRONTEND_REPO_DISPLAY_NAME}: 指针等于 {frontend_remote}/{frontend_remote_branch}，切回 {frontend_branch} 分支")
            safe_switch_branch(frontend, frontend_branch)
        else:
            print(f"{FRONTEND_REPO_DISPLAY_NAME}: 已在 {frontend_branch} 分支，无需切换")
    else:
        print(f"{FRONTEND_REPO_DISPLAY_NAME}: 指针未对齐 {frontend_remote}/{frontend_remote_branch}，保持当前 detached HEAD")


def ensure_can_sync_or_push(
    repo: Path,
    name: str,
    dirty: bool,
    behind: int,
    ahead: int,
    remote: str,
    branch: str,
) -> None:
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
        print(f"  git log --oneline --left-right {remote}/{branch}...HEAD")
        sys.exit(1)


def process_repo(
    repo: Path,
    name: str,
    actions: list[str],
    commit_msg: str | None,
    *,
    switch_branch: str | None = None,
) -> None:
    """按显式 actions 处理单个仓库。"""
    if switch_branch:
        branch = get_current_branch(repo)
        if not branch:  # detached HEAD
            print(f"{name}: 当前处于 detached HEAD，切换到 {switch_branch}")
            safe_switch_branch(repo, switch_branch)

    remote, remote_branch = get_tracking_target(
        repo,
        fallback_branch=switch_branch,
    )
    dirty = has_changes(repo)
    behind = 0
    ahead = 0

    if "sync" in actions or "push" in actions:
        behind, ahead = load_remote_state(repo, name, remote, remote_branch)
        ensure_can_sync_or_push(repo, name, dirty, behind, ahead, remote, remote_branch)

    if "sync" in actions:
        if behind > 0:
            pull_ff_only(repo, name, remote, remote_branch)
            behind, ahead = load_remote_state(repo, name, remote, remote_branch)
        else:
            print(f"{name}: 无需同步")

    if "commit" in actions:
        dirty = has_changes(repo)
        if dirty:
            commit_all(repo, name, commit_msg or "")
        else:
            print(f"{name}: 无未提交改动，跳过 commit")

    if "push" in actions:
        behind, ahead = load_remote_state(repo, name, remote, remote_branch)
        ensure_can_sync_or_push(repo, name, has_changes(repo), behind, ahead, remote, remote_branch)
        if ahead > 0:
            push_remote_branch(repo, name, remote, remote_branch)
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
        "--frontend-branch",
        type=str,
        required=True,
        help="前端子模块的工作分支，例如: master, llm-settings-redesign",
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
    print(f"ℹ️  frontend-branch: {args.frontend_branch}")
    print(f"ℹ️  target: {args.target}")

    if actions == ["status"]:
        if args.target in ("frontend", "all"):
            print_repo_status(frontend, FRONTEND_REPO_DISPLAY_NAME, fallback_branch=args.frontend_branch)
        if args.target in ("backend", "all"):
            print_repo_status(repo_root, MAIN_REPO_DISPLAY_NAME)
        print("完成")
        return

    remaining_actions = list(actions)

    if args.target == "all" and "sync" in actions:
        sync_all_repos(repo_root, frontend, args.frontend_branch)
        remaining_actions = [action for action in actions if action != "sync"]

    if remaining_actions:
        if args.target in ("frontend", "all"):
            process_repo(frontend, FRONTEND_REPO_DISPLAY_NAME, remaining_actions, args.message, switch_branch=args.frontend_branch)

        if args.target in ("backend", "all"):
            process_repo(repo_root, MAIN_REPO_DISPLAY_NAME, remaining_actions, args.message)

    report_sync_state(repo_root, frontend)
    print("完成")


if __name__ == "__main__":
    main()

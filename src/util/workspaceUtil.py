"""Team / Agent 工作目录的路径解析与目录操作。

工作目录布局：
    <team 工作目录>/shared/          # 团队共享目录
    <team 工作目录>/agent/<name>/    # Agent 私有目录

托管 Team 的工作目录为 workspace_root/<team>；配置了 working_directory
的 Team 使用用户指定的固定目录，但内部布局保持一致。
"""

import logging
import os
import shutil
import unicodedata
from typing import Any

from exception import TogoException
from util import configUtil

logger = logging.getLogger(__name__)

_SHARED_DIR_NAME = "shared"
_AGENT_DIR_NAME = "agent"

# 名称会直接作为目录名：黑名单方式，只禁止会影响跨平台路径安全的字符
# （路径分隔符、Windows 非法字符、控制字符），其余（含 emoji、任意 Unicode）一律放行
_NAME_INVALID_CHARS = frozenset("/\\:*?\"<>|")
_WINDOWS_RESERVED_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)


def _is_control_char(char: str) -> bool:
    """控制字符（Unicode Cc：含 NUL、换行、Tab、DEL 等），不可见或会引发崩溃。"""
    return unicodedata.category(char) == "Cc"


def validate_dir_name(name: str) -> str | None:
    """校验 Team / Agent 名称可直接作为跨平台目录名。

    纯校验函数：不抛异常，错误统一通过返回值表达，由调用方决定如何处理。

    Returns:
        合法返回 None；否则返回给调用方展示的错误信息。
    """
    if not isinstance(name, str):
        return "必须是字符串"
    if not name:
        return "不能为空"
    if name != name.strip():
        return "不能以空白开头或结尾"
    if name in {".", ".."}:
        return f"不能为 {name}"
    stem = name.split(".", 1)[0]
    if stem.upper() in _WINDOWS_RESERVED_NAMES:
        return f"不能使用系统保留名称: {name}"
    invalid_chars = sorted({
        char for char in name
        if char in _NAME_INVALID_CHARS or _is_control_char(char)
    })
    if invalid_chars:
        invalid_display = ", ".join(repr(char) for char in invalid_chars)
        return f"不能包含非法字符: {invalid_display}"
    return None


def get_custom_workdir(team_config: dict[str, Any] | None) -> str:
    """读取 Team 自定义 working_directory；未配置返回空字符串。"""
    if not isinstance(team_config, dict):
        return ""
    return str(team_config.get("working_directory") or "").strip()


def resolve_workspace_root(workspace_root: str | None) -> str:
    """显式传入优先，否则回退到应用配置中的 workspace_root。"""
    return workspace_root or configUtil.get_app_config().setting.workspace_root


def resolve_team_workdir(
    team_name: str,
    team_config: dict[str, Any] | None,
    workspace_root: str | None,
) -> str:
    """Team 工作目录：自定义 working_directory 优先，否则为 <workspace_root>/<team>。"""
    configured_workdir = get_custom_workdir(team_config)
    if configured_workdir:
        return configured_workdir

    assert workspace_root is not None, "workspace_root 未配置"
    return os.path.join(workspace_root, team_name)


def resolve_shared_workdir(team_workdir: str) -> str:
    """团队共享目录为 <team 工作目录>/shared。"""
    return os.path.join(team_workdir, _SHARED_DIR_NAME)


def resolve_agent_workdir(team_workdir: str, agent_name: str) -> str:
    """Agent 私有目录为 <team 工作目录>/agent/<name>。"""
    return os.path.join(team_workdir, _AGENT_DIR_NAME, str(agent_name))


def resolve_agent_root(
    team_name: str,
    team_config: dict[str, Any] | None,
    workspace_root: str | None = None,
) -> str:
    """所有 Agent 私有目录的父目录：<team 工作目录>/agent。"""
    team_workdir = resolve_team_workdir(team_name, team_config, resolve_workspace_root(workspace_root))
    return os.path.join(team_workdir, _AGENT_DIR_NAME)


def rename_directory(old_dir: str, new_dir: str, *, label: str) -> tuple[str, str] | None:
    """重命名单个目录：源不存在时跳过，目标已存在时报冲突（不覆盖）。"""
    if old_dir == new_dir or not os.path.isdir(old_dir):
        return None
    if os.path.lexists(new_dir):
        raise TogoException(
            error_message=f"{label}目录重命名失败，目标目录已存在: {new_dir}",
            error_code="workdir_rename_conflict",
        )
    os.rename(old_dir, new_dir)
    return old_dir, new_dir


def rollback_directory_renames(moves: list[tuple[str, str]], *, label: str) -> None:
    """按相反顺序撤销已完成的目录改名；撤销失败只记日志，避免掩盖原始异常。"""
    for old_dir, new_dir in reversed(moves):
        try:
            os.rename(new_dir, old_dir)
        except Exception:
            logger.exception("%s目录重命名回滚失败，请手动检查: %s -> %s", label, new_dir, old_dir)


def rename_team_workdir(
    team_config: dict[str, Any] | None,
    old_name: str,
    new_name: str,
) -> list[tuple[str, str]]:
    """Team 改名后同步重命名托管工作目录，返回实际执行的移动。

    自定义 working_directory 是用户指定的固定路径，不随 Team 名称变化，这里不处理。
    """
    if get_custom_workdir(team_config):
        return []
    workspace_root = resolve_workspace_root(None)
    moved = rename_directory(
        os.path.join(workspace_root, old_name),
        os.path.join(workspace_root, new_name),
        label=f"Team '{old_name}'",
    )
    return [moved] if moved else []


def rename_agent_workdirs(
    team_name: str,
    team_config: dict[str, Any] | None,
    renames: list[tuple[str, str]],
    workspace_root: str | None = None,
) -> list[tuple[str, str]]:
    """Agent 改名后同步重命名各自的私有目录，返回实际执行的移动。

    每个 Agent 的改名都是一次独立的目录重命名；中途失败时回滚已完成的移动。
    """
    if not renames:
        return []
    agent_root = resolve_agent_root(team_name, team_config, workspace_root)
    label = f"Agent in team '{team_name}'"
    moved: list[tuple[str, str]] = []
    try:
        for old_name, new_name in renames:
            completed = rename_directory(
                os.path.join(agent_root, old_name),
                os.path.join(agent_root, new_name),
                label=label,
            )
            if completed:
                moved.append(completed)
    except Exception:
        rollback_directory_renames(moved, label=label)
        raise
    return moved


def rollback_agent_workdirs(team_name: str, moves: list[tuple[str, str]]) -> None:
    """撤销 rename_agent_workdirs 已执行的移动（写库失败时调用）。"""
    if moves:
        rollback_directory_renames(moves, label=f"Agent in team '{team_name}'")


def remove_agent_workdirs(
    team_name: str,
    team_config: dict[str, Any] | None,
    offboard_names: list[str],
    on_board_names: list[str],
    workspace_root: str | None = None,
) -> list[str]:
    """删除离职成员的私有目录，返回实际删除的路径。

    目录按 name 共用：离职成员的名字可能已被新成员复用，
    只要还有在岗成员使用该名字，就保留目录。
    """
    if not offboard_names:
        return []
    agent_root = resolve_agent_root(team_name, team_config, workspace_root)
    in_use = set(on_board_names)
    removed: list[str] = []
    for name in dict.fromkeys(offboard_names):
        if not name or name in in_use:
            continue
        workdir = os.path.join(agent_root, name)
        if not os.path.isdir(workdir):
            continue
        try:
            shutil.rmtree(workdir)
        except Exception:
            logger.exception("离职成员 '%s' 的个人目录删除失败: %s", name, workdir)
            continue
        removed.append(workdir)
    return removed

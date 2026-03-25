import os
import sys

from service.agentService.driver.tspDriver import build_gtsp_command

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


def test_build_gtsp_command_uses_default_binary_and_workspace_flags():
    cmd = build_gtsp_command(None, workdir="/tmp/team-a", workdir_root="/tmp/workspaces")
    assert cmd[0].endswith("assert/execute/gtsp")
    assert "--mode" in cmd
    assert "stdio" in cmd
    assert "--workdir" in cmd
    assert "/tmp/team-a" in cmd
    assert "--workdir-root" in cmd
    assert "/tmp/workspaces" in cmd


def test_build_gtsp_command_respects_explicit_command_and_no_duplicate_flags():
    cmd = build_gtsp_command(
        ["./gtsp", "--mode", "stdio", "--workdir", "/custom/workdir"],
        workdir="/tmp/team-a",
        workdir_root="/tmp/workspaces",
    )
    assert cmd.count("--workdir") == 1
    assert "/custom/workdir" in cmd
    assert "--workdir-root" in cmd
    assert "/tmp/workspaces" in cmd


def test_build_gtsp_command_parses_string_command():
    cmd = build_gtsp_command(
        "./assert/execute/gtsp --mode stdio",
        workdir="/tmp/team-a",
        workdir_root="/tmp/workspaces",
    )
    assert cmd[0].endswith("assert/execute/gtsp")
    assert "--mode" in cmd
    assert "--workdir" in cmd

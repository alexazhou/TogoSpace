import os
import sys
import json
import shutil
import uuid
from dataclasses import dataclass, field

import pytest

from util import llmApiUtil
from service.agentService.driver.base import AgentDriverConfig
from service.agentService.driver.tspDriver import build_gtsp_command, TspAgentDriver

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


def test_build_gtsp_command_uses_default_binary_and_workdir_flag():
    cmd = build_gtsp_command(None, workdir="/tmp/team-a")
    assert cmd[0].endswith("assert/execute/gtsp")
    assert "--mode" in cmd
    assert "stdio" in cmd
    assert "--workdir" in cmd
    assert "/tmp/team-a" in cmd
    assert "--workdir-root" not in cmd


def test_build_gtsp_command_respects_explicit_command_and_no_duplicate_flags():
    cmd = build_gtsp_command(
        ["./gtsp", "--mode", "stdio", "--workdir", "/custom/workdir"],
        workdir="/tmp/team-a",
    )
    assert cmd.count("--workdir") == 1
    assert "/custom/workdir" in cmd
    assert "--workdir-root" not in cmd



@dataclass
class _DummyHost:
    name: str = "实习生"
    team_name: str = "default"
    system_prompt: str = ""
    model: str = "mock-model"
    team_workdir: str = "/tmp"
    workspace_root: str = "/tmp/workspaces"
    current_room: object | None = None
    _history: list[llmApiUtil.LlmApiMessage] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.name}@{self.team_name}"


@pytest.mark.asyncio
async def test_tsp_driver_e2e_initialize_tool_shutdown():
    binary_path = build_gtsp_command(None, workdir="/tmp/team-a")[0]
    if not os.path.isfile(binary_path) or not os.access(binary_path, os.X_OK):
        pytest.skip(f"real gtsp binary not available: {binary_path}")

    tmp_dir = f"/tmp/tsp_driver_e2e_{uuid.uuid4().hex[:8]}"
    file_path = f"{tmp_dir}/hello.txt"
    expected_content = "hello from tsp e2e\nline2\n"

    host = _DummyHost()
    config = AgentDriverConfig(
        driver_type="tsp",
        options={
            "request_timeout_sec": 5,
            "workdir": "/tmp",
            "command": [binary_path, "--mode", "stdio", "--workdir-root", "/"],
        },
    )
    driver = TspAgentDriver(host, config)

    await driver.startup()
    try:
        # initialize 阶段：应加载出 gtsp 工具
        assert driver._tsp_tools
        assert driver._tsp_tool_names

        # 1) 创建 /tmp 下测试目录
        mkdir_result = await driver._execute_tsp_tool(
            "execute_bash",
            json.dumps({"command": f"mkdir -p {tmp_dir}"}, ensure_ascii=False),
        )
        assert isinstance(mkdir_result, dict)
        assert mkdir_result.get("exit_code") == 0

        # 2) 写文件
        write_result = await driver._execute_tsp_tool(
            "write_file",
            json.dumps({"file_path": file_path, "content": expected_content}, ensure_ascii=False),
        )
        assert isinstance(write_result, dict)
        assert write_result.get("file_path") == file_path

        # 3) list 目录并确认文件存在
        list_result = await driver._execute_tsp_tool(
            "list_dir",
            json.dumps({"dir_path": tmp_dir, "recursive": False}, ensure_ascii=False),
        )
        assert isinstance(list_result, dict)
        items = list_result.get("items", [])
        names = {item.get("name") for item in items if isinstance(item, dict)}
        assert "hello.txt" in names

        # 4) read 文件并校验内容一致
        read_result = await driver._execute_tsp_tool(
            "read_file",
            json.dumps({"file_path": file_path}, ensure_ascii=False),
        )
        assert isinstance(read_result, dict)
        assert read_result.get("content") == expected_content
    finally:
        # shutdown 阶段：应能优雅断连
        await driver.shutdown()
        shutil.rmtree(tmp_dir, ignore_errors=True)

    assert driver._client is None

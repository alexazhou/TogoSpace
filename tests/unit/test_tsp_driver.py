import os
import sys
import json
from dataclasses import dataclass, field

import pytest

from util import llmApiUtil
from service.agentService.driver.base import AgentDriverConfig
from service.agentService.driver.tspDriver import build_gtsp_command, TspAgentDriver

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

def _build_minimal_args(properties: dict, required: list[str]) -> dict[str, object]:
    payload: dict[str, object] = {}
    for field_name in required:
        field_schema = properties.get(field_name, {}) if isinstance(properties, dict) else {}
        field_type = field_schema.get("type")
        if field_type == "string":
            payload[field_name] = "."
        elif field_type == "integer":
            payload[field_name] = 0
        elif field_type == "number":
            payload[field_name] = 0
        elif field_type == "boolean":
            payload[field_name] = False
        elif field_type == "array":
            payload[field_name] = []
        elif field_type == "object":
            payload[field_name] = {}
        else:
            payload[field_name] = ""
    return payload


@pytest.mark.asyncio
async def test_tsp_driver_e2e_initialize_tool_shutdown():
    binary_path = build_gtsp_command(None, workdir="/tmp/team-a", workdir_root="/tmp/workspaces")[0]
    if not os.path.isfile(binary_path) or not os.access(binary_path, os.X_OK):
        pytest.skip(f"real gtsp binary not available: {binary_path}")

    host = _DummyHost()
    config = AgentDriverConfig(
        driver_type="tsp",
        options={
            "request_timeout_sec": 5,
        },
    )
    driver = TspAgentDriver(host, config)

    await driver.startup()
    try:
        # initialize 阶段：应加载出 gtsp 工具
        assert driver._tsp_tools
        assert driver._tsp_tool_names

        # tool 阶段：挑一个工具发起真实调用
        candidate_tool = min(driver._tsp_tools, key=lambda t: len(t.function.parameters.required))
        args = _build_minimal_args(
            properties=candidate_tool.function.parameters.properties,
            required=candidate_tool.function.parameters.required,
        )
        result = await driver._execute_tsp_tool(
            candidate_tool.function.name,
            json.dumps(args, ensure_ascii=False),
        )
        assert isinstance(result, dict)
        # 返回可以是成功结果，也可以是参数校验错误；只要无异常返回 dict 即视为 tool 链路已执行
    finally:
        # shutdown 阶段：应能优雅断连
        await driver.shutdown()

    assert driver._client is None

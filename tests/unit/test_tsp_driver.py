import os
import sys
import json
import shutil
import uuid
import asyncio
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytspclient import TSPClient, TSPException

from util import llmApiUtil
from exception import TeamAgentException
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
    _history: list[llmApiUtil.OpenAIMessage] = field(default_factory=list)

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

@pytest.fixture
def mock_tsp_host():
    host = MagicMock()
    host.name = "tsp_agent"
    host.team_name = "test_team"
    host.key = "tsp_agent@test_team"
    host.team_workdir = "/tmp"
    host._infer = AsyncMock()
    host.append_history_message = AsyncMock()
    host.current_room = MagicMock()
    return host

@pytest.mark.asyncio
async def test_tsp_driver_execute_tool_calls_local_vs_tsp(mock_tsp_host):
    config = AgentDriverConfig(driver_type="tsp", options={})
    
    with patch("service.funcToolService.get_tools_by_names", return_value=[
        llmApiUtil.OpenAITool(function=llmApiUtil.OpenAIFunction(
            name="send_chat_msg", 
            description="", 
            parameters=llmApiUtil.OpenAIFunctionParameter(type="object", properties={}, required=[])
        ))
    ]):
        driver = TspAgentDriver(mock_tsp_host, config)
        driver._tsp_tools = {
            "tsp_tool": llmApiUtil.OpenAITool(function=llmApiUtil.OpenAIFunction(
                name="tsp_tool", 
                description="", 
                parameters=llmApiUtil.OpenAIFunctionParameter(type="object", properties={}, required=[])
            ))
        }
        
        # Mock _execute_tsp_tool
        driver._execute_tsp_tool = AsyncMock(return_value={"success": True})
        
        # Case 1: Local tool
        tool_calls = [
            llmApiUtil.OpenAIToolCall(id="c1", function={"name": "send_chat_msg", "arguments": "{}"})
        ]
        with patch("service.funcToolService.run_tool_call", return_value='{"success": true}'):
            await driver._execute_tool_calls(tool_calls)
            mock_tsp_host.append_history_message.assert_called()
            
        # Case 2: TSP tool
        tool_calls = [
            llmApiUtil.OpenAIToolCall(id="c2", function={"name": "tsp_tool", "arguments": "{}"})
        ]
        await driver._execute_tool_calls(tool_calls)
        driver._execute_tsp_tool.assert_called_with("tsp_tool", "{}")
        
        # Case 3: Unknown tool
        tool_calls = [
            llmApiUtil.OpenAIToolCall(id="c3", function={"name": "unknown", "arguments": "{}"})
        ]
        await driver._execute_tool_calls(tool_calls)
        last_msg = mock_tsp_host.append_history_message.call_args[0][0]
        assert "未知工具" in last_msg.content

@pytest.mark.asyncio
async def test_tsp_driver_execute_tsp_tool_error_handling(mock_tsp_host):
    config = AgentDriverConfig(driver_type="tsp", options={})
    driver = TspAgentDriver(mock_tsp_host, config)
    driver._client = MagicMock()
    driver._client.tool = AsyncMock()
    
    # Case 1: JSON Decode Error
    res = await driver._execute_tsp_tool("tool", "invalid json")
    assert "JSON 解析失败" in res["message"]
    
    # Case 2: TSP Exception
    driver._client.tool.side_effect = TSPException("tsp/code", "tsp error")
    res = await driver._execute_tsp_tool("tool", "{}")
    assert res["code"] == "tsp/code"
    assert res["message"] == "tsp error"
    
    # Case 3: General Exception
    driver._client.tool.side_effect = RuntimeError("network fail")
    res = await driver._execute_tsp_tool("tool", "{}")
    assert "工具调用失败" in res["message"]

@pytest.mark.asyncio
async def test_tsp_client_fail_pending():
    client = TSPClient(["mock"])
    
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    client.in_flight["req1"] = fut
    
    client._fail_pending(RuntimeError("closed"))
    
    with pytest.raises(RuntimeError, match="closed"):
        await fut
    assert len(client.in_flight) == 0

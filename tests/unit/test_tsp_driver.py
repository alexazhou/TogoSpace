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

from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtAgentHistory import GtAgentHistory
from model.dbModel.gtAgentTask import GtAgentTask
from util import llmApiUtil
from constants import AgentHistoryTag
from service.agentService.agentHistoryStore import AgentHistoryStore
from service.agentService.driver.base import AgentDriverConfig
from service.agentService.driver.tspDriver import build_gtsp_command, TspAgentDriver
from service.agentService.toolRegistry import AgentToolRegistry
from service.roomService import ToolCallContext

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


def test_build_gtsp_command_uses_default_binary_and_workdir_flag():
    try:
        cmd = build_gtsp_command(None, workdir="/tmp/team-a")
    except FileNotFoundError as e:
        pytest.skip(f"gtsp binary not available on this platform: {e}")
    assert "assets/execute/gtsp" in cmd[0]
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
    gt_agent: GtAgent = field(default_factory=lambda: GtAgent(id=1, team_id=1, name="实习生", role_template_id=1, model="mock-model"))
    name: str = "实习生"
    team_name: str = "default"
    system_prompt: str = ""
    model: str = "mock-model"
    team_workdir: str = "/tmp"
    workspace_root: str = "/tmp/workspaces"
    current_db_task: GtAgentTask | None = None
    _history: AgentHistoryStore = field(default_factory=lambda: AgentHistoryStore(agent_id=1))
    tool_registry: AgentToolRegistry = field(default_factory=AgentToolRegistry)


@pytest.mark.asyncio
async def test_tsp_driver_e2e_initialize_tool_shutdown():
    try:
        binary_path = build_gtsp_command(None, workdir="/tmp/team-a")[0]
    except FileNotFoundError as e:
        pytest.skip(f"real gtsp binary not available: {e}")
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
        mkdir_ctx = ToolCallContext(agent_name="e2e", team_id=1, chat_room=MagicMock(), tool_name="execute_bash")
        mkdir_result = await driver._execute_tsp_tool(
            json.dumps({"command": f"mkdir -p {tmp_dir}"}, ensure_ascii=False),
            mkdir_ctx,
        )
        assert isinstance(mkdir_result, dict)
        assert mkdir_result.get("exit_code") == 0

        # 2) 写文件
        write_ctx = ToolCallContext(agent_name="e2e", team_id=1, chat_room=MagicMock(), tool_name="write_file")
        write_result = await driver._execute_tsp_tool(
            json.dumps({"file_path": file_path, "content": expected_content}, ensure_ascii=False),
            write_ctx,
        )
        assert isinstance(write_result, dict)
        assert write_result.get("file_path") == file_path

        # 3) list 目录并确认文件存在
        list_ctx = ToolCallContext(agent_name="e2e", team_id=1, chat_room=MagicMock(), tool_name="list_dir")
        list_result = await driver._execute_tsp_tool(
            json.dumps({"dir_path": tmp_dir, "recursive": False}, ensure_ascii=False),
            list_ctx,
        )
        assert isinstance(list_result, dict)
        items = list_result.get("items", [])
        names = {item.get("name") for item in items if isinstance(item, dict)}
        assert "hello.txt" in names

        # 4) read 文件并校验内容一致
        read_ctx = ToolCallContext(agent_name="e2e", team_id=1, chat_room=MagicMock(), tool_name="read_file")
        read_result = await driver._execute_tsp_tool(
            json.dumps({"file_path": file_path}, ensure_ascii=False),
            read_ctx,
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
    host.gt_agent = MagicMock()
    host.gt_agent.id = 1
    host.name = "tsp_agent"
    host.team_name = "test_team"
    host.team_workdir = "/tmp"
    host._infer = AsyncMock()
    host.append_history_message = AsyncMock()
    host.current_db_task = MagicMock()
    host.tool_registry = AgentToolRegistry()
    return host

@pytest.mark.asyncio
async def test_tsp_driver_setup_registers_local_and_tsp_tools(mock_tsp_host):
    config = AgentDriverConfig(driver_type="tsp", options={})
    
    with patch("service.funcToolService.get_tools_by_names", return_value=[
        llmApiUtil.OpenAITool(function=llmApiUtil.OpenAIFunction(
            name="send_chat_msg", 
            description="", 
            parameters=llmApiUtil.OpenAIFunctionParameter(type="object", properties={}, required=[])
        )),
        llmApiUtil.OpenAITool(function=llmApiUtil.OpenAIFunction(
            name="finish_chat_turn",
            description="",
            parameters=llmApiUtil.OpenAIFunctionParameter(type="object", properties={}, required=[])
        ))
    ]):
        driver = TspAgentDriver(mock_tsp_host, config)
        driver._client = MagicMock()
        driver._tsp_tools = {
            "tsp_tool": llmApiUtil.OpenAITool(function=llmApiUtil.OpenAIFunction(
                name="tsp_tool", 
                description="", 
                parameters=llmApiUtil.OpenAIFunctionParameter(type="object", properties={}, required=[])
            ))
        }

        driver._execute_tsp_tool = AsyncMock(return_value={"success": True})
        run_tool_call = AsyncMock(return_value={"success": True})
        with patch("service.funcToolService.run_tool_call", run_tool_call):
            driver._register_host_tools()
            context = ToolCallContext(
                agent_name="alice",
                team_id=1,
                chat_room=MagicMock(),
            )
            finish_result = await mock_tsp_host.tool_registry.execute_tool_call(
                llmApiUtil.OpenAIToolCall(id="c1", function={"name": "finish_chat_turn", "arguments": "{}"}),
                context=context,
            )

        setup = driver.turn_setup
        assert setup.max_retries == 3

        exported_names = [tool.function.name for tool in mock_tsp_host.tool_registry.export_openai_tools()]
        assert exported_names == ["send_chat_msg", "finish_chat_turn", "tsp_tool"]

        run_tool_call.assert_called_once()
        called_args, called_context = run_tool_call.call_args.args
        assert called_args == "{}"
        assert called_context.agent_name == "alice"
        assert called_context.team_id == 1
        assert called_context.tool_name == "finish_chat_turn"
        assert finish_result.turn_finished is True
        assert finish_result.tags == [AgentHistoryTag.ROOM_TURN_FINISH]

        tsp_result = await mock_tsp_host.tool_registry.execute_tool_call(
            llmApiUtil.OpenAIToolCall(id="c2", function={"name": "tsp_tool", "arguments": "{}"}),
            context=context,
        )
        driver._execute_tsp_tool.assert_called_once()
        tsp_called_args, tsp_called_context = driver._execute_tsp_tool.call_args.args
        assert tsp_called_args == "{}"
        assert tsp_called_context.tool_name == "tsp_tool"
        assert json.loads(tsp_result.result_json)["success"] is True

        unknown_result = await mock_tsp_host.tool_registry.execute_tool_call(
            llmApiUtil.OpenAIToolCall(id="c3", function={"name": "unknown", "arguments": "{}"}),
            context=context,
        )
        assert "未知工具" in unknown_result.result_json


@pytest.mark.asyncio
async def test_tsp_driver_run_chat_turn_is_disabled(mock_tsp_host):
    config = AgentDriverConfig(driver_type="tsp", options={})
    driver = TspAgentDriver(mock_tsp_host, config)
    task = MagicMock(spec=GtAgentTask)
    with pytest.raises(RuntimeError, match="不再直接执行 run_chat_turn"):
        await driver.run_chat_turn(task=task, synced_count=0)

@pytest.mark.asyncio
async def test_tsp_driver_execute_tsp_tool_error_handling(mock_tsp_host):
    config = AgentDriverConfig(driver_type="tsp", options={})
    driver = TspAgentDriver(mock_tsp_host, config)
    driver._client = MagicMock()
    driver._client.tool = AsyncMock()
    
    # Case 1: JSON Decode Error
    ctx = ToolCallContext(agent_name="alice", team_id=1, chat_room=MagicMock(), tool_name="tool")
    res = await driver._execute_tsp_tool("invalid json", ctx)
    assert "JSON 解析失败" in res["message"]
    
    # Case 2: TSP Exception
    driver._client.tool.side_effect = TSPException("tsp/code", "tsp error")
    res = await driver._execute_tsp_tool("{}", ctx)
    assert res["code"] == "tsp/code"
    assert res["message"] == "tsp error"
    
    # Case 3: General Exception
    driver._client.tool.side_effect = RuntimeError("network fail")
    res = await driver._execute_tsp_tool("{}", ctx)
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

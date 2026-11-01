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

from constants import ToolCategory
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtAgentHistory import GtAgentHistory
from model.dbModel.gtScheculeTask import GtScheculeTask
from util import llmApiUtil
from service.agentService.agentHistoryStore import AgentHistoryStore
from service.agentService.driver.base import AgentDriverConfig
from service.agentService.driver.tspDriver import build_gtsp_command, TspAgentDriver
from service.agentService.toolRegistry import AgentToolRegistry
from service.roomService import ToolCallContext

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


def test_build_gtsp_command_uses_default_binary_and_workdir_flag() -> None:
    cmd = build_gtsp_command(None, workdir="/tmp/team-a")
    assert "assets/execute/gtsp" in cmd[0]
    assert "--mode" in cmd
    assert "stdio" in cmd
    assert "--workdir" in cmd
    assert "/tmp/team-a" in cmd
    assert "--workdir-root" not in cmd


def test_build_gtsp_command_respects_explicit_command_and_no_duplicate_flags() -> None:
    cmd = build_gtsp_command(
        ["./gtsp", "--mode", "stdio", "--workdir", "/custom/workdir"],
        workdir="/tmp/team-a",
    )
    assert cmd.count("--workdir") == 1
    assert "/custom/workdir" in cmd
    assert "--workdir-root" not in cmd



@dataclass
class _DummyHost:
    gt_agent: GtAgent = field(default_factory=lambda: GtAgent(id=1, team_id=1, name="实习生", role_template_id=1, model="mock-model@svc"))
    name: str = "实习生"
    team_name: str = "default"
    system_prompt: str = ""
    model: str = "mock-model"
    team_workdir: str = "/tmp"
    workspace_root: str = "/tmp/workspaces"
    current_db_task: GtScheculeTask | None = None
    _history: AgentHistoryStore = field(default_factory=lambda: AgentHistoryStore(agent_id=1))
    tool_registry: AgentToolRegistry = field(default_factory=AgentToolRegistry)


@pytest.mark.asyncio
async def test_tsp_driver_e2e_initialize_tool_shutdown() -> None:
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
            "command": [binary_path, "--mode", "stdio", "--access-root", "/"],
        },
    )
    driver = TspAgentDriver(host, config)

    try:
        await driver.startup()
    except OSError as e:
        pytest.skip(f"gtsp binary cannot be executed: {e}")
    try:
        # initialize 阶段：应加载出 gtsp 工具
        assert driver._tsp_tools

        # 1) 创建 /tmp 下测试目录
        mkdir_ctx = ToolCallContext(agent_id=1, team_id=1, chat_room=MagicMock(), tool_name="execute_bash")
        mkdir_result = await driver._execute_tsp_tool(
            json.dumps({"command": f"mkdir -p {tmp_dir}"}, ensure_ascii=False),
            mkdir_ctx,
        )
        assert isinstance(mkdir_result, dict)
        assert mkdir_result.get("exit_code") == 0

        # 2) 写文件
        write_ctx = ToolCallContext(agent_id=1, team_id=1, chat_room=MagicMock(), tool_name="write_file")
        write_result = await driver._execute_tsp_tool(
            json.dumps({"file_path": file_path, "content": expected_content}, ensure_ascii=False),
            write_ctx,
        )
        assert isinstance(write_result, dict)
        assert write_result.get("file_path") == file_path

        # 3) list 目录并确认文件存在
        list_ctx = ToolCallContext(agent_id=1, team_id=1, chat_room=MagicMock(), tool_name="list_dir")
        list_result = await driver._execute_tsp_tool(
            json.dumps({"dir_path": tmp_dir, "recursive": False}, ensure_ascii=False),
            list_ctx,
        )
        assert isinstance(list_result, dict)
        items = list_result.get("items", [])
        paths = {item.get("path") for item in items if isinstance(item, dict)}
        assert "hello.txt" in paths

        # 4) read 文件并校验内容一致
        read_ctx = ToolCallContext(agent_id=1, team_id=1, chat_room=MagicMock(), tool_name="read_file")
        read_result = await driver._execute_tsp_tool(
            json.dumps({"file_path": file_path}, ensure_ascii=False),
            read_ctx,
        )
        assert isinstance(read_result, dict)
        # v0.4.17 起 read_file 每行带行号前缀（如 "   1│..."）
        assert read_result.get("content") == "   1│hello from tsp e2e\n   2│line2\n"
    finally:
        # shutdown 阶段：应能优雅断连
        await driver.shutdown()
        shutil.rmtree(tmp_dir, ignore_errors=True)

    assert driver._client is None

@pytest.fixture
def mock_tsp_host() -> MagicMock:
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
async def test_tsp_driver_setup_registers_local_and_tsp_tools(mock_tsp_host: MagicMock) -> None:
    config = AgentDriverConfig(driver_type="tsp", options={})
    
    with patch("service.funcToolService.get_tools", return_value=[
        llmApiUtil.OpenAITool(function=llmApiUtil.OpenAIFunction(
            name="get_time",
            description="",
            parameters=llmApiUtil.OpenAIFunctionParameter(type="object", properties={}, required=[])
        )),
        llmApiUtil.OpenAITool(function=llmApiUtil.OpenAIFunction(
            name="get_dept_info",
            description="",
            parameters=llmApiUtil.OpenAIFunctionParameter(type="object", properties={}, required=[])
        )),
        llmApiUtil.OpenAITool(function=llmApiUtil.OpenAIFunction(
            name="get_room_info",
            description="",
            parameters=llmApiUtil.OpenAIFunctionParameter(type="object", properties={}, required=[])
        )),
        llmApiUtil.OpenAITool(function=llmApiUtil.OpenAIFunction(
            name="get_agent_info",
            description="",
            parameters=llmApiUtil.OpenAIFunctionParameter(type="object", properties={}, required=[])
        )),
        llmApiUtil.OpenAITool(function=llmApiUtil.OpenAIFunction(
            name="wake_up_agent",
            description="",
            parameters=llmApiUtil.OpenAIFunctionParameter(type="object", properties={}, required=[])
        )),
        llmApiUtil.OpenAITool(function=llmApiUtil.OpenAIFunction(
            name="send_chat_msg", 
            description="", 
            parameters=llmApiUtil.OpenAIFunctionParameter(type="object", properties={}, required=[])
        )),
        llmApiUtil.OpenAITool(function=llmApiUtil.OpenAIFunction(
            name="finish_action",
            description="",
            parameters=llmApiUtil.OpenAIFunctionParameter(type="object", properties={}, required=[])
        ))
    ]) as get_tools:
        driver = TspAgentDriver(mock_tsp_host, config)
        driver._client = MagicMock()
        driver._tsp_tools = {
            "list_dir": llmApiUtil.OpenAITool(function=llmApiUtil.OpenAIFunction(
                name="list_dir",
                description="",
                parameters=llmApiUtil.OpenAIFunctionParameter(type="object", properties={}, required=[])
            ), category=ToolCategory.READ)
        }

        driver._execute_tsp_tool = AsyncMock(return_value={"success": True})
        run_tool_call = AsyncMock(return_value={"success": True})
        with patch("service.funcToolService.run_tool_call", run_tool_call):
            driver._register_host_tools()
            context = ToolCallContext(
                agent_id=1,
                team_id=1,
                chat_room=MagicMock(),
            )
            finish_result = await mock_tsp_host.tool_registry.execute_tool_call(
                llmApiUtil.OpenAIToolCall(id="c1", function={"name": "finish_action", "arguments": "{}"}),
                context=context,
            )

        setup = driver.turn_setup
        assert setup.max_retries == 3

        exported_names = [tool.function.name for tool in mock_tsp_host.tool_registry.export_openai_tools()]
        assert exported_names == [
            "get_time",
            "get_dept_info",
            "get_room_info",
            "get_agent_info",
            "wake_up_agent",
            "send_chat_msg",
            "finish_action",
            "list_dir",
        ]
        get_tools.assert_called_once()

        run_tool_call.assert_called_once()
        called_args, called_context = run_tool_call.call_args.args
        assert called_args == "{}"
        assert called_context.agent_id == 1
        assert called_context.team_id == 1
        assert called_context.tool_name == "finish_action"
        assert finish_result.success is True

        tsp_result = await mock_tsp_host.tool_registry.execute_tool_call(
            llmApiUtil.OpenAIToolCall(id="c2", function={"name": "list_dir", "arguments": "{}"}),
            context=context,
        )
        driver._execute_tsp_tool.assert_called_once()
        tsp_called_args, tsp_called_context = driver._execute_tsp_tool.call_args.args
        assert tsp_called_args == "{}"
        assert tsp_called_context.tool_name == "list_dir"
        assert tsp_result.result["success"] is True

        unknown_result = await mock_tsp_host.tool_registry.execute_tool_call(
            llmApiUtil.OpenAIToolCall(id="c3", function={"name": "unknown", "arguments": "{}"}),
            context=context,
        )
        assert "未知工具" in str(unknown_result.result.get("message", ""))


@pytest.mark.asyncio
async def test_tsp_driver_respects_local_tool_names(mock_tsp_host: MagicMock) -> None:
    config = AgentDriverConfig(driver_type="tsp", options={"local_tool_names": ["send_chat_msg", "finish_action"]})

    with patch("service.funcToolService.get_tools", return_value=[
        llmApiUtil.OpenAITool(function=llmApiUtil.OpenAIFunction(
            name="get_time",
            description="",
            parameters=llmApiUtil.OpenAIFunctionParameter(type="object", properties={}, required=[])
        )),
        llmApiUtil.OpenAITool(function=llmApiUtil.OpenAIFunction(
            name="send_chat_msg",
            description="",
            parameters=llmApiUtil.OpenAIFunctionParameter(type="object", properties={}, required=[])
        )),
        llmApiUtil.OpenAITool(function=llmApiUtil.OpenAIFunction(
            name="finish_action",
            description="",
            parameters=llmApiUtil.OpenAIFunctionParameter(type="object", properties={}, required=[])
        )),
    ]) as get_tools:
        driver = TspAgentDriver(mock_tsp_host, config)

    get_tools.assert_called_once()
    assert [t.function.name for t in driver._local_tools] == ["get_time", "send_chat_msg", "finish_action"]

    driver._client = MagicMock()
    driver._tsp_tools = {}
    driver._register_host_tools()
    exported_names = [tool.function.name for tool in mock_tsp_host.tool_registry.export_openai_tools()]
    assert exported_names == ["send_chat_msg", "finish_action"]


@pytest.mark.asyncio
async def test_tsp_driver_run_task_turn_is_disabled(mock_tsp_host: MagicMock) -> None:
    config = AgentDriverConfig(driver_type="tsp", options={})
    driver = TspAgentDriver(mock_tsp_host, config)
    task = MagicMock(spec=GtScheculeTask)
    with pytest.raises(RuntimeError, match="不再直接执行 run_task_turn"):
        await driver.run_task_turn(task=task, synced_count=0)

@pytest.mark.asyncio
async def test_tsp_driver_execute_tsp_tool_error_handling(mock_tsp_host: MagicMock) -> None:
    config = AgentDriverConfig(driver_type="tsp", options={})
    driver = TspAgentDriver(mock_tsp_host, config)
    driver._client = MagicMock()
    driver._client.process = MagicMock()
    driver._client.process.returncode = None  # 模拟进程仍在运行
    driver._client.call_tool = AsyncMock()

    # Case 1: JSON Decode Error
    ctx = ToolCallContext(agent_id=1, team_id=1, chat_room=MagicMock(), tool_name="tool")
    res = await driver._execute_tsp_tool("invalid json", ctx)
    assert "JSON 解析失败" in res["message"]

    # Case 2: TSP Exception
    driver._client.call_tool.side_effect = TSPException("tsp/code", "tsp error")
    res = await driver._execute_tsp_tool("{}", ctx)
    assert res["code"] == "tsp/code"
    assert res["message"] == "tsp error"

    # Case 3: General Exception
    driver._client.call_tool.side_effect = RuntimeError("network fail")
    res = await driver._execute_tsp_tool("{}", ctx)
    assert "工具调用失败" in res["message"]


# 真实报文原文（alpine:3.20 与 gcr.io/distroless/base-debian12 实测逐字相同）
_MISSING_BASH_RAW = (
    'failed to start command: exec: "bash": executable file not found in $PATH'
)


@pytest.mark.asyncio
async def test_execute_bash_reports_missing_bash_with_reason_and_way_out(
    mock_tsp_host: MagicMock,
) -> None:
    """G1：无 bash 镜像下 `execute_bash` 必须给「明确原因 + 出路」（step1_product.md:45/:119）。

    同时钉住**两层语义分开**：
    - `code` 保留 TSP 协议层原文（既有契约，不许改）
    - `error_code` 是新增的业务层归纳
    """
    from service import containerService
    from service.agentService.driver.tspDriver import TspAgentDriver

    driver = TspAgentDriver(mock_tsp_host, AgentDriverConfig(driver_type="tsp", options={}))
    driver._client = MagicMock()
    driver._client.process = MagicMock()
    driver._client.process.returncode = None
    driver._client.call_tool = AsyncMock()
    driver._client.call_tool.side_effect = TSPException("server/internal_error", _MISSING_BASH_RAW)

    ctx = ToolCallContext(agent_id=1, team_id=1, chat_room=MagicMock(), tool_name="execute_bash")
    res = await driver._execute_tsp_tool('{"command": "echo hi"}', ctx)

    assert res["success"] is False
    # 协议层原文必须保留：改成业务码会丢排查线索，且破既有契约
    assert res["code"] == "server/internal_error"
    # 业务层归纳
    assert res["error_code"] == containerService.ERROR_CONTAINER_MISSING_BASH
    assert containerService.ERROR_CONTAINER_MISSING_BASH == "container_missing_bash"
    # 原因：必须点名 bash
    mess = str(res["message"]).lower()
    assert "bash" in mess, "原因必须点名「镜像里没有 bash」，否则模型会去查网络/权限"
    # 出路：换镜像 / 装 bash 二选一都要写出来
    for token in ("ubuntu:24.04", "debian:bookworm-slim", "apt-get install", "apk add"):
        assert token in mess, f"出路必须给出可执行动作，缺 {token!r}: {res['message']!r}"


@pytest.mark.asyncio
async def test_bash_command_not_found_is_not_reported_as_missing_bash(
    mock_tsp_host: MagicMock,
) -> None:
    """反向用例：**bash 在、命令不存在**绝不能报 `container_missing_bash`。

    ubuntu:24.04 实测返回的是成功调用、`exit_code=127`、stderr 为
    `bash: line 1: xxx: command not found`，压根不走 `TSPException` 分支；
    这条用例钉的是「就算报文进了异常分支也不许误报」——只匹配 `not found`
    这种宽串实现会让它变绿，那是用错误方式让测试变绿。
    """
    from service import containerService
    from service.agentService.driver.tspDriver import TspAgentDriver

    driver = TspAgentDriver(mock_tsp_host, AgentDriverConfig(driver_type="tsp", options={}))
    driver._client = MagicMock()
    driver._client.process = MagicMock()
    driver._client.process.returncode = None
    driver._client.call_tool = AsyncMock()
    driver._client.call_tool.side_effect = TSPException(
        "server/internal_error",
        "bash: line 1: this_command_does_not_exist_xyz: command not found",
    )

    ctx = ToolCallContext(agent_id=1, team_id=1, chat_room=MagicMock(), tool_name="execute_bash")
    res = await driver._execute_tsp_tool(
        '{"command": "this_command_does_not_exist_xyz"}', ctx
    )

    assert res["success"] is False
    assert "error_code" not in res, (
        f"「命令不存在」被误报成缺 bash 会让模型去换镜像，而真正该做的是改命令: {res!r}"
    )
    # 原文透出，不包装
    assert res["code"] == "server/internal_error"
    assert "command not found" in res["message"]


@pytest.mark.asyncio
async def test_bash_command_not_found_result_branch_keeps_gtsp_payload_verbatim(
    mock_tsp_host: MagicMock,
) -> None:
    """反向用例（**走 `return result.output` 的那条路**）：命令不存在不得被包装成缺 bash。

    为什么上面那条 `test_bash_command_not_found_is_not_reported_as_missing_bash`
    不够 —— 它把 `TSPException` 硬塞进 `except TSPException`（`:479`）分支，
    而真实 ubuntu:24.04 的「命令不存在」**根本不进那个分支**：gtsp 返
    `type:"result"`，`tspDriver.py:478` 直接 `return result.output`。

    也就是说「只匹配宽串 `not found`」这个变异在异常分支那层能被抓到，但
    **没有任何用例证明 `result` 分支不会被顺手加上包装**（比如有人把识别下沉到
    `_execute_tsp_tool` 的公共出口，或改 `ToolResult` 处理）。这条用例钉的就是那条路：
    识别逻辑一旦下沉出 `except TSPException`（含 `except Exception` 分支），
    `result` 分支返回值就不再等于 gtsp 原文，这条立刻红。

    报文是 ubuntu:24.04 实测原文（2026-11-01 真跑）：
        {"success": false, "stdout": "", "exit_code": 127,
         "stderr": "bash: line 1: this_command_does_not_exist_xyz: command not found\\n"}
    """
    from pytspclient import ToolResult

    from service.agentService.driver.tspDriver import TspAgentDriver

    gtsp_output = {
        "success": False,
        "stdout": "",
        "exit_code": 127,
        "stderr": "bash: line 1: this_command_does_not_exist_xyz: command not found\n",
    }

    driver = TspAgentDriver(mock_tsp_host, AgentDriverConfig(driver_type="tsp", options={}))
    driver._client = MagicMock()
    driver._client.process = MagicMock()
    driver._client.process.returncode = None
    driver._client.call_tool = AsyncMock(
        return_value=ToolResult(call_id="call-1", name="execute_bash", output=gtsp_output)
    )

    ctx = ToolCallContext(agent_id=1, team_id=1, chat_room=MagicMock(), tool_name="execute_bash")
    res = await driver._execute_tsp_tool(
        '{"command": "this_command_does_not_exist_xyz"}', ctx
    )

    # 原文逐字透出：既不加 `error_code`，也不改任何键
    assert res == gtsp_output, (
        "result 分支必须原样返回 gtsp 报文：任何包装都会让模型以为要换镜像，"
        f"而真正该做的是改命令。实际返回: {res!r}"
    )
    assert "error_code" not in res
    # 具名断言：换镜像的出路文案绝不许出现在这条路径上
    for token in ("ubuntu:24.04", "debian:bookworm-slim", "apt-get install", "apk add"):
        assert token not in json.dumps(res, ensure_ascii=False), (
            f"命令不存在却给出了「换镜像」的出路 {token!r}: {res!r}"
        )


@pytest.mark.asyncio
async def test_generic_exception_branch_never_carries_missing_bash(
    mock_tsp_host: MagicMock,
) -> None:
    """`except Exception` 分支不许被污染（裁定：识别逻辑只许写在 `except TSPException` 内）。

    上一层的两条用例都走 `except TSPException` / `return result.output`，
    覆盖不到 `except Exception`（`:498`）。而「把识别下沉到公共出口」的一个常见写法
    就是在最后那个兜底 `except` 里也加 `error_code`——它会让**任何** TSP 工具故障
    都被说成「缺 bash」，模型于是去换镜像，真正的 bug 被掩盖。

    这条钉两件事：兜底分支的返回结构仍只有 `success` + `message`；且不许出现
    换镜像的出路文案。
    """
    from service.agentService.driver.tspDriver import TspAgentDriver

    driver = TspAgentDriver(mock_tsp_host, AgentDriverConfig(driver_type="tsp", options={}))
    driver._client = MagicMock()
    driver._client.process = MagicMock()
    driver._client.process.returncode = None
    driver._client.call_tool = AsyncMock(side_effect=RuntimeError("network is unreachable"))

    ctx = ToolCallContext(agent_id=1, team_id=1, chat_room=MagicMock(), tool_name="execute_bash")
    res = await driver._execute_tsp_tool('{"command": "echo hi"}', ctx)

    assert res["success"] is False
    assert "error_code" not in res, (
        f"兜底 except 不许带业务错误码，否则任何 TSP 故障都会被说成缺 bash: {res!r}"
    )
    # 既有结构不变：只有 success + message
    assert set(res) == {"success", "message"}, f"兜底分支返回结构被改了: {res!r}"
    assert "工具调用失败" in res["message"]
    for token in ("ubuntu:24.04", "debian:bookworm-slim", "apt-get install", "apk add"):
        assert token not in res["message"], (
            f"通用 TSP 故障却给出了「换镜像」的出路 {token!r}: {res['message']!r}"
        )


@pytest.mark.asyncio
async def test_missing_bash_marker_only_applies_to_execute_bash(
    mock_tsp_host: MagicMock,
) -> None:
    """同一条报文，工具不是 `execute_bash` 时不许包装。

    文件类工具由 gtsp 内部实现，不经过 `bash -c`；把它们也报成「缺 bash」
    会把「换镜像」这个错误出路塞给模型。
    """
    from service.agentService.driver.tspDriver import TspAgentDriver

    driver = TspAgentDriver(mock_tsp_host, AgentDriverConfig(driver_type="tsp", options={}))
    driver._client = MagicMock()
    driver._client.process = MagicMock()
    driver._client.process.returncode = None
    driver._client.call_tool = AsyncMock()
    driver._client.call_tool.side_effect = TSPException("server/internal_error", _MISSING_BASH_RAW)

    ctx = ToolCallContext(agent_id=1, team_id=1, chat_room=MagicMock(), tool_name="read_file")
    res = await driver._execute_tsp_tool('{"file_path": "/workspace/self/a.txt"}', ctx)

    assert "error_code" not in res
    assert res["message"] == _MISSING_BASH_RAW


@pytest.mark.asyncio
async def test_missing_bash_message_is_not_leaked_to_unrelated_tsp_errors(
    mock_tsp_host: MagicMock,
) -> None:
    """窄匹配护栏：只含 `executable file not found` 但不含 `exec: "bash":` 的报文不认。"""
    from service.agentService.driver.tspDriver import TspAgentDriver

    driver = TspAgentDriver(mock_tsp_host, AgentDriverConfig(driver_type="tsp", options={}))
    driver._client = MagicMock()
    driver._client.process = MagicMock()
    driver._client.process.returncode = None
    driver._client.call_tool = AsyncMock()
    driver._client.call_tool.side_effect = TSPException(
        "server/internal_error", "exec: node: executable file not found in $PATH"
    )

    ctx = ToolCallContext(agent_id=1, team_id=1, chat_room=MagicMock(), tool_name="execute_bash")
    res = await driver._execute_tsp_tool('{"command": "node -v"}', ctx)

    assert "error_code" not in res, (
        f"匹配串必须钉住 bash 本身，否则别的命令缺失会被误报成缺 bash: {res!r}"
    )
    assert "exec: node" in res["message"]


@pytest.mark.asyncio
async def test_tsp_client_fail_pending() -> None:
    client = TSPClient.from_stdio("mock")

    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    client.in_flight["req1"] = fut

    client._fail_pending(RuntimeError("closed"))

    with pytest.raises(RuntimeError, match="closed"):
        await fut
    assert len(client.in_flight) == 0

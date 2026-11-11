"""#195 的 TSP 路径判据：远端那条路拿到的 schema 也必须是新的。

## 为什么单独立一条（这是本单 §4.7.5 的硬要求）

`toolArgsValidation` 有**两个** schema 来源：
- 本地路径：`get_tool_parameters_schema(name, func)` —— 走 `get_function_metadata`；
- 远端路径：`validate_tool_args(name, args, schema=...)` —— schema 由调用方给
  （`tspDriver._execute_tsp_tool` 从 `_tsp_tools[name].function.parameters` 取）。

只验本地路径，可能出现「本地用的是新生成器、远端那条分支还在用旧形态」的分裂实现，
而**两边测试都不会红**。所以这里两段都要钉：

1. TSP driver 暴露给模型/对端的本地工具 schema 来自新生成器（`_local_tools` 由
   `funcToolService.get_tools(include_container_tools=True)` 构造，其 `parameters`
   就是下发给模型的那份）；
2. `schema=<外部传入>` 这条**真实存在的分支**（远端路径）对新 schema 的校验行为
   与本地一致：19 个字段拒 null、`Optional` 字段放行 null。
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from service.funcToolService import core as funcCore
from service.funcToolService import toolArgsValidation
from service.roomService import ToolCallContext


def _ctx(tool_name: str) -> ToolCallContext:
    return ToolCallContext(agent_id=1, team_id=1, tool_name=tool_name)


def _rebuild_driver():
    """构造一个 TspAgentDriver（client 用 MagicMock，进程视为存活）。"""

    from service.agentService.driver.base import AgentDriverConfig
    from service.agentService.driver.tspDriver import TspAgentDriver

    host = MagicMock()
    host.gt_agent = MagicMock(id=1)
    host.tool_registry = MagicMock()
    driver = TspAgentDriver(host, AgentDriverConfig(driver_type="tsp", options={}))
    driver._client = MagicMock()
    driver._client.process = MagicMock()
    driver._client.process.returncode = None
    driver._client.call_tool = AsyncMock(return_value=MagicMock(output={"success": True}))
    return driver


@pytest.fixture(autouse=True)
def _loaded_tools():
    funcCore.load_func_tools()
    funcCore.load_container_func_tools()
    toolArgsValidation.clear_schema_cache()
    yield
    toolArgsValidation.clear_schema_cache()


def test_tsp_driver_local_tools_carry_new_schema() -> None:
    """TSP driver 拿到的本地工具 schema 必须是新形态（nullability 已在 schema 里）。

    `_local_tools` 就是 `_register_host_tools()` 注册给模型的那批工具；它们的
    `parameters.model_dump()` 与校验层用的是同一份结构，所以这里直接断言形态：
    - `get_time.timezone`（`Optional[str]`）⇒ `type` 含 `"null"`；
    - `create_task.priority`（`str`，有默认值）⇒ `type` 恰为 `"string"`，**不含** null。
    若将来有人给 TSP 路径塞回一份旧形态 schema，这条立刻红。
    """

    driver = _rebuild_driver()
    by_name = {tool.function.name: tool for tool in driver._local_tools}

    assert "get_time" in by_name and "create_task" in by_name

    timezone = by_name["get_time"].function.parameters.model_dump()["properties"]["timezone"]
    assert timezone["type"] == ["string", "null"], timezone

    priority = by_name["create_task"].function.parameters.model_dump()["properties"]["priority"]
    assert priority["type"] == "string", priority


@pytest.mark.asyncio
async def test_tsp_validate_tool_args_schema_branch_rejects_and_accepts() -> None:
    """`validate_tool_args(schema=...)` 这条**远端专用**分支：19 拒 null / Optional 放行。

    远端工具的 schema 由对端给出，`tspDriver` 把它原样传给 `validate_tool_args`。
    这条判据把「远端那条路径」单独钉一遍：既证明它能拦住「注解不含 None 的字段传 null」
    （与本地一致），也证明它**不**会误杀 Optional 的 null。
    """

    driver = _rebuild_driver()
    by_name = {tool.function.name: tool for tool in driver._local_tools}

    # 远端路径的真实入口形态：schema 是从 OpenAITool 上取出来的一份 dict。
    create_task_schema = by_name["create_task"].function.parameters.model_dump()

    # ① 注解非 Optional（有默认值）⇒ null 必拒
    rejected = toolArgsValidation.validate_tool_args(
        "create_task", {"title": "t", "assignee_id": 38, "description": None},
        schema=create_task_schema,
    )
    assert rejected is not None, "远端路径未拦住 create_task.description=null"
    assert rejected.get("error_code") == toolArgsValidation.INVALID_TOOL_ARGS
    assert "description" in rejected["message"], rejected

    # ② 注解是 Optional ⇒ null 必放行（防远端路径「一律拒 null」）
    accepted = toolArgsValidation.validate_tool_args(
        "create_task", {"title": "t", "assignee_id": 38, "depends_on": None},
        schema=create_task_schema,
    )
    assert accepted is None, accepted


@pytest.mark.asyncio
async def test_tsp_execute_rejects_null_before_crossing_process_boundary() -> None:
    """端到端：TSP 远端工具喂 `{"depth": null}`（注解 int、有默认值）⇒ 不进 `call_tool`。

    这条与 #189 的 H2 判据同形（非法参数不得越过进程边界），但用了本单的缺陷形态：
    改造前 `_is_ignorable_null` 会把「非必填字段整体 null」一律豁免，这个 schema 在旧
    实现下会**放行**并真的调用对端。
    """

    from util import llmApiUtil

    schema = {
        "type": "object",
        "properties": {"depth": {"type": "integer", "description": "深度"}},
        "required": [],
    }
    tool = llmApiUtil.OpenAITool(
        function=llmApiUtil.OpenAIFunction(
            name="remote_depth",
            description="remote depth",
            parameters=llmApiUtil.OpenAIFunctionParameter(
                type="object", properties={}, required=[]
            ),
        )
    )
    tool.function.parameters = schema  # type: ignore[assignment]

    driver = _rebuild_driver()
    driver._tsp_tools = {"remote_depth": tool}

    result = await driver._execute_tsp_tool(json.dumps({"depth": None}), _ctx("remote_depth"))

    assert result.get("error_code") == toolArgsValidation.INVALID_TOOL_ARGS, result
    driver._client.call_tool.assert_not_called()

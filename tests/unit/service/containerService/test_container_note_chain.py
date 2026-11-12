"""`container note` 的**整链**判据（#203 交付标准 1）：console → 注册表第 2 跳 →
工具 → service → DB → `list_containers` 读回新值。

为什么单写一个文件、而不是并进 `test_container_note_console.py`：

- 那个文件用替身 handler 只证「可达」，不碰 DB（快、无副作用）；
- 这里要真库（跑完整迁移的临时 sqlite）+ 真 service 编排，才能证明「改完之后
  `list_containers` 里看到的是新说明」。用替身 handler 证不了这件事 —— 替身返回的是
  我写的 `{"message": "reached"}`。

唯一的替身是 `_container_agent`（`agentService` 未在本用例里启动）。它只提供
`agent_id` 与执行环境状态，不参与 note 的读写路径，所以链路上「console → note 落库 →
列表回显」这一段是真的。
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from service.agentService import toolRegistry
from service.funcToolService import core as funcCore
from service.funcToolService import tools as tools_module
from service.roomService import ToolCallContext
from util import llmApiUtil

# 复用容器服务的「真库 + 假 docker」夹具（跑完整迁移链到最新）
from .test_container_core import env as container_env  # noqa: F401

AGENT_ID = 1


@pytest.fixture
def fake(container_env):  # noqa: F811
    return container_env


@pytest.fixture
def ctx_agent(monkeypatch):
    """把 `_container_agent` 换成假 Agent（只提供归属 id 与执行环境；不参与 note 读写）。

    `get_exec_env_state()` 必须返回**真的** `ExecEnvState`：`list_containers` 要把它的
    `exec_env` 序列化进返回体，而裸 `MagicMock` 在 `jsonUtil` 里会无限递归
    （实测报「maximum recursion depth exceeded」）。
    """
    from constants import ExecEnv
    from model.coreModel import ExecEnvState

    agent = MagicMock()
    agent.gt_agent.id = AGENT_ID
    agent.get_exec_env_state.return_value = ExecEnvState(exec_env=ExecEnv.HOST)
    monkeypatch.setattr(tools_module, "_container_agent", lambda _ctx: agent)
    return agent


def _ctx(tool_name: str) -> ToolCallContext:
    return ToolCallContext(agent_id=AGENT_ID, team_id=1, tool_name=tool_name)


def _registry() -> toolRegistry.AgentToolRegistry:
    funcCore.load_func_tools()
    funcCore.load_container_func_tools()
    registry = toolRegistry.AgentToolRegistry()
    for tool in funcCore.get_all_tools(include_container_tools=True):
        registry.register(tool, funcCore.run_tool_call)
    return registry


async def _run(registry: toolRegistry.AgentToolRegistry, name: str, args: dict) -> dict:
    result = await registry.execute_tool_call(
        llmApiUtil.OpenAIToolCall(
            id=f"tc_{name}",
            function={"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
        ),
        context=_ctx(name),
    )
    return result.result


async def test_console_note_updates_and_list_containers_reflects_it(fake, ctx_agent):
    """`container note <id> <text>` 改成 → `list_containers` 里就是新值。

    这条把「命令原文」和「列表回显」串成一件事：只测前者会漏掉「改了但读不出来」，
    只测后者无法区分「是 console 改的」还是「别的地方改的」。
    """
    from service import containerService

    record = await containerService.create_container(AGENT_ID, note="原始说明")
    registry = _registry()

    # 1) console 侧改
    changed = await _run(
        registry, "console", {"command": f'container note {record.container_id} "跑前端构建"'}
    )
    assert changed.get("success") is True, changed
    assert changed.get("note") == "跑前端构建", changed

    # 2) 列表侧读（走同一个注册表、同一个工具）
    listed = await _run(registry, "list_containers", {})
    assert listed.get("success") is True, listed
    notes = {item["container_id"]: item["note"] for item in listed["containers"]}
    assert notes[record.container_id] == "跑前端构建", listed["containers"]


async def test_console_note_empty_string_clears_and_list_shows_null(fake, ctx_agent):
    """`container note <id> ""` 清空 → 列表里是 `null`（不是空串）。"""
    from service import containerService

    record = await containerService.create_container(AGENT_ID, note="要清掉的")
    registry = _registry()

    changed = await _run(
        registry, "console", {"command": f'container note {record.container_id} ""'}
    )
    assert changed.get("success") is True, changed
    assert changed.get("note") is None, changed

    listed = await _run(registry, "list_containers", {})
    notes = {item["container_id"]: item["note"] for item in listed["containers"]}
    assert notes[record.container_id] is None, listed["containers"]


async def test_console_note_over_limit_reports_error_and_keeps_old_value(fake, ctx_agent):
    """超长走 console 时报 `container_note_too_long`，且旧说明保持不变。"""
    from service import containerService

    record = await containerService.create_container(AGENT_ID, note="原始说明")
    registry = _registry()

    too_long = "a" * 201
    result = await _run(
        registry, "console", {"command": f'container note {record.container_id} "{too_long}"'}
    )

    assert result.get("success") is False, result
    assert result.get("error_code") == "container_note_too_long", result

    listed = await _run(registry, "list_containers", {})
    notes = {item["container_id"]: item["note"] for item in listed["containers"]}
    assert notes[record.container_id] == "原始说明", listed["containers"]


async def test_direct_model_call_does_not_change_note(fake, ctx_agent):
    """反面对照：模型直调（hop 0）被拒，**且库里一个字节都没变**。

    只断言「返回失败」不够：如果拒绝发生在 service 调用**之后**（先写后拒），
    说明也会被改掉。所以这里必须回读 DB。
    """
    from service import containerService
    from service.containerService import core as container_core

    record = await containerService.create_container(AGENT_ID, note="原始说明")
    registry = _registry()

    result = await _run(
        registry,
        "update_container_note",
        {"container_id": record.container_id, "note": "模型偷偷改的"},
    )

    assert result.get("success") is False, result
    assert "请通过 console 命令使用" in result.get("message", ""), result

    reloaded = await container_core.gtAgentContainerManager.get_active_by_id(record.id)
    assert reloaded is not None
    assert reloaded.note == "原始说明", "直调被拒了但说明还是被改了 —— 拒绝点太晚"

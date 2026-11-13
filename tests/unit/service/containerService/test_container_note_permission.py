"""`container note` 的**权限层**判据（#209 交付标准 1、2）。

## 为什么单写一个文件（而不是并进已有的两个）

`#203` 已有 37 条判据全绿，却漏掉了 F1（普通成员经 console 第 2 跳能改容器 note）。
根因不在断言写得松，而在**结构上不可能被发现**：

- `test_container_note_console.py:43` 的 `_registry()` 与
  `test_container_note_chain.py:62` 的 `_registry()` 都**从不调用
  `apply_tool_allow_specs`**。注册表刚 `register` 完时每个工具都是 `enabled=True`，
  等价于「root 全权」口径。于是类目是 `EXECUTE` 还是 `ADMIN` 对现有用例毫无影响 ——
  改类目在那些用例里**完全不生效**，它们自然永远绿。
- 已有的两条 console 用例只证「可达」与「改完能读回」，没有一条站在**普通成员**口径上。

所以本文件只做一件事：显式构造**生产口径的权限面**再跑同一条调用链。

## 口径怎么来的（不手写 spec 列表）

用 `build_runtime_allow_specs(None, is_root_leader=...)` 生成，与
`nativeDriver.py:34` / `tspDriver.py:502` / `claudeSdkDriver.py:109` 三处生产调用点
同一个函数。手写 `["Category:Basic", ...]` 会让判据与生产口径脱钩：生产改了默认
类别集合，这里还会绿。

## 四条断言为什么必须同断（任缺一条都能假绿）

| 断言 | 少了它会怎样 |
|---|---|
| `message` 含「工具无权限使用: update_container_note」 | 只看 `success is False` ⇒ 传个不存在的 id 也失败，**恒绿** |
| `list_enabled_tool_names()` 不含它 | 只断文案 ⇒ 拒绝可能来自可见性或别处，类目没真参与计算也绿 |
| DB 回读 note 未变 | 只断文案 ⇒ 先写后拒（拒绝点太晚）也绿 |
| root leader 成功且 DB 真变 | 只验堵住 ⇒ 把功能废掉（全拒）同样绿 |

第四条特别容易被省：`#203` 的 `test_container_note_chain.py` 就是在 root 口径下验
「改了能读回」，如果只看「能不能改」，把类目改成 `ADMIN` 之后它仍然绿 —— 但那样
普通成员与 root 都改不了，功能等于废了。所以「堵住」与「还能用」必须成对出现。
"""

from __future__ import annotations

import json

import pytest

from service.agentService import toolRegistry
from service.funcToolService import core as funcCore
from service.funcToolService import tools as tools_module
from service.roomService import ToolCallContext
from util import llmApiUtil

# 复用容器服务的「真库（完整迁移）+ 假 docker」夹具
from .test_container_core import env as container_env  # noqa: F401

AGENT_ID = 1
NOTE_TOOL = "update_container_note"

#: 权限拒绝的文案片段。第 2 跳的 `enabled` 判定在 `execute_tool_call` 里先于可见性，
#: 所以被类目挡住时走的是这一句，而不是 hop 0 那句「请通过 console 命令使用」。
PERMISSION_DENIED = f"工具无权限使用: {NOTE_TOOL}"

#: 记录不存在时的文案片段。与上面**必须不同**，否则「权限拒」与「查不到」无法区分，
#: 断言 `PERMISSION_DENIED` 就退化成「要一句失败文案」。
NOT_FOUND = "容器记录不存在"


@pytest.fixture
def fake(container_env):  # noqa: F811
    return container_env


@pytest.fixture
def ctx_agent(monkeypatch):
    """假 Agent：只提供归属 id 与执行环境，不参与 note 读写路径（与 chain 同款）。"""
    from constants import ExecEnv
    from model.coreModel import ExecEnvState
    from unittest.mock import MagicMock

    agent = MagicMock()
    agent.gt_agent.id = AGENT_ID
    agent.get_exec_env_state.return_value = ExecEnvState(exec_env=ExecEnv.HOST)
    monkeypatch.setattr(tools_module, "_container_agent", lambda _ctx: agent)
    return agent


def _build_registry() -> toolRegistry.AgentToolRegistry:
    """注册表 + 全量容器工具，但**不**套权限面（与生产 driver 的注册段一致）。"""
    funcCore.load_func_tools()
    funcCore.load_container_func_tools()
    registry = toolRegistry.AgentToolRegistry()
    for tool in funcCore.get_all_tools(include_container_tools=True):
        registry.register(tool, funcCore.run_tool_call)
    return registry


def _registry_for(is_root_leader: bool) -> toolRegistry.AgentToolRegistry:
    """生产口径的注册表：注册完把运行时权限面按角色套上去。

    `allowed_tools=None` 正是「Agent 没配 `allow_tools`」的默认分支
    （`agentService/core.py:192` 只在配了的时候写 `tool_allow_specs`）。
    """
    registry = _build_registry()
    specs = toolRegistry.build_runtime_allow_specs(None, is_root_leader=is_root_leader)
    registry.apply_tool_allow_specs(specs)
    return registry


def _ctx(tool_name: str) -> ToolCallContext:
    return ToolCallContext(agent_id=AGENT_ID, team_id=1, tool_name=tool_name)


async def _run(registry: toolRegistry.AgentToolRegistry, name: str, args: dict) -> dict:
    result = await registry.execute_tool_call(
        llmApiUtil.OpenAIToolCall(
            id=f"tc_{name}",
            function={"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
        ),
        context=_ctx(name),
    )
    return result.result


async def _note_in_db(record_id: int) -> str | None:
    """从 DB 回读 note —— 判据必须落在存储上，不能只看返回体。"""
    from service.containerService import core as container_core

    reloaded = await container_core.gtAgentContainerManager.get_active_by_id(record_id)
    assert reloaded is not None, f"记录 {record_id} 消失了"
    return reloaded.note


# ---------------------------------------------------------------------------
# 1. 核心：普通成员经 console 第 2 跳改不动（F1 本身）
# ---------------------------------------------------------------------------


async def test_normal_member_cannot_change_note_via_console(fake, ctx_agent):
    """普通成员口径 `console → container note` 被权限挡住，**且库一个字节没变**。

    这条是本单的主判据。第 3 条断言（DB 回读）不是冗余：如果拒绝发生在
    `containerService.update_container_note` **之后**（先写再拒），返回体同样是失败，
    说明却已经被改掉了 —— `#203` 的 `test_direct_model_call_does_not_change_note`
    对 hop 0 已经吃过这个教训，这里对 hop 1 同理。
    """
    from service import containerService

    record = await containerService.create_container(AGENT_ID, note="原始说明")
    registry = _registry_for(is_root_leader=False)

    result = await _run(
        registry, "console", {"command": f'container note {record.container_id} "模型改的"'}
    )

    assert result.get("success") is False, result
    assert PERMISSION_DENIED in result.get("message", ""), result
    # DB 回读：文案对了但写进去了，同样是漏洞
    assert await _note_in_db(record.id) == "原始说明", "返回失败但说明被改了 —— 拒绝点太晚"


async def test_normal_member_registry_excludes_it_by_category(fake, ctx_agent):
    """类目真的参与了 `enabled` 计算 —— 堵在权限层，不是堵在别处。

    `list_exposed_tool_names()` 在 `#203` 就已经不含它了（`CONSOLE_ONLY` 只影响暴露面）。
    所以只断暴露面的话，**改之前也是绿的**，这条判据必须落在 `enabled` 面。
    """
    registry = _registry_for(is_root_leader=False)

    enabled = registry.list_enabled_tool_names()
    assert NOTE_TOOL not in enabled, "普通成员的 enabled 面仍含它 —— 类目没生效"
    assert NOTE_TOOL not in registry.list_exposed_tool_names()

    # 反向对照：`console` 本身必须还在（BASIC）。否则「console 被一起禁掉」
    # 也会让上一条用例的断言通过，而那只是把入口关了、没修类目。
    assert "console" in enabled, "console 入口被误禁 —— 那不是修权限，是关功能"
    # 另一个容器工具仍在（同一批注册、同为容器组），证明不是整组被禁
    assert "list_containers" in enabled


async def test_root_leader_can_still_change_note_via_console(fake, ctx_agent):
    """同一套调用换成 root leader ⇒ 成功，且 DB **真的**变了。

    与第 1 条成对：只验「堵住」不验「还能用」，等于用废掉功能换取漏洞消失。
    """
    from service import containerService

    record = await containerService.create_container(AGENT_ID, note="原始说明")
    registry = _registry_for(is_root_leader=True)

    result = await _run(
        registry, "console", {"command": f'container note {record.container_id} "人改的"'}
    )

    assert result.get("success") is True, result
    assert result.get("note") == "人改的", result
    assert await _note_in_db(record.id) == "人改的", "返回成功但库里没变 —— 空成功"


# ---------------------------------------------------------------------------
# 2. 判别力自证：拒绝层级可区分（防「传个假 id 也能绿」）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("is_root_leader", "expected_fragment"),
    [
        (False, PERMISSION_DENIED),
        (True, NOT_FOUND),
    ],
)
async def test_rejection_layer_is_distinguishable(
    fake, ctx_agent, is_root_leader, expected_fragment
):
    """同一个**不存在的** container_id，两种口径给出**不同的**拒绝理由。

    这一格是主判据的判别力来源：

    - 普通成员：权限面先挡 ⇒ `工具无权限使用`（`enabled` 判定在 handler 之前）
    - root leader：权限过了才去查库 ⇒ `容器记录不存在`

    如果两条 message 相同，主判据里的 `PERMISSION_DENIED` 断言就退化成
    「反正失败了就行」；反过来，这也证明了主判据在「真实 id」上不会因为
    「记录不存在」这种偶然原因而绿。
    """
    registry = _registry_for(is_root_leader=is_root_leader)

    # 12 位十六进制，形态合法但库里没有
    result = await _run(
        registry, "console", {"command": 'container note ffffffffffff "x"'}
    )

    assert result.get("success") is False, result
    message = result.get("message", "")
    assert expected_fragment in message, message

    other = NOT_FOUND if expected_fragment == PERMISSION_DENIED else PERMISSION_DENIED
    assert other not in message, f"两种拒绝理由串了: {message}"

"""P0 收敛过程中翻出来的 5 处实现缺陷的**回归钉**（每处一条能咬住它的判据）。

背景：P0 主体 commit 之后，全量测试有 85 条红。其中 5 条**归不进设计 §6 的 a–f 有意变化**，
是实打实的回归/实现偏离（改的是实现，不是期望值）：

| # | 缺陷形态 | 后果 | 本文件的判据 | 另有集成侧判据 |
|---|---|---|---|---|
| 1 | `core._container_func_tools` 只在 `load_*()` 里赋值、没有模块级定义 | 新进程里直接 `get_func_tool` / `_reset` ⇒ `NameError`（启动失败回滚路径最先踩） | `test_container_globals_exist_before_any_load` | `test_console_tool_align_p1::TestP1d…no_naked_db_constraint_error` |
| 2 | `create_dept` 的同名检查用了未导入的 `gtDeptManager` | 建部门 ⇒ `NameError` | —（需 DB） | `test_tool_functions::TestCreateDeptParentName`（5 条） |
| 3 | `create_agent` 的「同名已存在」判定抢在保留名判定之前 | 建 `OPERATOR` 报「已存在，请用 update_agent」——把「永远不许碰」说成「换个接口再来」 | `test_create_agent_reserved_name_wins_over_duplicate` | `test_role_template_tools::test_create_agent_rejects_invalid_inputs` |
| 4 | `_is_blank_update_value` 只认 `None/[]/""`，纯空白串算「给了」 | `dept update <id> '{"responsibility":"   "}'` 把职责改成空格（旧 console 层 `_is_blank` 的保护在纯转发后丢了） | `test_blank_value_matrix` | `test_console_tool::test_cc6_blank_config_counts_as_no_update_action` |
| 5 | console 收敛为纯转发后 `update_task` 没有 `no_update_action` | 空调用退化成服务层 `empty_note`（文案讲「追加评论要填 note」，与「什么都没给」不符） | —（需服务层） | `test_console_task::test_empty_update_is_explicitly_rejected` / `test_empty_note_only_is_rejected` |

每一条都做过「把修复回退成缺陷形态 ⇒ 判据变红」的实跑（输出见 #315 报告 §9）。
"""

from __future__ import annotations

import subprocess
import sys
from unittest.mock import AsyncMock

import pytest

from dal.db import gtAgentManager
from model.dbModel.gtAgent import GtAgent
from service.funcToolService import core as func_core
from service.funcToolService import tools
from service.roomService import ToolCallContext

TEAM_ID = 1


def _ctx(tool_name: str = "t") -> ToolCallContext:
    return ToolCallContext(agent_id=1, team_id=TEAM_ID, tool_name=tool_name)


# ---------------------------------------------------------------------------
# 缺陷 1：容器工具组的模块级容器必须存在（不能只在 `load_*` 里赋值）
# ---------------------------------------------------------------------------

def test_container_globals_exist_before_any_load():
    """`core` 的两个工具容器必须是**模块级定义**的空 dict，而不是「第一次 load 才有」。

    为什么用子进程：本进程里 `load_func_tools()` 早被别的用例调过，两个全局必然已存在——
    在本进程断言等于什么都没测。缺陷形态是「**新进程**、**还没 startup** 就访问」，
    只有真开一个新解释器才复现得了（P0 期间就是这么红的：`get_func_tool` 抛 NameError）。
    """
    code = (
        "from service.funcToolService import core;"
        "print('container:', core.get_func_tool('__nope__'));"
        "core._reset();"
        "print('reset ok')"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, (
        "新进程里未 startup 就访问工具容器崩了（模块级定义缺失？）：\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    assert "container: None" in proc.stdout, proc.stdout
    assert "reset ok" in proc.stdout, proc.stdout


def test_reset_is_idempotent_after_load():
    """`_reset()` 在「load 之前 / 之后」都能跑（启动失败回滚会走它）。"""
    func_core.load_func_tools()
    func_core._reset()
    func_core._reset()
    assert func_core.get_func_tool("console") is None


def test_container_tool_names_derived_from_spec_group():
    """容器组名由 `ToolSpec.group` 派生，不再有手写的 `CONTAINER_TOOL_NAMES` 常量可漂移。"""
    assert not hasattr(func_core, "CONTAINER_TOOL_NAMES"), "手写常量不该回来（会被新工具漏登记）"
    names = func_core.container_tool_names()
    assert len(names) == 6, names
    func_core.load_container_func_tools()
    for name in names:
        assert func_core.get_func_tool(name) is not None, name


# ---------------------------------------------------------------------------
# 缺陷 3：保留成员（team_id == -1）不算「同名冲突」
# ---------------------------------------------------------------------------

def _reserved_operator() -> GtAgent:
    """系统保留成员行：`team_id == -1`（`SpecialAgent.OPERATOR.value`）。"""
    return GtAgent(id=900, team_id=-1, name="OPERATOR", role_template_id=21)


@pytest.mark.asyncio
async def test_create_agent_reserved_name_wins_over_duplicate(monkeypatch):
    """建 `OPERATOR` 时报「保留成员」，不能报「已存在，请用 update_agent」。

    缺陷形态：`create_agent` 的同名预检查把保留行也算作「同名」，于是文案变成
    「请用 update_agent」——而 `update_agent` 同样会被保留名判定拒绝，模型照提示做只会
    再撞一次墙。判据钉的是**文案指向**（能不能照做），不只是「成功了没有」。
    """
    monkeypatch.setattr(
        gtAgentManager, "get_team_agents_by_names", AsyncMock(return_value=[_reserved_operator()])
    )
    # 不 stub `_upsert_agent`：保留名判定就在它内部，而且发生在**任何 DB 访问之前**，
    # 所以这条用例不需要 DB。stub 掉它等于把要测的东西换掉了（实测踩过：桩一律 return
    # success，判据直接假绿）。
    result = await tools.create_agent(
        name="OPERATOR", role_template_name="writer", _context=_ctx()
    )

    assert result["success"] is False, result
    assert "保留成员" in result["message"], result["message"]
    assert "update_agent" not in result["message"], (
        f"保留名被说成「换个接口再来」，模型照做会再撞墙：{result['message']}"
    )


@pytest.mark.asyncio
async def test_create_agent_real_duplicate_still_rejected(monkeypatch):
    """对照：真·同名（在职行）仍报「已存在 → update_agent」，不能被上面那条改宽。"""
    monkeypatch.setattr(
        gtAgentManager,
        "get_team_agents_by_names",
        AsyncMock(return_value=[GtAgent(id=11, team_id=TEAM_ID, name="alice", role_template_id=21)]),
    )
    upsert = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(tools, "_upsert_agent", upsert)

    result = await tools.create_agent(name="alice", role_template_name="writer", _context=_ctx())

    assert result["success"] is False, result
    assert result["error_code"] == "invalid_tool_args", result
    assert "update_agent" in result["message"], result["message"]
    upsert.assert_not_awaited()


# ---------------------------------------------------------------------------
# 缺陷 4：纯空白串算「本次没给」
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "value,expected",
    [
        (None, True),
        ("", True),
        ("   ", True),
        ("\n\t ", True),
        ([], True),
        ("x", False),
        (" x ", False),
        ([0], False),
        (0, False),
        (False, False),
        ({"k": "v"}, False),
    ],
)
def test_blank_value_matrix(value, expected):
    """`_is_blank_update_value`：`None`/空串/**纯空白**/空列表 算「没给」，其余都算给了。

    `0` 与 `False` 必须算「给了」——它们是有效值（`max_rounds=0`、布尔 false），
    被误判成「没给」会让合法调用变成 `no_update_action`。
    """
    assert tools._is_blank_update_value(value) is expected


@pytest.mark.asyncio
async def test_update_dept_whitespace_only_is_no_update_action(monkeypatch):
    """`update_dept(responsibility="   ")` ⇒ `no_update_action`，且**不碰库**。

    缺陷形态：空白串被当成有效变更，`_upsert_dept` 真的把职责改成空格。
    判据同时钉两件事：错误码，以及「没有任何写入发生」（只看错误码挡不住「先写后报错」）。
    """
    upsert = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(tools, "_upsert_dept", upsert)

    result = await tools.update_dept(dept_id=7, responsibility="   ", _context=_ctx())

    assert result["success"] is False, result
    assert result["error_code"] == "no_update_action", result
    upsert.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_agent_whitespace_only_is_no_update_action(monkeypatch):
    """同一口径对 `update_agent` 生效（三个 update_* 共用同一个判定函数）。"""
    upsert = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(tools, "_upsert_agent", upsert)

    result = await tools.update_agent(agent_id=11, model="  ", _context=_ctx())

    assert result["success"] is False, result
    assert result["error_code"] == "no_update_action", result
    upsert.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_room_whitespace_only_is_no_update_action(monkeypatch):
    """同一口径对 `update_room` 生效。"""
    upsert = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(tools, "_upsert_room", upsert)

    result = await tools.update_room(room_id=7, initial_topic="  ", _context=_ctx())

    assert result["success"] is False, result
    assert result["error_code"] == "no_update_action", result
    upsert.assert_not_awaited()

"""#376 B4 判据（第一步）：`run_tool_call()` 出口兜底——失败体必须有 `error_code`。

范围只有一件事：**出口补通用码 `operation_failed`，且不覆盖已有码**。
70 处手写字典的逐处收敛是同一个任务的后续批次，不在本文件里断言。

为什么要在**纯函数 + 真入口**两层各钉一遍：
- 纯函数层（`ensure_error_code`）能精确表达"什么该补、什么不该补"，一条分支一条断言；
- 真入口层（`run_tool_call`）才能证明它**真的接在出口上**——纯函数写得再对，
  没被调用也一样是被 bug 保护不到的代码。真入口层还额外钉了三条**不覆盖**的实证：
  坏 JSON（`invalid_json_args`）、参数不符（`invalid_tool_args`）、业务异常码
  都不能被兜底抹平成 `operation_failed`（否则模型侧按码自纠的能力反而退化）。
"""

from __future__ import annotations

import json

import pytest

from constants import ToolVisibility
from service.funcToolService import core as funcCore
from service.funcToolService.funcToolType import FuncTool
from service.roomService import ToolCallContext
from toolResult import OPERATION_FAILED, ensure_error_code


def _ctx(tool_name: str = "t", agent_id: int = 1, team_id: int = 1) -> ToolCallContext:
    return ToolCallContext(agent_id=agent_id, team_id=team_id, tool_name=tool_name)


@pytest.fixture
def fake_tools(monkeypatch):
    """往"已装配工具表"里塞两个假工具：一个抛裸异常，一个成功返回。

    为什么改内部表而不是 mock `get_func_tool`：真入口要经过参数校验与
    `except` 转换这两段真实代码，mock 掉查表就把"抛异常之后怎么被兜住"整段跳过。
    """

    def _boom(**_kwargs):
        raise ValueError("boom-376")

    def _ok(**_kwargs):
        return {"success": True, "text": "fine"}

    monkeypatch.setitem(
        funcCore._func_tools,  # noqa: SLF001 —— 判据就要钉在这张表上
        "_fake_fail_376",
        FuncTool(name="_fake_fail_376", callable=_boom, visibility=ToolVisibility.TOOL_ONLY),
    )
    monkeypatch.setitem(
        funcCore._func_tools,  # noqa: SLF001
        "_fake_ok_376",
        FuncTool(name="_fake_ok_376", callable=_ok, visibility=ToolVisibility.TOOL_ONLY),
    )


# --------------------------------------------------------------------------- #
# 1. 纯函数：什么该补、什么不该补
# --------------------------------------------------------------------------- #


def test_ensure_error_code_supplies_generic_code() -> None:
    """无码失败体 ⇒ 补通用码（不猜语义：文案里有"不存在"也不许补 `not_found`）。"""
    patched = ensure_error_code({"success": False, "message": "任务不存在"})
    assert patched["error_code"] == OPERATION_FAILED
    assert patched["message"] == "任务不存在"


@pytest.mark.parametrize(
    "code",
    ["invalid_tool_args", "invalid_json_args", "not_found", "agent_not_failed", "permission_denied"],
)
def test_ensure_error_code_keeps_existing_code(code: str) -> None:
    """已有码一律不覆盖——兜底只填空白，不改判。"""
    body = {"success": False, "message": "x", "error_code": code}
    assert ensure_error_code(body)["error_code"] == code


@pytest.mark.parametrize("blank", ["", None])
def test_ensure_error_code_treats_blank_code_as_missing(blank) -> None:
    """`""` / `None` 等于"没码"（不是"有码但为空"），要补上。"""
    body = {"success": False, "message": "x", "error_code": blank}
    assert ensure_error_code(body)["error_code"] == OPERATION_FAILED


@pytest.mark.parametrize(
    "body",
    [
        {"success": True},
        {"success": True, "text": "正文"},
        {"total": 3},  # 无 `success` 键：不是失败体，不该被塞码
        ["not", "a", "dict"],
        "plain text",
        None,
    ],
)
def test_ensure_error_code_leaves_non_failure_alone(body) -> None:
    """成功体 / 无 `success` 键 / 非 dict 一律原样返回。"""
    assert ensure_error_code(body) == body


def test_ensure_error_code_does_not_mutate_input_and_adds_no_extra_keys() -> None:
    """返回新对象、只多一个键：不动调用方的 dict，也不顺手塞别的字段。"""
    body = {"success": False, "message": "x", "hint": "h"}
    patched = ensure_error_code(body)

    assert body == {"success": False, "message": "x", "hint": "h"}, "原 dict 被就地改写了"
    assert set(patched) - set(body) == {"error_code"}
    assert set(body) - set(patched) == set()


# --------------------------------------------------------------------------- #
# 2. 真入口：守卫确实接在 `run_tool_call()` 的出口上
# --------------------------------------------------------------------------- #


async def test_run_tool_call_empty_tool_name_gets_code() -> None:
    """`tool_name` 为空这条分支原本直接返回无码失败体，现在必须被补上。

    同时钉住"补码不改文案"：`函数执行失败: tool_name 为空` 有既有提示词依赖。
    """
    result = await funcCore.run_tool_call({}, context=None)

    assert result["success"] is False
    assert result["error_code"] == OPERATION_FAILED
    assert result["message"] == "函数执行失败: tool_name 为空"


async def test_dispatch_layer_still_returns_no_code() -> None:
    """"补码"发生在出口，不是执行体内部——否则判据可能被别的层悄悄满足。

    这是本文件的**对照组**：同一条路径在 `_dispatch_tool_call` 上仍是无码的，
    只有经过 `run_tool_call` 才会多出 `operation_failed`。少了这条，前一条断言
    无法排除"码是执行体自己补的"。
    """
    raw = await funcCore._dispatch_tool_call({}, context=None)  # noqa: SLF001

    assert raw["success"] is False
    assert "error_code" not in raw


async def test_run_tool_call_plain_exception_gets_operation_failed(fake_tools) -> None:
    """工具抛裸异常（无 `error_code` 属性）⇒ 出口补通用码。"""
    result = await funcCore.run_tool_call({}, _ctx("_fake_fail_376"))

    assert result["success"] is False
    assert result["error_code"] == OPERATION_FAILED
    assert "函数执行失败: " in result["message"]
    assert "boom-376" in result["message"]


async def test_run_tool_call_keeps_invalid_json_args() -> None:
    """坏 JSON 的码（`invalid_json_args`）不被兜底抹平。"""
    result = await funcCore.run_tool_call("{not json", _ctx("_fake_ok_376"))

    assert result["success"] is False
    assert result["error_code"] == "invalid_json_args"


async def test_run_tool_call_keeps_invalid_tool_args() -> None:
    """参数校验失败（`invalid_tool_args`）不被兜底抹平——真工具、真校验，不 mock。"""
    # 本目录的单测默认没装工具表，先装配（与 `test_container_note_console` 同法）。
    funcCore.load_func_tools()
    assert funcCore.get_func_tool("memory_read") is not None, "工具表没装上，下面等于在测 404"

    # `memory_read` 的 `memory_id` 必填：给空对象必然在校验段被判掉（不会执行到工具体）。
    result = await funcCore.run_tool_call(json.dumps({}), _ctx("memory_read"))

    assert result["success"] is False
    assert result["error_code"] == "invalid_tool_args"


async def test_run_tool_call_success_has_no_error_code(fake_tools) -> None:
    """成功体不被塞码（兜底不能把成功也标成失败）。"""
    result = await funcCore.run_tool_call(json.dumps({}), _ctx("_fake_ok_376"))

    assert result["success"] is True
    assert "error_code" not in result

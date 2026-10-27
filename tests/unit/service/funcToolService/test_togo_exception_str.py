"""#86：`TogoException.__str__` 不得泄漏元组字面量（工具层 / REST 错误文案变形）。

背景（网关开发实测发现）：
    TogoException.__init__ 里 `super().__init__(error_message, error_code)` 传两个参数，
    而 `Exception.__str__` 对**两个及以上** args 返回整个元组的 repr，于是
        str(TogoException("已存在成员相同的房间", error_code="ROOM_DUPLICATE"))
        == "('已存在成员相同的房间', 'ROOM_DUPLICATE')"
    这串东西会原样进入工具层返回给模型的消息（`save_room` / `delete_room` / `delete_dept`）
    以及 REST 的 `error_desc`。

本文件守住三件事：
  1. `str(exc)` 只给 error_message；`error_message` / `error_code` 属性语义不变；`args` 不变。
  2. 工具层失败返回体：`message` 不含元组形态，且带结构化 `error_code`。
  3. `run_tool_call` 兜底分支同样带 `error_code`。
"""

from __future__ import annotations

import inspect

import pytest

from exception import TogoException
from service.funcToolService import tools
from service.roomService import ToolCallContext


# --------------------------------------------------------------------------- #
# 1. 异常本身
# --------------------------------------------------------------------------- #


def test_str_returns_message_only_not_tuple_literal():
    """`str(exc)` 只返回 error_message —— 修复前这里是元组 repr，会红。"""
    exc = TogoException("已存在成员相同的房间", error_code="ROOM_DUPLICATE")

    assert str(exc) == "已存在成员相同的房间"
    assert str(exc) != "('已存在成员相同的房间', 'ROOM_DUPLICATE')"
    assert "(" not in str(exc) and "'" not in str(exc)


def test_str_without_error_code_is_unchanged():
    """不带 error_code 时行为与基类一致（都是纯消息）。"""
    assert str(TogoException("无错误码")) == "无错误码"


def test_error_message_and_error_code_properties_unchanged():
    """修复不得改 `error_message` / `error_code` 语义（assertUtil、baseController 依赖）。"""
    exc = TogoException("成员不足", error_code="DEPT_MEMBERS_TOO_FEW")

    assert exc.error_message == "成员不足"
    assert exc.error_code == "DEPT_MEMBERS_TOO_FEW"


def test_args_still_holds_both_values():
    """刻意**不动** args：日志里的 `repr(exc)` 仍能看到 error_code。"""
    exc = TogoException("成员不足", error_code="DEPT_MEMBERS_TOO_FEW")

    assert exc.args == ("成员不足", "DEPT_MEMBERS_TOO_FEW")
    assert repr(exc) == "TogoException('成员不足', 'DEPT_MEMBERS_TOO_FEW')"


def test_str_is_overridden_not_inherited():
    """护栏：确认 `__str__` 是 TogoException 自己定义的（不是靠基类恰好行为正确）。"""
    assert "__str__" in TogoException.__dict__


# --------------------------------------------------------------------------- #
# 2. 工具层失败返回体
# --------------------------------------------------------------------------- #


def test_tool_error_result_maps_togo_exception_to_structured_failure():
    """`_tool_error_result`：message 只留文本，error_code 单独给字段。"""
    exc = TogoException("已存在成员相同的房间", error_code="ROOM_DUPLICATE")

    result = tools._tool_error_result(exc)

    assert result["success"] is False
    assert result["message"] == "已存在成员相同的房间"
    assert result["error_code"] == "ROOM_DUPLICATE"


def test_tool_error_result_handles_plain_exception_without_error_code():
    """普通异常没有 error_code：不得凭空造一个字段，message 走 str()。"""
    result = tools._tool_error_result(ValueError("坏的参数"))

    assert result == {"success": False, "message": "坏的参数"}


def test_tool_error_result_never_emits_tuple_literal():
    """核心断言：返回体里不得出现 `('...', 'CODE')` 这种 Python 元组字面量。"""
    result = tools._tool_error_result(TogoException("房间重名", error_code="ROOM_DUPLICATE"))

    assert not (result["message"].startswith("(") and result["message"].endswith(")"))
    assert "ROOM_DUPLICATE" not in result["message"]


@pytest.mark.asyncio
async def test_save_room_duplicate_returns_structured_error(monkeypatch):
    """save_room 触发 ROOM_DUPLICATE：message 无元组形态，error_code 单独给出。"""

    async def _boom(*args, **kwargs):
        raise TogoException("已存在成员相同的房间 'x'，不能创建重复房间", error_code="ROOM_DUPLICATE")

    class _Agent:
        def __init__(self, agent_id: int, name: str) -> None:
            self.id = agent_id
            self.name = name

    async def _fake_all_agents(team_id: int):
        return [_Agent(1, "alice"), _Agent(2, "bob")]

    async def _no_existing(team_id: int, name: str):
        return None

    monkeypatch.setattr(tools.gtAgentManager, "get_team_all_agents", _fake_all_agents)
    monkeypatch.setattr(tools.gtRoomManager, "get_room_by_team_and_name", _no_existing)
    monkeypatch.setattr(tools.roomService, "upsert_room", _boom)

    ctx = ToolCallContext(agent_id=1, team_id=1, tool_name="save_room")
    res = await tools.save_room(name="dup", member_names=["alice", "bob"], _context=ctx)

    assert res["success"] is False
    assert res["error_code"] == "ROOM_DUPLICATE"
    assert res["message"] == "已存在成员相同的房间 'x'，不能创建重复房间"
    assert "ROOM_DUPLICATE" not in res["message"]


@pytest.mark.asyncio
async def test_delete_room_error_is_structured(monkeypatch):
    """delete_room 失败返回体同样是结构化错误。"""

    class _Room:
        id = 7
        team_id = 1
        name = "r"

    async def _fake_get(room_id: int):
        return _Room()

    async def _boom(*args, **kwargs):
        raise TogoException("DEPT 房间不允许直接修改", error_code="ROOM_DEPT_PROTECTED")

    monkeypatch.setattr(tools.gtRoomManager, "get_room_by_id", _fake_get)
    monkeypatch.setattr(tools.roomService, "delete_managed_room", _boom)

    ctx = ToolCallContext(agent_id=1, team_id=1, tool_name="delete_room")
    res = await tools.delete_room(name="r", room_id=7, _context=ctx)

    assert res["success"] is False
    assert res["error_code"] == "ROOM_DEPT_PROTECTED"
    assert res["message"] == "DEPT 房间不允许直接修改"


@pytest.mark.asyncio
async def test_delete_dept_error_is_structured(monkeypatch):
    """delete_dept 失败返回体同样是结构化错误。"""

    class _Dept:
        id = 9
        team_id = 1
        name = "d"

    async def _fake_get(dept_id: int):
        return _Dept()

    async def _boom(*args, **kwargs):
        raise TogoException("部门 'd' 下还有子部门", error_code="DEPT_HAS_CHILDREN")

    # delete_dept 在函数体内 `from dal.db import gtDeptManager`，故打补丁在 dal.db 模块上。
    from dal.db import gtDeptManager
    import service.deptService as deptService

    monkeypatch.setattr(gtDeptManager, "get_dept_by_id", _fake_get)
    monkeypatch.setattr(deptService, "delete_dept", _boom)

    ctx = ToolCallContext(agent_id=1, team_id=1, tool_name="delete_dept")
    res = await tools.delete_dept(name="d", dept_id=9, _context=ctx)

    assert res["success"] is False
    assert res["error_code"] == "DEPT_HAS_CHILDREN"
    assert res["message"] == "部门 'd' 下还有子部门"


def test_three_tool_except_branches_use_shared_helper():
    """护栏：三处 `except ... str(exc)` 都已走统一 helper，别再各自拼返回体。"""
    src = inspect.getsource(tools)

    assert src.count("return _tool_error_result(exc)") == 3
    assert 'return {"success": False, "message": str(exc)}' not in src


# --------------------------------------------------------------------------- #
# 3. run_tool_call 兜底
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_run_tool_call_fallback_carries_error_code(monkeypatch):
    """兜底分支：异常逃到 run_tool_call 时，返回体带 error_code 且无元组字面量。"""
    import service.funcToolService as funcToolService
    from service.funcToolService.funcToolType import FuncTool

    async def _boom(**kwargs):
        raise TogoException("业务失败", error_code="SOME_CODE")

    monkeypatch.setattr(
        funcToolService.core,
        "get_func_tool",
        lambda name: FuncTool(name=name, callable=_boom),
    )

    ctx = ToolCallContext(agent_id=1, team_id=1, tool_name="save_room")
    res = await funcToolService.run_tool_call("{}", context=ctx)

    assert res["success"] is False
    assert res["error_code"] == "SOME_CODE"
    assert "SOME_CODE" not in res["message"]
    assert res["message"] == "函数执行失败: 业务失败"


# --------------------------------------------------------------------------- #
# 4. deptService 的双重包装
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_overwrite_dept_tree_does_not_double_wrap_togo_exception(monkeypatch):
    """`overwrite_dept_tree` 只该包装 ValueError；内层若已是 TogoException 必须原样透传。

    旧写法 `except ValueError` 兜不到 TogoException，但如果有人图省事改成 `except Exception`
    再 `TogoException(str(exc), ...)`，内层的 error_code 会被替换成 DEPT_MEMBERS_TOO_FEW。
    """
    import service.deptService as deptService

    class _Team:
        enabled = False

    async def _fake_team(team_id: int):
        return _Team()

    class _Root:
        def validate_tree(self) -> None:
            raise TogoException("子部门 X 成员不足", error_code="SOME_INNER_CODE")

    monkeypatch.setattr(deptService.gtTeamManager, "get_team_by_id", _fake_team)

    with pytest.raises(TogoException) as exc_info:
        await deptService.overwrite_dept_tree(1, _Root())

    assert exc_info.value.error_code == "SOME_INNER_CODE"


@pytest.mark.asyncio
async def test_overwrite_dept_tree_wraps_value_error(monkeypatch):
    """`validate_tree` 抛 ValueError（真实实现就是 ValueError）时，包装为 DEPT_MEMBERS_TOO_FEW。"""
    import service.deptService as deptService

    class _Team:
        enabled = False

    async def _fake_team(team_id: int):
        return _Team()

    class _Root:
        def validate_tree(self) -> None:
            raise ValueError("部门 'x' 成员不足 2 人，无法创建房间")

    monkeypatch.setattr(deptService.gtTeamManager, "get_team_by_id", _fake_team)

    with pytest.raises(TogoException) as exc_info:
        await deptService.overwrite_dept_tree(1, _Root())

    assert exc_info.value.error_code == "DEPT_MEMBERS_TOO_FEW"
    # 包装后的 str() 只有文本，没有元组字面量
    assert str(exc_info.value) == "部门 'x' 成员不足 2 人，无法创建房间"

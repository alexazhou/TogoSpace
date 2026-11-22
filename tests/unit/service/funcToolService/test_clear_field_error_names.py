"""`clear_fields` 的报错文案里，工具名必须是**现存**工具名（P1 顺带修的 §1.2 缺陷）。

## 这条判据防什么

`_normalize_clear_fields(clear_fields, allowed, tool_name)` 会把 `tool_name` 拼进
**用户可见**的 message：

    clear_fields 必须是字段名数组（save_agent 当前可用：model / allow_tools …）
    不支持清除的字段：xxx；save_agent 当前可清除的字段只有 model / allow_tools

#315 P0 把 `save_agent / save_dept / save_room` 拆成 `create_* + update_*` 后，这三个
名字**作为工具已不存在**，但 `_upsert_agent` / `_upsert_dept` / `_upsert_room` 仍把旧名
当参数传进去 ⇒ 用户按报错提示去查 `save_agent` 会查不到任何东西。

## 判据为什么不是「等于 update_agent」一条就够

只钉字面值的话，下一次改名又会在别处悄悄漂（这轮就是这么攒出 6 处的）。所以每条用例
同时断言两件事：

1. 文案里出现的名字**属于 `declared_tools()` 里真实注册的工具**（结构性，改名后自动
   兜住）；
2. 且正好是该命令的 update 工具（`update_agent` / `update_dept` / `update_room`）——
   `create_*` 的签名里没有 `clear_fields`，不可能走到这条分支，报 create 名是错的。
"""

from __future__ import annotations

import re

import pytest

from service.funcToolService.toolSpec import declared_tools
from service.funcToolService.tools import update_agent, update_dept, update_room
from service.roomService import ToolCallContext

#: `agent_id` / `dept_id` / `room_id` 用 1：这三条校验都在触库之前返回，
#: 不会真去读数据（判据只关心文案，不需要 DB）。
_CTX = ToolCallContext(agent_id=1, team_id=1, tool_name="console")

_OLD_NAMES = {"save_agent", "save_dept", "save_room"}

#: 文案里的工具名出现位置（两处 message 各一种句式）。
_NAME_IN_BAD_TYPE = re.compile(r"（(\w+) 当前可用")
_NAME_IN_INVALID_FIELD = re.compile(r"(\w+) 当前可清除的字段只有")


async def _call(tool: str, value: object) -> dict:
    if tool == "agent":
        return await update_agent(agent_id=1, clear_fields=value, _context=_CTX)  # type: ignore[arg-type]
    if tool == "dept":
        return await update_dept(dept_id=1, clear_fields=value, _context=_CTX)  # type: ignore[arg-type]
    return await update_room(room_id=1, clear_fields=value, _context=_CTX)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "tool,expected_tool,value,pattern",
    [
        ("agent", "update_agent", 5, _NAME_IN_BAD_TYPE),
        ("agent", "update_agent", ["no_such_field"], _NAME_IN_INVALID_FIELD),
        ("dept", "update_dept", 5, _NAME_IN_BAD_TYPE),
        ("dept", "update_dept", ["no_such_field"], _NAME_IN_INVALID_FIELD),
        ("room", "update_room", 5, _NAME_IN_BAD_TYPE),
        ("room", "update_room", ["no_such_field"], _NAME_IN_INVALID_FIELD),
    ],
)
async def test_clear_field_error_names_a_live_tool(tool, expected_tool, value, pattern):
    result = await _call(tool, value)

    assert result["success"] is False, result
    assert result["error_code"] == "invalid_clear_field", result
    message = result["message"]

    matched = pattern.search(message)
    assert matched is not None, f"文案里没有工具名，句式变了？{message!r}"
    named = matched.group(1)

    live = {item.name for item in declared_tools()}
    assert named in live, f"报错文案指向了一个不存在的工具 {named!r}：{message!r}"
    assert named not in _OLD_NAMES, f"P0 改名前的旧工具名又出现了：{message!r}"
    assert named == expected_tool, f"{tool} 的 clear_fields 报错应点名 {expected_tool}：{message!r}"

"""`memory_*` 工具层测试（Agent 长期记忆系统，#134 段2）。

关注点：**身份注入**（`_context` 提供 `agent_id` / `team_id`，不向 LLM 暴露这两个参数）、
**返回体口径**（对外 JSON 枚举是小写 value、失败带 `error_code`）、以及
工具是否真的注册进 `load_func_tools()` 与 `ToolCategory.BASIC`。

服务层行为（校验 / 配额 / 检索）在 `tests/unit/service/test_memory_service.py` 覆盖，
这里用 mock 替身只验证工具层的接线与序列化。
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import peewee
import pytest

from constants import MemoryStarLevel, MemoryStatus, ToolCategory
from exception import TogoException
from service.agentService.toolRegistry import CATEGORY_CONFIG, build_runtime_allow_specs
from service.funcToolService import tools
from service.funcToolService.core import get_func_tool, load_func_tools
from service.funcToolService.funcToolType import get_function_metadata
from service.roomService import ToolCallContext

_TEAM_ID = 2
_AGENT_ID = 7

MEMORY_TOOL_NAMES = (
    "memory_list",
    "memory_read",
    "memory_search",
    "memory_create",
    "memory_update",
)


def _context(agent_id: int = _AGENT_ID, team_id: int = _TEAM_ID) -> ToolCallContext:
    return ToolCallContext(agent_id=agent_id, team_id=team_id)


def _memory(**overrides) -> Any:
    """构造一条带枚举字段的记忆替身（只用到被序列化的字段）。"""
    import datetime

    data = {
        "id": 12,
        "team_id": _TEAM_ID,
        "agent_id": _AGENT_ID,
        "title": "方案输出规范",
        "summary": "先给结论再展开细节",
        "content": "正文",
        "star_level": MemoryStarLevel.SUMMARY,
        "status": MemoryStatus.ACTIVE,
        "tags": ["输出风格"],
        "created_at": datetime.datetime(2026, 11, 6, 10, 0, 0),
        "updated_at": datetime.datetime(2026, 11, 6, 10, 24, 0),
    }
    data.update(overrides)
    memory = SimpleNamespace(**data)
    return memory


# ---------------------------------------------------------------------------
# 注册与工具面
# ---------------------------------------------------------------------------


def test_memory_tools_registered_in_load_func_tools():
    registered = load_func_tools()
    for name in MEMORY_TOOL_NAMES:
        assert name in registered, f"{name} 未注册进 load_func_tools()"


def test_memory_tools_resolvable_by_get_func_tool():
    """执行期按名字查找必须命中（`run_tool_call` 走的是这个入口）。"""
    load_func_tools()
    for name in MEMORY_TOOL_NAMES:
        tool = get_func_tool(name)
        assert tool is not None, f"{name} 无法按名字解析"
        assert callable(tool.callable)


def test_memory_tools_are_basic_category():
    for name in MEMORY_TOOL_NAMES:
        assert CATEGORY_CONFIG.get(name) is ToolCategory.BASIC, f"{name} 未归入 BASIC"


def test_basic_category_keeps_memory_tools_available_by_default():
    """`allowed_tools is None` 时默认规格含 `Category:Basic`，记忆工具默认可用。"""
    assert "Category:Basic" in build_runtime_allow_specs(None, is_root_leader=False)
    # 即使显式给了别的类别，Basic 也会被强制补上
    assert "Category:Basic" in build_runtime_allow_specs(["Category:Read"], is_root_leader=False)


# ---------------------------------------------------------------------------
# 签名：不向 LLM 暴露 team_id / agent_id
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", MEMORY_TOOL_NAMES)
def test_memory_tool_schema_hides_identity_params(name):
    """工具 schema 的 properties 里不能出现 team_id / agent_id / _context。"""
    tool = load_func_tools()[name]
    metadata = get_function_metadata(name, tool.callable)
    properties = set(metadata["parameters"]["properties"])
    assert "_context" not in properties
    assert "team_id" not in properties
    assert "agent_id" not in properties
    assert "memory_id" not in properties or name in {"memory_read", "memory_update"}


def test_memory_list_signature():
    assert list(inspect.signature(tools.memory_list).parameters) == ["include_archived", "_context"]
    assert inspect.signature(tools.memory_list).parameters["include_archived"].default is False


def test_memory_read_signature():
    assert list(inspect.signature(tools.memory_read).parameters) == ["memory_id", "_context"]


def test_memory_search_signature():
    params = inspect.signature(tools.memory_search).parameters
    assert list(params) == ["query", "limit", "_context"]
    assert params["limit"].default == 5


def test_memory_create_signature():
    params = inspect.signature(tools.memory_create).parameters
    assert list(params) == ["title", "summary", "content", "star_level", "tags", "_context"]
    assert params["star_level"].default == "none"
    assert params["tags"].default is None


def test_memory_update_signature():
    params = inspect.signature(tools.memory_update).parameters
    assert list(params) == [
        "memory_id", "title", "summary", "content", "star_level", "status", "tags", "_context",
    ]
    # 部分更新：除 memory_id 外全部默认 None（未传 = 不改）
    for name in ("title", "summary", "content", "star_level", "status", "tags"):
        assert params[name].default is None, f"{name} 的默认值应为 None"


# ---------------------------------------------------------------------------
# 上下文缺失
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", MEMORY_TOOL_NAMES)
async def test_memory_tool_requires_context(name):
    """没有上下文时拒绝调用（不猜一个默认 Agent 出来）。"""
    call = getattr(tools, name)
    kwargs: dict[str, Any] = {}
    if name in {"memory_read", "memory_update"}:
        kwargs["memory_id"] = 1
    if name == "memory_search":
        kwargs["query"] = "关键词"
    if name == "memory_create":
        kwargs.update(title="t", summary="s", content="c")

    result = await call(_context=None, **kwargs)
    assert result["success"] is False
    assert "上下文" in result["message"]


# ---------------------------------------------------------------------------
# 返回体口径
# ---------------------------------------------------------------------------


async def test_memory_list_returns_camel_free_snake_and_lowercase_enum():
    """对外 JSON：枚举是**小写 value**，字段名保持 snake_case，L1 不含正文。"""
    with patch("service.memoryService.list_memories", new=AsyncMock(return_value=[])) as mocked:
        result = await tools.memory_list(_context=_context())

    assert result["success"] is True
    assert result["count"] == 0
    assert result["memories"] == []
    mocked.assert_awaited_once()
    # 身份从上下文注入，不是从参数来
    assert mocked.await_args.args == (_TEAM_ID, _AGENT_ID)
    assert mocked.await_args.kwargs == {"include_archived": False}


async def test_memory_list_serializes_dataclass_items():
    """真实 `MemoryListItem` 序列化路径：`star_level` 走小写 value。"""
    from model.coreModel.memoryModel import MemoryListItem
    from util import jsonUtil

    item = MemoryListItem.from_memory(_memory())
    with patch("service.memoryService.list_memories", new=AsyncMock(return_value=[item])):
        result = await tools.memory_list(_context=_context())

    payload = result["memories"][0]
    assert payload["star_level"] == "summary"
    assert payload["title"] == "方案输出规范"
    assert "content" not in payload
    # 顺带证明：默认 jsonUtil 会出大写 name，工具层的小写来自模型序列化器
    assert jsonUtil.object_to_json_data(_memory())["star_level"] == "SUMMARY"


async def test_memory_search_serializes_snippet_and_lowercase_enum():
    from model.coreModel.memoryModel import MemorySearchItem

    item = MemorySearchItem.from_memory(_memory(), "…命中片段…")
    with patch("service.memoryService.search_memories", new=AsyncMock(return_value=[item])) as mocked:
        result = await tools.memory_search(query="偏好", limit=3, _context=_context())

    assert result["count"] == 1
    payload = result["memories"][0]
    assert payload["snippet"] == "…命中片段…"
    assert payload["star_level"] == "summary"
    assert payload["summary"] == "先给结论再展开细节"
    assert "content" not in payload  # L2 不给正文
    assert mocked.await_args.kwargs == {"query": "偏好", "limit": 3}


async def test_memory_read_serializes_detail():
    from model.coreModel.memoryModel import MemoryDetail

    detail = MemoryDetail.from_memory(_memory())
    with patch("service.memoryService.read_memory", new=AsyncMock(return_value=detail)) as mocked:
        result = await tools.memory_read(memory_id=12, _context=_context())

    assert result["success"] is True
    payload = result["memory"]
    assert payload["id"] == 12
    assert payload["content"] == "正文"
    assert payload["star_level"] == "summary"
    assert payload["status"] == "active"
    assert mocked.await_args.args == (_TEAM_ID, _AGENT_ID, 12)


async def test_memory_create_passes_through_all_fields():
    from model.coreModel.memoryModel import MemoryDetail

    detail = MemoryDetail.from_memory(_memory())
    with patch("service.memoryService.create_memory", new=AsyncMock(return_value=detail)) as mocked:
        result = await tools.memory_create(
            title="方案输出规范",
            summary="先给结论再展开细节",
            content="正文",
            star_level="summary",
            tags=["输出风格"],
            _context=_context(),
        )

    assert result["success"] is True
    assert result["memory"]["star_level"] == "summary"
    assert mocked.await_args.args == (_TEAM_ID, _AGENT_ID)
    assert mocked.await_args.kwargs == {
        "title": "方案输出规范",
        "summary": "先给结论再展开细节",
        "content": "正文",
        "star_level": "summary",
        "tags": ["输出风格"],
    }


async def test_memory_update_partial_passthrough():
    """未传的字段以 None 透传给 service（service 侧解释为「不改」）。"""
    from model.coreModel.memoryModel import MemoryDetail

    detail = MemoryDetail.from_memory(_memory())
    with patch("service.memoryService.update_memory", new=AsyncMock(return_value=detail)) as mocked:
        await tools.memory_update(memory_id=12, star_level="title", _context=_context())

    assert mocked.await_args.args == (_TEAM_ID, _AGENT_ID, 12)
    assert mocked.await_args.kwargs == {
        "title": None,
        "summary": None,
        "content": None,
        "star_level": "title",
        "status": None,
        "tags": None,
    }


# ---------------------------------------------------------------------------
# 错误码透传
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error_code, tool_name, call_kwargs, service_fn",
    [
        ("invalid_memory_title", "memory_create", {"title": "", "summary": "s", "content": "c"}, "create_memory"),
        ("duplicate_memory_title", "memory_create", {"title": "t", "summary": "s", "content": "c"}, "create_memory"),
        ("summary_required", "memory_update", {"memory_id": 1, "content": "c"}, "update_memory"),
        ("star_limit_exceeded", "memory_update", {"memory_id": 1, "star_level": "title"}, "update_memory"),
        ("memory_not_found", "memory_read", {"memory_id": 9999}, "read_memory"),
        ("invalid_memory_query", "memory_search", {"query": " "}, "search_memories"),
    ],
)
async def test_memory_tool_surfaces_error_code(error_code, tool_name, call_kwargs, service_fn):
    """`TogoException` 统一转成 `{"success": False, "message", "error_code"}`。"""
    tool = getattr(tools, tool_name)
    with patch(
        f"service.memoryService.{service_fn}",
        new=AsyncMock(side_effect=TogoException("业务错误", error_code)),
    ):
        result = await tool(_context=_context(), **call_kwargs)

    assert result["success"] is False
    assert result["error_code"] == error_code
    assert result["message"] == "业务错误"

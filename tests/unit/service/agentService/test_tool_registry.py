from unittest.mock import AsyncMock, MagicMock
import pytest
from constants import ToolCategory
from service.agentService.toolRegistry import (
    CATEGORY_CONFIG,
    AgentToolRegistry,
    validate_tool_allow_specs,
    build_runtime_allow_specs,
)
from service.roomService import ToolCallContext
from util import llmApiUtil

def _register_tools(registry: AgentToolRegistry, *names: str) -> AsyncMock:
    handler = AsyncMock(return_value={"success": True})
    for name in names:
        # 直接构造 OpenAITool
        tool = llmApiUtil.OpenAITool(
            function=llmApiUtil.OpenAIFunction(
                name=name,
                description="",
                parameters=llmApiUtil.OpenAIFunctionParameter(type="object", properties={}, required=[])
            )
        )
        registry.register(tool, handler, marks_turn_finish=name == "finish_action")
    return handler

def test_validate_tool_allow_specs() -> None:
    # 正常情况 (list_dir 是 READ)
    assert validate_tool_allow_specs(["Category:Read", "list_dir"]) is None
    # 包含 ADMIN 类别
    assert "不允许分配管理员类别权限" in validate_tool_allow_specs(["Category:Admin"])
    # 包含 ADMIN 工具
    assert "不允许分配管理员工具权限" in validate_tool_allow_specs(["save_role_template"])

def test_build_runtime_allow_specs() -> None:
    # 默认权限
    specs = build_runtime_allow_specs(None, is_root_leader=False)
    assert set(specs) == {"Category:Basic", "Category:Read", "Category:Write", "Category:Execute"}
    
    # 指定权限，应自动补齐 Basic
    # list_dir 是 READ
    specs = build_runtime_allow_specs(["list_dir"], is_root_leader=False)
    assert set(specs) == {"list_dir", "Category:Basic"}
    
    # Root Leader 自动补齐 Admin 和 Basic
    specs = build_runtime_allow_specs(None, is_root_leader=True)
    assert "Category:Admin" in specs
    assert "Category:Basic" in specs

def test_registry_register_and_list() -> None:
    registry = AgentToolRegistry()
    # list_dir 是 READ, send_chat_msg 是 BASIC
    _register_tools(registry, "list_dir", "send_chat_msg")
    
    assert set(registry.list_registered_tool_names()) == {"list_dir", "send_chat_msg"}
    assert set(registry.list_enabled_tool_names()) == {"list_dir", "send_chat_msg"}
    
    tool = registry.get_registered_tool("list_dir")
    assert tool.category == ToolCategory.READ
    assert tool.marks_turn_finish is False

def test_registry_clear() -> None:
    registry = AgentToolRegistry()
    _register_tools(registry, "list_dir")
    registry.clear()
    assert registry.list_registered_tool_names() == []

def test_apply_tool_allow_specs() -> None:
    registry = AgentToolRegistry()
    _register_tools(registry, "list_dir", "send_chat_msg", "save_role_template")
    
    # 仅开启 Basic
    registry.apply_tool_allow_specs(["Category:Basic"])
    # send_chat_msg 是 BASIC, list_dir 是 READ
    assert registry.list_enabled_tool_names() == ["send_chat_msg"]
    
    # 仅开启指定工具
    registry.apply_tool_allow_specs(["list_dir"])
    assert registry.list_enabled_tool_names() == ["list_dir"]
    
    # 开启 Read 和具体工具
    registry.apply_tool_allow_specs(["Category:Read", "save_role_template"])
    assert set(registry.list_enabled_tool_names()) == {"list_dir", "save_role_template"}

def test_read_image_categorized_and_enabled_under_read() -> None:
    """read_image 应归入 READ 类别，Category:Read 策略下被启用（防止漏配 CATEGORY_CONFIG）。"""
    registry = AgentToolRegistry()
    _register_tools(registry, "read_image", "read_file")
    registry.apply_tool_allow_specs(["Category:Read"])
    assert "read_image" in registry.list_enabled_tool_names()


def test_basic_category_has_no_get_task() -> None:
    """#71：废弃别名 get_task 已删除，Category:Basic 与注册表都不再出现它，正式工具仍在。"""
    from service.funcToolService import core

    core.load_func_tools()
    registry = AgentToolRegistry()
    for tool in core.get_tools():
        registry.register(tool, lambda args_str, ctx: None)

    basic_names = registry.resolve_enabled_tool_names(["Category:Basic"])

    assert "get_task" not in basic_names
    assert "get_task_detail" in basic_names
    assert "list_task_events" in basic_names
    # 别名已彻底移除：不再可被注册表解析，也不在任何类别里
    assert "get_task" not in registry.list_registered_tool_names()
    assert "get_task" not in registry.resolve_enabled_tool_names(["get_task"])
    assert "get_task" not in registry.resolve_enabled_tool_names(["Category:Read"])


@pytest.mark.asyncio
async def test_execute_tool_call_success() -> None:
    registry = AgentToolRegistry()

    # 显式定义的 handler，方便断言参数
    async def mock_handler(args_str: str, context: ToolCallContext) -> dict:
        # 不要返回 context 对象，会引起 jsonUtil 递归（尤其是 mock 对象）
        return {"success": True, "time": "12:00", "handled_tool": context.tool_name}

    tool = llmApiUtil.OpenAITool(
        function=llmApiUtil.OpenAIFunction(
            name="list_dir",
            description="",
            parameters=llmApiUtil.OpenAIFunctionParameter(type="object", properties={}, required=[])
        )
    )
    registry.register(tool, mock_handler)

    mock_room = MagicMock()
    ctx = ToolCallContext(agent_id=1, team_id=1, chat_room=mock_room)

    result = await registry.execute_tool_call(
        llmApiUtil.OpenAIToolCall(id="tc_1", function={"name": "list_dir", "arguments": "{}"}),
        context=ctx
    )

    assert result.success is True
    assert result.result["time"] == "12:00"
    assert result.result["handled_tool"] == "list_dir"


@pytest.mark.asyncio
async def test_execute_tool_call_unknown_tool() -> None:
    registry = AgentToolRegistry()
    ctx = ToolCallContext(agent_id=1, team_id=1, chat_room=MagicMock())
    result = await registry.execute_tool_call(
        llmApiUtil.OpenAIToolCall(id="tc_1", function={"name": "unknown", "arguments": "{}"}),
        context=ctx
    )
    assert result.success is False
    assert "未知工具" in result.error_message

@pytest.mark.asyncio
async def test_execute_tool_call_disabled_tool() -> None:
    registry = AgentToolRegistry()
    _register_tools(registry, "list_dir")
    registry.apply_tool_allow_specs([]) # 全部禁用
    
    ctx = ToolCallContext(agent_id=1, team_id=1, chat_room=MagicMock())
    result = await registry.execute_tool_call(
        llmApiUtil.OpenAIToolCall(id="tc_1", function={"name": "list_dir", "arguments": "{}"}),
        context=ctx
    )
    assert result.success is False
    assert "工具无权限使用" in result.error_message

@pytest.mark.asyncio
async def test_execute_tool_call_exception() -> None:
    registry = AgentToolRegistry()
    handler = _register_tools(registry, "list_dir")
    handler.side_effect = Exception("boom")
    
    ctx = ToolCallContext(agent_id=1, team_id=1, chat_room=MagicMock())
    result = await registry.execute_tool_call(
        llmApiUtil.OpenAIToolCall(id="tc_1", function={"name": "list_dir", "arguments": "{}"}),
        context=ctx
    )
    assert result.success is False
    assert "工具调用失败" in result.result["message"]


# ---------------------------------------------------------------------------
# #75：CATEGORY_CONFIG 分类归属的字面量契约
# ---------------------------------------------------------------------------

# 分类表（37 条 = tools.py 本地 26 + TSP 11），**刻意写死字面量**：
# 分类归属决定 `Category:*` 展开出哪些工具（见 `build_runtime_allow_specs`），
# 把 `update_task` 挪出 BASIC 等于悄悄收走 Agent 的核心协作能力（`Category:Basic`
# 下模型就看不到任务工具了），而 tool schema 快照对此完全沉默。故这里把归属
# 变成显式契约，让「归错 / 漏登记 / 多残留」都以代码 diff 的形式出现在 review 里。
#
# 分布（按 `ToolCategory.name` 统计，**不要按 `.value` 反推** ——
# 枚举声明序是 ADMIN=1 / BASIC=2 / READ=3 / WRITE=4 / EXECUTE=5）：
#   BASIC 15 / ADMIN 10 / READ 6 / EXECUTE 4 / WRITE 2
EXPECTED_TOOL_CATEGORIES = {
    "create_task": ToolCategory.BASIC,
    "delete_dept": ToolCategory.ADMIN,
    "delete_role_template": ToolCategory.ADMIN,
    "delete_room": ToolCategory.ADMIN,
    "edit": ToolCategory.WRITE,
    "execute_bash": ToolCategory.EXECUTE,
    "finish_action": ToolCategory.BASIC,
    "get_agent_info": ToolCategory.BASIC,
    "get_dept_info": ToolCategory.BASIC,
    "get_role_template": ToolCategory.ADMIN,
    "get_room_info": ToolCategory.BASIC,
    "get_task_detail": ToolCategory.BASIC,
    "get_task_event": ToolCategory.BASIC,
    "get_time": ToolCategory.BASIC,
    "glob": ToolCategory.READ,
    "grep_search": ToolCategory.READ,
    "list_dir": ToolCategory.READ,
    "list_role_templates": ToolCategory.ADMIN,
    "list_task_events": ToolCategory.BASIC,
    "list_tasks": ToolCategory.BASIC,
    "load_skill": ToolCategory.BASIC,
    "process_list": ToolCategory.EXECUTE,
    "process_output": ToolCategory.EXECUTE,
    "process_stop": ToolCategory.EXECUTE,
    "read_file": ToolCategory.READ,
    "read_image": ToolCategory.READ,
    "reload_team": ToolCategory.ADMIN,
    "save_agent": ToolCategory.ADMIN,
    "save_dept": ToolCategory.ADMIN,
    "save_role_template": ToolCategory.ADMIN,
    "save_room": ToolCategory.ADMIN,
    "send_chat_msg": ToolCategory.BASIC,
    "start_chat": ToolCategory.BASIC,
    "update_task": ToolCategory.BASIC,
    "wake_up_agent": ToolCategory.BASIC,
    "web_search": ToolCategory.READ,
    "write_file": ToolCategory.WRITE,
}


def test_category_config_matches_expected_contract() -> None:
    """`CATEGORY_CONFIG` 必须与字面量契约**双向相等**。

    用 dict 相等而不是逐个 lookup：单向查表会放过「新增工具漏登记」（期望表里没有
    就不会被检查），而漏登记正是最容易悄悄发生的漂移。dict 相等把「漏登记」
    「多残留」「类别改错」三种情况一次拦住，26/37 条的规模下 diff 依然可读。
    """
    assert dict(CATEGORY_CONFIG) == EXPECTED_TOOL_CATEGORIES
    assert set(CATEGORY_CONFIG) == set(EXPECTED_TOOL_CATEGORIES)

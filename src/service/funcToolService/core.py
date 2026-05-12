import inspect
import json
import logging
from typing import Any, Iterable, Optional

from constants import ToolCategory
from util import llmApiUtil
from service.roomService import ToolCallContext
from .toolConfig import CATEGORY_CONFIG
from .funcToolType import FuncTool
from .tools import (
    delete_role_template,
    finish_chat_turn,
    get_agent_info,
    get_dept_info,
    get_role_template,
    get_room_info,
    get_time,
    list_role_templates,
    save_role_template,
    send_chat_msg,
    wake_up_agent,
)

logger = logging.getLogger(__name__)


def build_tools(func_tools: Iterable[FuncTool]) -> list[llmApiUtil.OpenAITool]:
    """遍历 FuncTool 定义，构建并返回工具列表。"""
    return [func_tool.to_openai_tool() for func_tool in func_tools]


_func_tools: dict[str, FuncTool] = {}


def load_func_tools() -> dict[str, FuncTool]:
    global _func_tools
    _registry: dict[str, Any] = {
        "get_time": get_time,
        "send_chat_msg": send_chat_msg,
        "finish_chat_turn": finish_chat_turn,
        "get_dept_info": get_dept_info,
        "get_room_info": get_room_info,
        "get_agent_info": get_agent_info,
        "wake_up_agent": wake_up_agent,
        "list_role_templates": list_role_templates,
        "get_role_template": get_role_template,
        "save_role_template": save_role_template,
        "delete_role_template": delete_role_template,
    }
    _func_tools = {}
    for name, func in _registry.items():
        _func_tools[name] = FuncTool(name, func, CATEGORY_CONFIG.get(name))
    return _func_tools


def get_func_tool(name: str) -> FuncTool | None:
    return _func_tools.get(name)


async def startup() -> None:
    """加载启用的函数列表，须在首次调用 get_tools 前调用一次。"""
    load_func_tools()


def get_tools() -> list[llmApiUtil.OpenAITool]:
    """返回已初始化的工具列表。"""
    return build_tools(_func_tools.values())


def get_tools_by_names(
    names: list[str],
    *,
    category_list: set[ToolCategory] | None = None,
) -> list[llmApiUtil.OpenAITool]:
    """根据名称列表从注册表构建并返回对应工具的 schema 列表。
    若指定 category_list，则仅返回属于这些类别的工具。
    """
    return build_tools([
        _func_tools[name]
        for name in names
        if name in _func_tools and (
            category_list is None or CATEGORY_CONFIG.get(name) in category_list
        )
    ])


def build_effective_tool_allow_specs(
    allowed_tools: list[str] | None,
    *,
    is_root_leader: bool,
    default_enable_all: bool,
) -> list[str]:
    """根据 allowed_tools 和角色构建实际生效的工具规格列表。"""
    if allowed_tools is None:
        effective_specs = (
            ["Category:Basic", "Category:Read", "Category:Write", "Category:Execute"]
            if default_enable_all else
            ["Category:Basic"]
        )
    else:
        effective_specs = list(allowed_tools)

    if is_root_leader:
        if "Category:Admin" not in effective_specs:
            effective_specs.append("Category:Admin")
        return effective_specs

    filtered_specs: list[str] = []
    for spec in effective_specs:
        category = ToolCategory.from_spec(spec)
        if category == ToolCategory.ADMIN:
            continue
        if CATEGORY_CONFIG.get(spec) == ToolCategory.ADMIN:
            continue
        filtered_specs.append(spec)
    return filtered_specs


def resolve_enabled_tool_names(
    tool_names: Iterable[str],
    allow_specs: list[str],
) -> list[str]:
    """根据 allow_specs 从 tool_names 中解析出实际启用的工具名列表。"""
    ordered_names = list(tool_names)
    categories = {ToolCategory.BASIC}
    explicit_names: set[str] = set()

    for spec in allow_specs:
        category = ToolCategory.from_spec(spec)
        if category is not None:
            categories.add(category)
            continue
        if spec in ordered_names:
            explicit_names.add(spec)

    resolved = [t.function.name for t in get_tools_by_names(ordered_names, category_list=categories)]
    for tool_name in ordered_names:
        if tool_name in explicit_names and tool_name not in resolved:
            resolved.append(tool_name)
    return resolved


def filter_external_allowed_tools(allowed_tools: list[str] | None) -> list[str] | None:
    """从 allowed_tools 中过滤掉类别规格和已知本地工具名，返回纯外部工具列表。"""
    if allowed_tools is None:
        return None
    filtered: list[str] = []
    for spec in allowed_tools:
        if ToolCategory.from_spec(spec) is not None:
            continue
        if spec in CATEGORY_CONFIG:
            continue
        filtered.append(spec)
    return filtered



async def run_tool_call(
    function_args: str,
    context: Optional[ToolCallContext] = None,
) -> dict[str, Any]:
    """解析 function_args JSON 字符串并执行函数，返回结果字典。"""
    function_name = context.tool_name if context is not None else ""
    if not function_name:
        logger.error("函数执行失败: tool_name 为空")
        return {"success": False, "message": "函数执行失败: tool_name 为空"}

    try:
        args: dict = json.loads(function_args)
    except json.JSONDecodeError:
        logger.warning(f"工具参数 JSON 解析失败，已忽略参数: tool={function_name}, args={function_args!r}")
        args = {}

    caller = context.agent_id if context is not None else "unknown"
    logger.info(f"use_tool: caller_id={caller}, tool={function_name}, args={args}")

    try:
        func_tool = get_func_tool(function_name)
        func = func_tool.callable if func_tool is not None else None

        if func is None:
            raise ValueError(f"Function {function_name} not found")

        if not callable(func):
            raise ValueError(f"{function_name} is not callable")

        if context and "_context" in inspect.signature(func).parameters:
            args = {**args, "_context": context}

        result = func(**args)

        if inspect.isawaitable(result):
            result = await result

        if not isinstance(result, dict):
            result = {"success": True, "result": result}

        logger.info(f"函数执行结果: {result}")
        return result

    except Exception as e:
        if isinstance(e, TypeError):
            error = f"Invalid arguments for function {function_name}: {e}"
        else:
            error = str(e)

        logger.error(f"函数执行失败: {e}")
        return {"success": False, "message": f"函数执行失败: {error}"}


def shutdown() -> None:
    """清空工具列表，程序退出前调用。"""
    global _func_tools
    _func_tools = {}

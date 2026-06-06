import inspect
import json
import logging
from typing import Any, Iterable, Optional

from constants import ToolCategory
from util import llmApiUtil
from service.roomService import ToolCallContext
from .funcToolType import FuncTool
from .tools import (
    create_task,
    delete_dept,
    delete_role_template,
    delete_room,
    finish_action,
    get_agent_info,
    get_dept_info,
    get_role_template,
    get_room_info,
    get_task,
    get_time,
    list_role_templates,
    list_tasks,
    load_skill,
    reload_team,
    save_agent,
    save_dept,
    start_chat,
    save_role_template,
    save_room,
    send_chat_msg,
    update_task,
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
        "finish_action": finish_action,
        "get_dept_info": get_dept_info,
        "get_room_info": get_room_info,
        "get_agent_info": get_agent_info,
        "wake_up_agent": wake_up_agent,
        "start_chat": start_chat,
        "reload_team": reload_team,
        "list_role_templates": list_role_templates,
        "get_role_template": get_role_template,
        "save_agent": save_agent,
        "save_dept": save_dept,
        "delete_dept": delete_dept,
        "save_room": save_room,
        "delete_room": delete_room,
        "save_role_template": save_role_template,
        "delete_role_template": delete_role_template,
        "create_task": create_task,
        "update_task": update_task,
        "get_task": get_task,
        "list_tasks": list_tasks,
        "load_skill": load_skill,
    }
    _func_tools = {}
    for name, func in _registry.items():
        _func_tools[name] = FuncTool(name, func)
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
) -> list[llmApiUtil.OpenAITool]:
    """根据名称列表从注册表构建并返回对应工具的 schema 列表。"""
    return build_tools([
        _func_tools[name]
        for name in names
        if name in _func_tools
    ])



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

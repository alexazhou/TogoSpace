import inspect
import json
import logging
from typing import Any, Iterable, Optional

from pydantic import BaseModel

from constants import ToolCategory
from model.coreModel.serviceStatus import ServiceStatusInfo
from service import serviceUtil
from util import llmApiUtil
from service.roomService import ToolCallContext
from .funcToolType import FuncTool
from .tools import (
    create_container,
    create_task,
    delete_container,
    delete_dept,
    delete_role_template,
    delete_room,
    enter_container,
    exit_to_host,
    finish_action,
    get_agent_info,
    get_dept_info,
    get_role_template,
    get_room_info,
    get_task_detail,
    get_task_event,
    get_time,
    list_containers,
    list_role_templates,
    list_task_events,
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
    web_search,
)

logger = logging.getLogger(__name__)

s_status = ServiceStatusInfo(name="funcToolService")


def build_tools(func_tools: Iterable[FuncTool]) -> list[llmApiUtil.OpenAITool]:
    """遍历 FuncTool 定义，构建并返回工具列表。"""
    return [func_tool.to_openai_tool() for func_tool in func_tools]


_func_tools: dict[str, FuncTool] = {}


def load_func_tools() -> dict[str, FuncTool]:
    global _func_tools
    _registry: dict[str, Any] = {
        "get_time": get_time,
        "web_search": web_search,
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
        "get_task_detail": get_task_detail,
        "list_task_events": list_task_events,
        "get_task_event": get_task_event,
        "list_tasks": list_tasks,
        "load_skill": load_skill,
    }
    _func_tools = {}
    for name, func in _registry.items():
        _func_tools[name] = FuncTool(name, func)
    return _func_tools


# 容器工具单独一组：只对 TspAgentDriver 暴露（§6.1 生效范围）。
# 放在这里而不是混进 `load_func_tools` 的注册表，是为了让「默认工具集」保持原样
# ——`get_tools()` 不带容器工具，Native / Claude SDK 的行为零变化。
CONTAINER_TOOL_NAMES = (
    "create_container",
    "enter_container",
    "exit_to_host",
    "list_containers",
    "delete_container",
)

_container_func_tools: dict[str, FuncTool] = {}


def load_container_func_tools() -> dict[str, FuncTool]:
    """加载容器工具（`startup` 时一并构建，避免每次 `get_tools` 现造）。"""
    global _container_func_tools
    _container_func_tools = {
        name: FuncTool(name, globals()[name]) for name in CONTAINER_TOOL_NAMES
    }
    return _container_func_tools


def get_func_tool(name: str) -> FuncTool | None:
    # 执行时按名字查找必须覆盖两组工具：容器工具只是「默认不暴露」给
    # Native / Claude SDK，并不代表它们不能被 TSP driver 执行。
    return _func_tools.get(name) or _container_func_tools.get(name)


def _reset() -> None:
    """纯资源清理：正常 shutdown 与启动失败回滚共用；幂等。"""
    _func_tools.clear()
    _container_func_tools.clear()


@serviceUtil.service_start_wrapper(s_status, on_error=_reset)
async def startup() -> None:
    """加载启用的函数列表，须在首次调用 get_tools 前调用一次。"""
    load_func_tools()
    load_container_func_tools()


def get_tools(include_container_tools: bool = False) -> list[llmApiUtil.OpenAITool]:
    """返回已初始化的工具列表。

    Args:
        include_container_tools: 是否包含容器沙箱工具。默认 False——只有
            `TspAgentDriver` 需要（容器工具要切换 gtsp 执行环境），Native /
            Claude SDK 暴露出来只会误导模型（§6.1 生效范围）。
    """
    tools = list(_func_tools.values())
    if include_container_tools:
        tools.extend(_container_func_tools.values())
    return build_tools(tools)


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

        if isinstance(result, BaseModel):
            result = result.model_dump(mode="json")
        elif not isinstance(result, dict):
            result = {"success": True, "result": result}

        logger.info(f"函数执行结果: {result}")
        return result

    except Exception as e:
        if isinstance(e, TypeError):
            error = f"Invalid arguments for function {function_name}: {e}"
        else:
            # `TogoException.__str__` 只吐 error_message（不带 error_code 的元组 repr）。
            error = str(e)

        # 日志用 `!r`：`str(e)` 现在是干净的业务消息，error_code 只留在 `args` /
        # `repr` 里；用 `{e}` 记日志会把错误码一起丢掉，排查时无法按码 grep。
        logger.error(f"函数执行失败: {e!r}")
        failure: dict[str, Any] = {"success": False, "message": f"函数执行失败: {error}"}
        # 结构化带上错误码，模型可据此自纠；非 TogoException 的异常没有该属性，跳过。
        error_code = getattr(e, "error_code", None)
        if error_code is not None:
            failure["error_code"] = error_code
        return failure


@serviceUtil.service_stop_wrapper(s_status)
def shutdown() -> None:
    """清空工具列表，程序退出前调用。"""
    _reset()

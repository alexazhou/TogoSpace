import inspect
import json
import logging
import os
from typing import Any, Iterable, Optional

from pydantic import BaseModel

from constants import ToolCategory, ToolVisibility
from exception import ForwardToolCall
from model.coreModel.serviceStatus import ServiceStatusInfo
from service import serviceUtil
from util import llmApiUtil
from service.roomService import ToolCallContext
from .consoleCommands import COMMAND_SPECS, ENV_KEEP_LEGACY_TOOLS, LEGACY_CONVERGED_TOOLS
from .toolArgsValidation import validate_tool_args
from toolResult import error_result, invalid_json_args
from .consoleTool import console, console_triggers_self_interrupt
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
    memory_create,
    memory_list,
    memory_read,
    memory_search,
    memory_update,
    reload_team,
    save_agent,
    save_dept,
    start_chat,
    save_role_template,
    save_room,
    send_chat_msg,
    update_container_note,
    update_task,
    wake_up_agent,
    web_search,
    webfetch,
)

logger = logging.getLogger(__name__)

s_status = ServiceStatusInfo(name="funcToolService")


def build_tools(func_tools: Iterable[FuncTool]) -> list[llmApiUtil.OpenAITool]:
    """遍历 FuncTool 定义，构建并返回工具列表。"""
    return [func_tool.to_openai_tool() for func_tool in func_tools]


_func_tools: dict[str, FuncTool] = {}


def keep_legacy_admin_tools() -> bool:
    """D2 单点开关：被 `console` 收敛的 16 个管理工具是否仍作为独立 Tool 暴露。

    开关只影响**模型暴露面**（`ToolVisibility.tool_visible`）；注册面与 console 的
    转发面不受影响（#166-A 已修）：

    - 三个 Driver 用 `get_all_tools()` 全量注册，`console` 的第 2 跳因此总能找到目标；
    - `get_tools()` / `export_openai_tools()` / Claude SDK 的工具列表只保留
      `tool_visible` 的工具；
    - `execute_tool_call` 第 1 跳要求 `tool_visible`，第 2 跳（console 转发）要求
      `console_visible`。隐藏模式下已收敛工具的动态可见性是 `CONSOLE_ONLY`，
      所以「模型看不到、console 调得到」。

    口径用环境变量而不是模块常量：测 token 收益时要在**不改代码**的前提下对比两种
    暴露面，常量得改文件、重启才生效。默认 = 保留；任何无法识别的值也按保留处理
    ——「悄悄收走一批工具」的后果比「多暴露几个工具」严重得多。
    """
    raw = os.environ.get(ENV_KEEP_LEGACY_TOOLS, "")
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def resolve_effective_visibility(name: str, declared: ToolVisibility) -> ToolVisibility:
    """把「静态基准可见性」解析成「当前环境下的实际可见性」。

    D2 开关只影响已收敛工具（`LEGACY_CONVERGED_TOOLS`）：保留时它们是 `BOTH`，
    关闭时降为 `CONSOLE_ONLY`（独立 Tool 面隐藏、console 转发面保留）。其余工具
    原样返回。注册表与暴露层都调用本函数，保证「同一个开关只有一处解释」。
    """
    if (
        declared == ToolVisibility.BOTH
        and name in LEGACY_CONVERGED_TOOLS
        and not keep_legacy_admin_tools()
    ):
        return ToolVisibility.CONSOLE_ONLY
    return declared


#: console 命令表声明的全部转发目标（静态集合，用于推导基准可见性）。
_CONSOLE_FORWARD_TARGETS: frozenset[str] = frozenset(
    spec.legacy_tool for spec in COMMAND_SPECS if spec.legacy_tool
)


def _declared_visibility(name: str) -> ToolVisibility:
    """静态基准：console 会转发的工具 = BOTH，其余 = TOOL_ONLY。"""
    return ToolVisibility.BOTH if name in _CONSOLE_FORWARD_TARGETS else ToolVisibility.TOOL_ONLY


def load_func_tools() -> dict[str, FuncTool]:
    global _func_tools
    _registry: dict[str, Any] = {
        "get_time": get_time,
        "web_search": web_search,
        "webfetch": webfetch,
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
        "memory_list": memory_list,
        "memory_read": memory_read,
        "memory_search": memory_search,
        "memory_create": memory_create,
        "memory_update": memory_update,
        # 管理控制台（#154）：16 个管理工具收敛成这一条，与它们并存（D2 默认保留）。
        # 注册名固定 `console`，schema 由 `console()` 的 docstring 生成。
        "console": console,
    }
    _func_tools = {}
    for name, func in _registry.items():
        _func_tools[name] = FuncTool(name, func, visibility=_declared_visibility(name))
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
    "update_container_note",
)

#: 容器工具组的**基准可见性**（不在此表里的一律 `TOOL_ONLY`，即随容器工具组整体
#: 暴露给 TspAgentDriver 的模型面）。
#:
#: `update_container_note` 是 `CONSOLE_ONLY`（Operator 2026-11-13 需求）：它由
#: `container note` 子命令转发执行，模型不该直接看见 —— 修改容器说明是**人**在
#: 控制台做的整理动作，让模型自己也去改，等于给它一个随时能篡改自己「用途说明」
#: 的口子（说明是给人看的历史信息，不是模型可自选的标签）。
#:
#: 显式声明而不是靠 `_declared_visibility`：后者只对 `_func_tools` 生效，容器工具组
#: 走的是另一条装配路径，靠上去会静默退回 `TOOL_ONLY`（即模型可见），与本需求正好
#: 相反 —— 这种「安全默认值恰好是最危险值」的地方必须显式写出来。
_CONTAINER_TOOL_VISIBILITY: dict[str, ToolVisibility] = {
    "update_container_note": ToolVisibility.CONSOLE_ONLY,
}

_container_func_tools: dict[str, FuncTool] = {}


def load_container_func_tools() -> dict[str, FuncTool]:
    """加载容器工具（`startup` 时一并构建，避免每次 `get_tools` 现造）。"""
    global _container_func_tools
    _container_func_tools = {
        name: FuncTool(
            name,
            globals()[name],
            visibility=_CONTAINER_TOOL_VISIBILITY.get(name, ToolVisibility.TOOL_ONLY),
        )
        for name in CONTAINER_TOOL_NAMES
    }
    return _container_func_tools


def get_func_tool(name: str) -> FuncTool | None:
    # 执行时按名字查找必须覆盖两组工具：容器工具只是「默认不暴露」给
    # Native / Claude SDK，并不代表它们不能被 TSP driver 执行。
    return _func_tools.get(name) or _container_func_tools.get(name)


#: 需要「按参数」而非「按注册名」判定是否自中断的工具（#154 D6 引入的机制）。
#: `reload_team` 仍是静态自中断（driver 里直接传 `self_interrupt=True`），所以不在表里；
#: 这里只登记参数相关的，避免把每次调用都变成一次 predicate 调用。
_SELF_INTERRUPT_PREDICATES: dict[str, Any] = {
    "console": console_triggers_self_interrupt,
}


def get_self_interrupt_predicate(name: str):
    """返回该工具的动态自中断判定器；没有则返回 None。

    三个 driver 注册本地工具时统一用
    `funcToolService.get_self_interrupt_predicate(function_name)` 取值，
    避免判定口径在三个 driver 里各抄一份后漂移。
    """
    return _SELF_INTERRUPT_PREDICATES.get(name)


def _reset() -> None:
    """纯资源清理：正常 shutdown 与启动失败回滚共用；幂等。"""
    _func_tools.clear()
    _container_func_tools.clear()


@serviceUtil.service_start_wrapper(s_status, on_error=_reset)
async def startup() -> None:
    """加载启用的函数列表，须在首次调用 get_tools 前调用一次。"""
    load_func_tools()
    load_container_func_tools()


def get_all_tools(include_container_tools: bool = False) -> list[llmApiUtil.OpenAITool]:
    """返回**全量注册面**：所有 `visibility != NONE` 的工具 schema。

    不受 D2 开关影响——开关只决定「模型能不能直接看到/调用」，不影响「注册表里
    有没有这个工具」。三个 Driver 都用本函数注册，`console` 的第 2 跳因此总能找到
    转发目标（#166-A）。模型暴露面请用 `get_tools()`。

    Args:
        include_container_tools: 是否包含容器沙箱工具。
    """
    tools = [tool for tool in _func_tools.values() if tool.visibility != ToolVisibility.NONE]
    if include_container_tools:
        tools.extend(
            tool for tool in _container_func_tools.values()
            if tool.visibility != ToolVisibility.NONE
        )
    return build_tools(tools)


def get_tools(include_container_tools: bool = False) -> list[llmApiUtil.OpenAITool]:
    """返回**模型暴露面**：当前 `tool_visible` 的工具列表（受 D2 开关影响）。

    被 `console` 收敛的原 16 个工具在开关关闭时动态解析为 `CONSOLE_ONLY`，不再出现
    在这里；但它们在注册表里仍然存在，console 转发的第 2 跳只要求 `console_visible`。

    Args:
        include_container_tools: 是否包含容器沙箱工具。默认 False——只有
            `TspAgentDriver` 需要（容器工具要切换 gtsp 执行环境），Native /
            Claude SDK 暴露出来只会误导模型（§6.1 生效范围）。
    """
    return [
        tool for tool in get_all_tools(include_container_tools=include_container_tools)
        if resolve_effective_visibility(tool.function.name, tool.visibility).tool_visible
    ]


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
    function_args: str | dict[str, Any],
    context: Optional[ToolCallContext] = None,
) -> dict[str, Any]:
    """执行本地工具，返回结果字典。

    入参 `function_args` 有两种形态：
    - 模型侧调用传 **JSON 字符串**（`str`）；
    - console 转发（第 2 跳）传**已组装好的 dict**，跳过 JSON 往返。

    这是**本地工具的唯一执行收口**（三个 driver 都把本函数注册为 handler），因此
    schema 校验与内容错误（JSON 坏、参数类型不符）都在这里统一发生一次：

    1. `json.loads` 失败（仅 str 入参）⇒ 结构化失败返回体（#189 之前是**静默
       `args = {}` 继续执行**，坏 JSON 会让全可选参数的工具拿默认值真的跑起来）；
    2. 参数形状/类型/枚举/必填按 `get_function_metadata()` 的 schema 校验 —— schema
       与下发给模型的是同一份产出（单一事实源），失败返回字段级错误 + `error_code`；
    3. 通过后才注入 `_context` 并调用函数。
    """
    function_name = context.tool_name if context is not None else ""
    if not function_name:
        logger.error("函数执行失败: tool_name 为空")
        return {"success": False, "message": "函数执行失败: tool_name 为空"}

    if isinstance(function_args, dict):
        # 转发路径：浅拷贝，避免下游注入 `_context` 时改到转发方的 dict。
        args: dict = dict(function_args)
    else:
        try:
            args = json.loads(function_args)
        except json.JSONDecodeError as e:
            logger.warning(
                f"工具参数 JSON 解析失败，拒绝执行: tool={function_name}, args={function_args!r}"
            )
            return invalid_json_args(function_name, function_args, e)

    caller = context.agent_id if context is not None else "unknown"
    logger.info(f"use_tool: caller_id={caller}, tool={function_name}, args={args}")

    try:
        func_tool = get_func_tool(function_name)
        func = func_tool.callable if func_tool is not None else None

        if func is None:
            raise ValueError(f"Function {function_name} not found")

        if not callable(func):
            raise ValueError(f"{function_name} is not callable")

        # 校验在**取到 func 之后**：本地工具的 schema 来源就是它的签名，没有第二份。
        invalid = validate_tool_args(function_name, args, func=func)
        if invalid is not None:
            logger.warning(
                f"工具参数校验未通过: tool={function_name}, error_code={invalid.get('error_code')}, "
                f"args={args!r}"
            )
            return invalid

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

    except ForwardToolCall:
        # 转发信号必须原样上抛（#160）：它是控制流，不是失败。
        # 这一层是 `run_tool_call` 的兜底 `except Exception`，不先放行就会把
        # `console` 抛出的 `ForwardToolCall` 转成「函数执行失败」返回体，
        # 统一入口 `execute_tool_call` 再也看不到它 —— 整个转发设计会静默失效。
        raise
    except Exception as e:
        if isinstance(e, TypeError):
            error = f"Invalid arguments for function {function_name}: {e}"
        else:
            # `TogoException.__str__` 只吐 error_message（不带 error_code 的元组 repr）。
            error = str(e)

        # 日志用 `!r`：`str(e)` 现在是干净的业务消息，error_code 只留在 `args` /
        # `repr` 里；用 `{e}` 记日志会把错误码一起丢掉，排查时无法按码 grep。
        logger.error(f"函数执行失败: {e!r}")
        # 返回体形状与 error_code 口径收敛到 `toolResult.error_result`（#189：唯一实现）；
        # 前缀 `函数执行失败: ` 是既有对外文案，保持不变。
        return error_result(e, prefix="函数执行失败: ", message=error)


@serviceUtil.service_stop_wrapper(s_status)
def shutdown() -> None:
    """清空工具列表，程序退出前调用。"""
    _reset()

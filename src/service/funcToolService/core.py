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
from . import toolSpec
from .toolSpec import (
    ENV_KEEP_LEGACY_TOOLS,
    keep_legacy_admin_tools,
    resolve_effective_visibility,
)
from .toolArgsValidation import validate_tool_args
from toolResult import ensure_error_code, error_result, invalid_json_args
from .consoleTool import console, console_triggers_self_interrupt
from .funcToolType import FuncTool
from . import tools as _tools  # noqa: F401 —— import 副作用：触发 49 个 @tool(...) 声明

logger = logging.getLogger(__name__)

s_status = ServiceStatusInfo(name="funcToolService")


def build_tools(func_tools: Iterable[FuncTool]) -> list[llmApiUtil.OpenAITool]:
    """遍历 FuncTool 定义，构建并返回工具列表。"""
    return [func_tool.to_openai_tool() for func_tool in func_tools]


_func_tools: dict[str, FuncTool] = {}
# 容器工具单独一组（`ToolSpec.group == "container"`），与默认组同源装配、物理分离存放。
_container_func_tools: dict[str, FuncTool] = {}


def declared_visibility(name: str) -> ToolVisibility:
    """某个已声明工具的静态基准可见性（未声明按 `NONE`，即「谁也别想调」）。

    可见性现在由工具自己声明（`@tool(visibility=...)`），这里只做查表 + fail-closed：
    查不到就返回 `NONE`，而不是退回「BOTH」——「漏声明」变成「调不动」比
    「漏声明」变成「意外全开」安全。
    """
    spec = toolSpec.get_declared_spec(name)
    return spec.visibility if spec is not None else ToolVisibility.NONE


def container_tool_names() -> tuple[str, ...]:
    """容器组工具名（由 `ToolSpec.group == "container"` 派生，取代手写 `CONTAINER_TOOL_NAMES`）。"""
    return tuple(
        declared.name for declared in toolSpec.declared_tools() if declared.spec.group == "container"
    )


def _assemble(func_tools: Iterable[FuncTool]) -> tuple[dict[str, FuncTool], dict[str, FuncTool]]:
    """把 `FuncTool` 按 `ToolSpec.group` 分成「默认」与「容器」两组（物理分离不变）。

    分组的理由与之前一致：`get_tools()` 默认不带容器工具，Native / Claude SDK 的暴露面
    零变化；容器工具只对 TspAgentDriver 暴露（§6.1 生效范围）。
    """
    default: dict[str, FuncTool] = {}
    container: dict[str, FuncTool] = {}
    for func_tool in func_tools:
        spec = func_tool.spec
        group = spec.group if spec is not None else "default"
        (container if group == "container" else default)[func_tool.name] = func_tool
    return default, container


def _declared_func_tools() -> list[FuncTool]:
    """按 `toolSpec` 收集列表装配 `FuncTool`（**唯一**装配来源）。"""
    return [
        FuncTool(
            name=declared.name,
            callable=declared.func,
            visibility=declared.spec.visibility,
            spec=declared.spec,
        )
        for declared in toolSpec.declared_tools()
    ]


def load_func_tools() -> dict[str, FuncTool]:
    global _func_tools, _container_func_tools
    _func_tools, _container_func_tools = _assemble(_declared_func_tools())
    return _func_tools


def load_container_func_tools() -> dict[str, FuncTool]:
    """加载容器工具（`startup` 时一并构建，避免每次 `get_tools` 现造）。"""
    global _func_tools, _container_func_tools
    if not _func_tools and not _container_func_tools:
        load_func_tools()
    return _container_func_tools


def get_func_tool(name: str) -> FuncTool | None:
    # 执行时按名字查找必须覆盖两组工具：容器工具只是「默认不暴露」给
    # Native / Claude SDK，并不代表它们不能被 TSP driver 执行。
    return _func_tools.get(name) or _container_func_tools.get(name)


def get_self_interrupt_predicate(name: str):
    """返回该工具的动态自中断判定器；没有则返回 None。

    三个 driver 注册本地工具时统一用
    `funcToolService.get_self_interrupt_predicate(function_name)` 取值，避免判定口径
    在三个 driver 里各抄一份后漂移。判定器来自工具自己的声明
    （`ToolSpec.self_interrupt_predicate`），不再是按名字索引的第二张表。
    """
    func_tool = get_func_tool(name)
    spec = func_tool.spec if func_tool is not None else None
    return spec.self_interrupt_predicate if spec is not None else None


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

    被 `console` 收敛的 28 个 `BOTH` 工具在开关关闭时动态解析为 `CONSOLE_ONLY`，不再出现
    在这里；但它们在注册表里仍然存在，console 转发的第 2 跳只要求 `console_visible`。

    Args:
        include_container_tools: 是否包含容器沙箱工具。默认 False——只有
            `TspAgentDriver` 需要（容器工具要切换 gtsp 执行环境），Native /
            Claude SDK 暴露出来只会误导模型（§6.1 生效范围）。
    """
    return [
        tool for tool in get_all_tools(include_container_tools=include_container_tools)
        if resolve_effective_visibility(tool.visibility).tool_visible
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
    """执行本地工具，返回结果字典（**带出口兜底：失败体必须有 `error_code`**）。

    本函数是三个 driver 共同注册的 handler，因此也是本地工具的**唯一出口**；
    兜底放在出口而不是散在各处 return 上，理由同 `toolResult` 的整体设计：
    手写失败字典有 200+ 处，逐处补齐必漏。

    - 失败体（`success is False`）缺码 ⇒ 补通用码 `operation_failed`；
    - **已有码一律不覆盖**（`invalid_tool_args` / `invalid_json_args` / 业务码都不动）；
    - 成功体不动。详见 `toolResult.ensure_error_code`。
    """

    result = await _dispatch_tool_call(function_args, context)
    tool_name = context.tool_name if context is not None else ""
    return ensure_error_code(result, where=tool_name)


async def _dispatch_tool_call(
    function_args: str | dict[str, Any],
    context: Optional[ToolCallContext] = None,
) -> dict[str, Any]:
    """`run_tool_call` 的实际执行体（校验 + 调用 + 异常转换）。

    入参 `function_args` 有两种形态：
    - 模型侧调用传 **JSON 字符串**（`str`）；
    - console 转发（第 2 跳）传**已组装好的 dict**，跳过 JSON 往返。

    三个 driver 都把 `run_tool_call` 注册为 handler，所有本地工具调用都从那里进来，
    因此 schema 校验与内容错误（JSON 坏、参数类型不符）在这里统一发生一次
    （出口兜底在 `run_tool_call` 里，不在本函数）：

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

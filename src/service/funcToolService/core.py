import inspect
import json
import logging
from typing import Any, Callable, List, Optional

from util import llm_api_util
from model.coreModel.gtCoreChatContext import ChatContext
from .tools import FUNCTION_REGISTRY
from .toolLoader import build_tools

logger = logging.getLogger(__name__)

_tools: list[llm_api_util.Tool] = []


async def startup() -> None:
    """加载启用的函数列表并构建工具，须在首次调用 get_tools 前调用一次。"""
    global _tools
    _tools = build_tools(FUNCTION_REGISTRY)


def get_tools() -> list[llm_api_util.Tool]:
    """返回已初始化的工具列表。"""
    return _tools


async def run_tool_call(
    function_name: str,
    function_args: str,
    context: Optional[ChatContext] = None,
) -> str:
    """解析 function_args JSON 字符串并执行函数，返回结果字符串。"""
    try:
        args: dict = json.loads(function_args)
    except json.JSONDecodeError:
        args = {}

    caller = context.agent_name if context is not None else "unknown"
    logger.info(f"use_tool: caller={caller}, tool={function_name}, args={args}")

    try:
        func: Callable[..., Any] | None = FUNCTION_REGISTRY.get(function_name)

        if func is None:
            raise ValueError(f"Function {function_name} not found")

        if not callable(func):
            raise ValueError(f"{function_name} is not callable")

        if context and "_context" in inspect.signature(func).parameters:
            args = {**args, "_context": context}

        result = func(**args)

        if inspect.isawaitable(result):
            result = await result

        result = json.dumps(result, ensure_ascii=False)
        logger.info(f"函数执行结果: {result}")
        return result

    except Exception as e:
        if isinstance(e, TypeError):
            error = f"Invalid arguments for function {function_name}: {e}"
        else:
            error = str(e)

        logger.error(f"函数执行失败: {e}")
        return json.dumps({"success": False, "message": f"函数执行失败: {error}"}, ensure_ascii=False)


def shutdown() -> None:
    """清空工具列表，程序退出前调用。"""
    global _tools
    _tools = []

import inspect
import json
import logging
from typing import List, Optional

from util.llm_api_util import Tool
from model.chat_context import ChatContext
from .tools import FUNCTION_REGISTRY
from .tool_loader import build_tools

_tools: List[Tool] = []


def init() -> None:
    """加载启用的函数列表并构建工具，须在首次调用 get_tools 前调用一次。"""
    global _tools
    _tools = build_tools(FUNCTION_REGISTRY)


def get_tools() -> List[Tool]:
    """返回已初始化的工具列表。"""
    return _tools


def close() -> None:
    """清空工具列表，程序退出前调用。"""
    global _tools
    _tools = []


def run_tool_call(
    function_name: str,
    function_args: str,
    context: Optional[ChatContext] = None,
) -> str:
    """解析 function_args JSON 字符串并执行函数，返回结果字符串。"""
    try:
        args: dict = json.loads(function_args)
    except json.JSONDecodeError:
        args = {}

    logging.getLogger(__name__).info(f"调用函数: {function_name}, 参数: {args}")
    try:
        result = execute_function(function_name, args, context=context)
        logging.getLogger(__name__).info(f"函数执行结果: {result}")
        return result
    except Exception as e:
        logging.getLogger(__name__).error(f"函数执行失败: {e}")
        return f"函数执行失败: {str(e)}"


def execute_function(func_name: str, args: dict, context: Optional[ChatContext] = None) -> str:
    """动态调用指定函数"""
    try:
        func = FUNCTION_REGISTRY.get(func_name)
        if func is None:
            raise ValueError(f"Function {func_name} not found")

        if not callable(func):
            raise ValueError(f"{func_name} is not callable")

        if getattr(func, "needs_context", False) and context:
            if "_context" in inspect.signature(func).parameters:
                args = {**args, "_context": context}

        result = func(**args)
        return str(result)

    except ValueError:
        raise
    except TypeError as e:
        raise ValueError(f"Invalid arguments for function {func_name}: {e}")
    except Exception as e:
        raise RuntimeError(f"Error executing function {func_name}: {e}")

import inspect
import logging
from typing import List, Optional

from model.llm_api_model import Tool, Function, FunctionParameter
from model.chat_context import ChatContext
from util.tool_loader_util import get_function_metadata
from util.tool_util import FUNCTION_REGISTRY

_tools: List[Tool] = []


def init() -> None:
    """加载启用的函数列表并构建工具，须在首次调用 get_tools 前调用一次。"""
    global _tools
    _tools = []

    for func_name, func in FUNCTION_REGISTRY.items():
        try:
            metadata = get_function_metadata(func_name, func)
            tool = Tool(
                function=Function(
                    name=metadata["name"],
                    description=metadata["description"],
                    parameters=FunctionParameter(
                        type=metadata["parameters"]["type"],
                        properties=metadata["parameters"]["properties"],
                        required=metadata["parameters"].get("required", [])
                    )
                )
            )
            _tools.append(tool)
            logging.info(f"Loaded function: {func_name}")

        except Exception as e:
            logging.error(f"Error loading function {func_name}: {e}")


def get_tools() -> List[Tool]:
    """返回已初始化的工具列表。"""
    return _tools


def close() -> None:
    """清空工具列表，程序退出前调用。"""
    global _tools
    _tools = []


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

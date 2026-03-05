import logging
from typing import List

from model.api_model import Tool, Function, FunctionParameter
from util.function_loader_util import load_enabled_functions, get_function_metadata
from util.functions_util import FUNCTION_REGISTRY


def build_tools() -> List[Tool]:
    """构建工具列表"""
    enabled_functions = load_enabled_functions()
    tools = []

    for func_name in enabled_functions:
        try:
            func = FUNCTION_REGISTRY.get(func_name)
            if func is None or not callable(func):
                logging.warning(f"Function {func_name} not found or not callable")
                continue

            metadata = get_function_metadata(func_name, func)

            param_properties = metadata["parameters"]["properties"]
            param_required = metadata["parameters"].get("required", [])

            function_param = FunctionParameter(
                type=metadata["parameters"]["type"],
                properties=param_properties,
                required=param_required
            )

            tool = Tool(
                function=Function(
                    name=metadata["name"],
                    description=metadata["description"],
                    parameters=function_param
                )
            )
            tools.append(tool)
            logging.info(f"Loaded function: {func_name}")

        except Exception as e:
            logging.error(f"Error loading function {func_name}: {e}")

    return tools


def execute_function(func_name: str, args: dict, context: dict = None) -> str:
    """动态调用指定函数"""
    try:
        func = FUNCTION_REGISTRY.get(func_name)
        if func is None:
            raise ValueError(f"Function {func_name} not found")

        if not callable(func):
            raise ValueError(f"{func_name} is not callable")

        if getattr(func, "needs_context", False) and context:
            args = {**args, "_chat_room": context.get("chat_room"), "_agent_name": context.get("agent_name")}

        result = func(**args)
        return str(result)

    except ValueError:
        raise
    except TypeError as e:
        raise ValueError(f"Invalid arguments for function {func_name}: {e}")
    except Exception as e:
        raise RuntimeError(f"Error executing function {func_name}: {e}")

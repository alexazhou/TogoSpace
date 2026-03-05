import inspect
import json
import logging
from typing import Any, Dict, List, get_type_hints, get_origin, get_args, Literal
from types import UnionType
from typing import Union
from model import Tool, Function, FunctionParameter

# 导入所有可用函数
from tools.functions import *


def load_enabled_functions() -> List[str]:
    """读取 function_list.json，返回启用的函数名列表"""
    try:
        with open("../resource/bk/function_list.json", "r", encoding="utf-8") as f:
            config = json.load(f)
        return config.get("enabled_functions", [])
    except FileNotFoundError:
        logging.warning("function_list.json not found, no functions enabled")
        return []
    except json.JSONDecodeError as e:
        logging.error(f"Error parsing function_list.json: {e}")
        return []


def python_type_to_json_schema(python_type: Any) -> Dict[str, Any]:
    """将 Python 类型转换为 JSON Schema 类型定义

    Args:
        python_type: Python 类型对象

    Returns:
        JSON Schema 类型定义字典
    """
    # 处理 Optional[T] 或 Union[T, None]
    if get_origin(python_type) in (Union, UnionType):
        args = get_args(python_type)
        if len(args) == 2 and type(None) in args:
            # Optional[T] = Union[T, None]
            non_none_type = args[0] if args[1] is type(None) else args[1]
            return python_type_to_json_schema(non_none_type)
        return {"type": "object"}

    # 处理 Literal["a", "b", ...]
    if get_origin(python_type) is Literal:
        return {"enum": list(get_args(python_type))}

    # 处理基本类型
    type_mapping = {
        str: {"type": "string"},
        int: {"type": "integer"},
        float: {"type": "number"},
        bool: {"type": "boolean"},
        list: {"type": "array"},
        dict: {"type": "object"},
    }

    for py_type, schema in type_mapping.items():
        if python_type is py_type:
            return schema

    # 默认返回 object
    return {"type": "object"}


def get_function_metadata(func_name: str, func) -> Dict[str, Any]:
    """使用 inspect 模块提取函数元数据

    Args:
        func_name: 函数名称
        func: 函数对象

    Returns:
        符合 OpenAI Function 格式的字典
    """
    # 获取函数签名
    sig = inspect.signature(func)

    # 获取类型注解
    try:
        type_hints = get_type_hints(func)
    except Exception:
        type_hints = {}

    # 解析 docstring
    docstring = inspect.getdoc(func) or ""
    description = docstring.split("\n")[0].strip()

    # 解析参数说明（从 docstring 的 Args 部分）
    param_descriptions = {}
    if "Args:" in docstring:
        args_section = docstring.split("Args:")[1].split("\n\n")[0]
        for line in args_section.strip().split("\n"):
            line = line.strip()
            if line.startswith("-") or ":" in line:
                # 处理格式: "param: description" 或 "- param: description"
                parts = line.split(":", 1)
                if len(parts) == 2:
                    param_name = parts[0].lstrip("- ").strip().split()[0]
                    param_descriptions[param_name] = parts[1].strip()

    # 构建参数属性
    properties = {}
    required = []

    for param_name, param in sig.parameters.items():
        # 跳过 self 参数和私有注入参数（以 _ 开头）
        if param_name == "self" or param_name.startswith("_"):
            continue

        # 获取参数类型
        param_type = type_hints.get(param_name, str)

        # 构建 JSON Schema 类型
        schema = python_type_to_json_schema(param_type)

        # 添加描述
        if param_name in param_descriptions:
            schema["description"] = param_descriptions[param_name]

        # 检查是否是必需参数
        if param.default == inspect.Parameter.empty:
            required.append(param_name)

        properties[param_name] = schema

    # 构建完整的函数元数据
    metadata = {
        "name": func_name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required
        }
    }

    return metadata


def build_tools() -> List[Tool]:
    """构建工具列表

    Returns:
        Tool 对象列表
    """
    enabled_functions = load_enabled_functions()
    tools = []

    for func_name in enabled_functions:
        try:
            # 从模块获取函数对象
            func = globals().get(func_name)
            if func is None or not callable(func):
                logging.warning(f"Function {func_name} not found or not callable")
                continue

            # 提取元数据
            metadata = get_function_metadata(func_name, func)

            # 构建参数对象
            param_properties = metadata["parameters"]["properties"]
            param_required = metadata["parameters"].get("required", [])

            function_param = FunctionParameter(
                type=metadata["parameters"]["type"],
                properties=param_properties,
                required=param_required
            )

            # 构建 Tool 对象
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
    """动态调用指定函数

    Args:
        func_name: 函数名称
        args: 参数字典
        context: 可选的上下文字典，包含 chat_room 和 agent_name

    Returns:
        函数执行结果的字符串表示
    """
    try:
        # 从模块获取函数对象
        func = globals().get(func_name)
        if func is None:
            raise ValueError(f"Function {func_name} not found")

        if not callable(func):
            raise ValueError(f"{func_name} is not callable")

        # 为 send_chat_msg 注入上下文参数
        if func_name == "send_chat_msg" and context:
            args = {**args, "_chat_room": context.get("chat_room"), "_agent_name": context.get("agent_name")}

        # 调用函数
        result = func(**args)

        # 确保返回字符串
        return str(result)

    except ValueError:
        raise
    except TypeError as e:
        raise ValueError(f"Invalid arguments for function {func_name}: {e}")
    except Exception as e:
        raise RuntimeError(f"Error executing function {func_name}: {e}")

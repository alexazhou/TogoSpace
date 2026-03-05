import inspect
from typing import Any, Dict, List, get_type_hints, get_origin, get_args, Literal
from types import UnionType
from typing import Union


def python_type_to_json_schema(python_type: Any) -> Dict[str, Any]:
    """将 Python 类型转换为 JSON Schema 类型定义"""
    # 处理 Optional[T] 或 Union[T, None]
    if get_origin(python_type) in (Union, UnionType):
        args = get_args(python_type)
        if len(args) == 2 and type(None) in args:
            non_none_type = args[0] if args[1] is type(None) else args[1]
            return python_type_to_json_schema(non_none_type)
        return {"type": "object"}

    # 处理 Literal["a", "b", ...]
    if get_origin(python_type) is Literal:
        return {"enum": list(get_args(python_type))}

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

    return {"type": "object"}


def get_function_metadata(func_name: str, func) -> Dict[str, Any]:
    """使用 inspect 模块提取函数元数据"""
    sig = inspect.signature(func)

    try:
        type_hints = get_type_hints(func)
    except Exception:
        type_hints = {}

    docstring = inspect.getdoc(func) or ""
    description = docstring.split("\n")[0].strip()

    param_descriptions = {}
    if "Args:" in docstring:
        args_section = docstring.split("Args:")[1].split("\n\n")[0]
        for line in args_section.strip().split("\n"):
            line = line.strip()
            if line.startswith("-") or ":" in line:
                parts = line.split(":", 1)
                if len(parts) == 2:
                    param_name = parts[0].lstrip("- ").strip().split()[0]
                    param_descriptions[param_name] = parts[1].strip()

    properties = {}
    required = []

    for param_name, param in sig.parameters.items():
        if param_name == "self" or param_name.startswith("_"):
            continue

        param_type = type_hints.get(param_name, str)
        schema = python_type_to_json_schema(param_type)

        if param_name in param_descriptions:
            schema["description"] = param_descriptions[param_name]

        if param.default == inspect.Parameter.empty:
            required.append(param_name)

        properties[param_name] = schema

    return {
        "name": func_name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required
        }
    }

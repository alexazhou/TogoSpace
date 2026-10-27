from __future__ import annotations

import inspect
import logging
import re
from dataclasses import dataclass
from types import UnionType
from typing import Any, Callable, Dict, Literal, Union, get_args, get_origin, get_type_hints

from util import llmApiUtil

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# docstring → schema 解析（Args 段状态机）
# ---------------------------------------------------------------------------
# 说明：旧实现用 `docstring.split("Args:")` + 「段内首个空行截断」，导致 5 类写法失效：
#   ① 用「参数：」当段标题；② 正文提到 `Args:` 字面串；③ 段内出现空行；
#   ④ 用 `Arguments:` / `Parameters:`；⑤ 全角冒号。
# 现改为状态机：整行匹配段标题 + 段内空行不终止 + 续行拼接。

# 参数段标题（小写归一化后比对；半角 / 全角冒号都认）。
_ARGS_SECTION_TITLES = frozenset({
    "args",
    "arguments",
    "parameters",
    "参数",
    "参数说明",
    "参数列表",
})

# 「整行仅标题 + 冒号」才算段标题（Returns: / Raises: / Notes: / Examples: / Yields: /
# Attributes: / 参数： 等）；这样正文里提到 `Args:` 不会劫持参数段。
_SECTION_TITLE_PATTERN = re.compile(
    r"^(?P<title>[A-Za-z][A-Za-z0-9 _\-/]{0,23}|[\u4e00-\u9fff]{1,10})\s*[:：]\s*$"
)

# 参数行：`name: 说明` / `name (类型): 说明` / 全角括号 + 全角冒号 / 允许前导 `- `。
_PARAM_LINE_PATTERN = re.compile(
    r"^(?P<name>[A-Za-z_]\w*)\s*(?:\([^)]*\)|（[^）]*）)?\s*[:：]\s*(?P<desc>.*)$"
)


def _line_indent(raw_line: str) -> int:
    return len(raw_line) - len(raw_line.lstrip())


def _strip_bullet(line: str) -> str:
    """去掉列表符号（`- `/`* `/`• `）与前导空白。"""
    return line.lstrip("-*• \t").strip()


def _match_section_title(raw_line: str) -> str | None:
    """整行匹配段标题并返回归一化标题（小写）；非标题返回 None。"""
    matched = _SECTION_TITLE_PATTERN.match(raw_line.strip())
    return matched.group("title").strip().lower() if matched else None


def _extract_description(docstring: str) -> str:
    """取 docstring **首段原文**（保留段内换行，不拼接），不再只取首行。

    口径（Operator 2026-10-26 拍板）：description = 首段，多行原样保留换行
    （JSON 里 `\\n` 合法，OpenAI / Anthropic 的 function description 都吃多行）；
    **不得**把 `Args:` 之后的段（Returns / Notes 等）也塞进来——细节契约放各参数的
    description，重要但不属于某个参数的散文并进首段。
    """
    parts: list[str] = []
    for raw_line in docstring.split("\n"):
        stripped = raw_line.strip()
        if not stripped:
            if parts:
                break
            continue
        if _match_section_title(raw_line) is not None:
            break
        parts.append(stripped)
    return "\n".join(parts).strip()


def _parse_param_descriptions(docstring: str, valid_names: set[str]) -> dict[str, str]:
    """解析「参数段」→ {参数名: 说明}，只认签名内的参数名。

    规则：
    1. 段标题**整行**匹配（Args/Arguments/Parameters/参数/参数说明/参数列表，半角全角冒号）；
    2. 段内**空行不终止**；
    3. 参数行按 `_PARAM_LINE_PATTERN` 解析，允许前导 `- `、类型括号、全角冒号；
       但**只有 `name ∈ valid_names`（函数签名里的非私有参数）才算参数行**——
       否则 `NOTE: xxx` / `TODO: xxx` 这类英文标识符行会顶替「当前参数」，
       把后续续行抢走、并让该行自身的正文变成幽灵键后被丢弃；
    4. 不匹配「合法参数行」的段内内容视为**续行**，用空格拼接到上一个参数的说明
       （保留 `NOTE:` 等前缀原文，不抹掉）；
    5. 段首/无当前参数时的续行直接忽略（不抛错、不产生幽灵键）；
    6. 遇下一个段标题（整行标题）或缩进回退到段标题同级/更浅的非空行 ⇒ 参数段结束
       （因此一个 docstring 只解析**第一个**参数段）。
    """
    descriptions: dict[str, str] = {}
    in_args_section = False
    args_indent = 0
    current_param: str | None = None

    for raw_line in docstring.split("\n"):
        stripped = raw_line.strip()

        if not in_args_section:
            title = _match_section_title(raw_line)
            if title is not None and title in _ARGS_SECTION_TITLES:
                in_args_section = True
                args_indent = _line_indent(raw_line)
                current_param = None
            continue

        # ---- 已在参数段内 ----
        if not stripped:  # 段内空行不终止
            continue

        matched = _PARAM_LINE_PATTERN.match(_strip_bullet(stripped))
        if matched and matched.group("name") in valid_names:
            current_param = matched.group("name")
            descriptions[current_param] = matched.group("desc").strip()
            continue

        if _line_indent(raw_line) <= args_indent:
            # 下一个段标题，或缩进回退到同级/更浅 ⇒ 参数段结束
            break

        if current_param:  # 续行：拼接到上一个参数（含 `NOTE:` 等冒名行的原文）
            extra = _strip_bullet(stripped)
            descriptions[current_param] = f"{descriptions[current_param]} {extra}".strip()
        else:
            # 段首就是无主续行：忽略，不制造幽灵键
            logger.debug(f"参数段内出现无主续行，已忽略: {stripped[:40]}")

    return descriptions


def python_type_to_json_schema(python_type: Any) -> Dict[str, Any]:
    """将 Python 类型转换为 JSON Schema 类型定义"""
    # 处理 Optional[T] 或 Union[T, None]
    if get_origin(python_type) in (Union, UnionType):
        args: tuple[Any, ...] = get_args(python_type)
        if len(args) == 2 and type(None) in args:
            non_none_type = args[0] if args[1] is type(None) else args[1]
            return python_type_to_json_schema(non_none_type)
        return {"type": "object"}

    # 处理 Literal["a", "b", ...]
    if get_origin(python_type) is Literal:
        return {"enum": list(get_args(python_type))}

    origin = get_origin(python_type)
    if origin is list:
        args = get_args(python_type)
        if not args:
            raise TypeError(f"list 类型缺少元素类型注解，请使用 list[T] 形式")
        return {"type": "array", "items": python_type_to_json_schema(args[0])}
    if origin is dict:
        return {"type": "object"}

    type_mapping = {
        str: {"type": "string"},
        int: {"type": "integer"},
        float: {"type": "number"},
        bool: {"type": "boolean"},
        dict: {"type": "object"},
    }

    for py_type, schema in type_mapping.items():
        if python_type is py_type:
            return schema

    raise TypeError(f"不支持的工具参数类型: {python_type}，请使用 str/int/float/bool/dict/list[T]")


def get_function_metadata(func_name: str, func: Callable[..., Any]) -> Dict[str, Any]:
    """使用 inspect 模块提取函数元数据"""
    sig = inspect.signature(func)

    try:
        type_hints: Dict[str, Any] = get_type_hints(func)
    except Exception as e:
        logger.warning(f"获取函数类型提示失败，已忽略: func={func_name}, error={e}")
        type_hints = {}

    docstring = inspect.getdoc(func) or ""
    description = _extract_description(docstring)
    # 只有签名里的非私有参数名才算「参数行」，避免 `NOTE:` 之类的冒名行顶替当前参数。
    valid_param_names = {
        name for name in sig.parameters if name != "self" and not name.startswith("_")
    }
    param_descriptions = _parse_param_descriptions(docstring, valid_param_names)

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


@dataclass(frozen=True)
class FuncTool:
    name: str
    callable: Callable[..., Any]

    def to_openai_tool(self) -> llmApiUtil.OpenAITool:
        """将当前工具转换为 OpenAI 工具格式。"""
        metadata = get_function_metadata(self.name, self.callable)
        return llmApiUtil.OpenAITool(
            function=llmApiUtil.OpenAIFunction(
                name=metadata["name"],
                description=metadata["description"],
                parameters=llmApiUtil.OpenAIFunctionParameter(
                    type=metadata["parameters"]["type"],
                    properties=metadata["parameters"]["properties"],
                    required=metadata["parameters"].get("required", [])
                )
            ),
        )

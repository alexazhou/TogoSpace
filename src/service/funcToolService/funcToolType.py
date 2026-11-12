from __future__ import annotations

import inspect
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, get_type_hints

from pydantic import TypeAdapter

from constants import ToolVisibility
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


def _type_adapter_json_schema(python_type: Any) -> Dict[str, Any]:
    """把 Python 注解交给 pydantic `TypeAdapter` 产出 JSON Schema 片段。

    为什么不再手写映射（#195，Operator 2026-11-12 00:20 批准）：
    旧实现把 `Optional[T]` **剥成 T**（见下方 git 历史），于是
    「给了键、值是 null」这件事在 schema 里彻底丢失，校验层只能靠一条
    `_is_ignorable_null` 豁免兜住 —— 那条豁免同时把 19 个**注解不含 None**
    的字段（`create_task.description` 等）的 null 也放行了，最终一路撞到
    `NOT NULL constraint failed`。用 TypeAdapter 后 nullability 直接进 schema，
    豁免的前提消失（豁免已在本单删除）。

    两个例外保持原有语义（有既有用例钉住，不许静默变成别的东西）：
    - 裸 `list`：TypeAdapter 会给 `{"type":"array","items":{}}`，即「元素类型未知」，
      而本项目要求显式 `list[T]` ⇒ 仍抛 `TypeError`；
    - 无法建 schema 的类型：抛 `TypeError`（不静默兜底成 object）。
    """
    if python_type is list:
        raise TypeError("list 类型缺少元素类型注解，请使用 list[T] 形式")

    try:
        return TypeAdapter(python_type).json_schema()
    except Exception as error:  # noqa: BLE001 —— 统一收敛成本项目原有异常契约
        raise TypeError(
            f"不支持的工具参数类型: {python_type}，请使用 str/int/float/bool/dict/list[T]"
        ) from error


#: 子 schema 会出现在这些键下面；逐个递归，别对 `required` / `enum` / `default`
#: 这类「值不是 schema」的键做递归（否则会被当成 schema 误改）。
_SCHEMA_DICT_KEYS = ("items", "additionalProperties", "not")
_SCHEMA_LIST_KEYS = ("anyOf", "oneOf", "allOf")

#: 这些关键字即使实例是 `null` 也会被判定，因此**不能**扁平化
#: （扁平化把 `anyOf:[X, null]` 压成「X 的约束 + null」会把它们也套到 null 上）。
#: 实测：`Optional[Literal['TODO','ON_HOLD']]` 若扁平化 ⇒
#: `{"enum":["TODO","ON_HOLD"],"type":["string","null"]}`，
#: jsonschema 对 `null` 会先查 `enum`（null 不在其中）而报错 ⇒ 合法用法被误杀。
#: `type` / `items` / `properties` / `required` / `minimum` 等对 `null` 实例天然
#: 不适用（JSON Schema 按实例类型分派），所以可以扁平化。
_NULL_SENSITIVE_KEYS = frozenset({"enum", "const", "not", "anyOf", "oneOf", "allOf", "if", "patternProperties"})


def _flatten_nullable(schema: Dict[str, Any]) -> Dict[str, Any]:
    """`{"anyOf":[X, {"type":"null"}]}`（恰好一支纯 null）⇒ `X` 的 `type` 变 `[t, "null"]`。

    为什么扁平化而不是留着 `anyOf`（#195 实测）：
    - 体积：`anyOf` 原样 11871→13333B（+12.3%）；扁平化 12473B（+5.1%）；
    - 不引入结构嵌套，错误文案与 `_describe` 的既有形态一致。

    只在下列条件**全部**成立时扁平化，否则原样保留 `anyOf`：
    1. 恰好两支，其中一支是纯 `{"type": "null"}`；
    2. 另一支的 `type` 是单值字符串；
    3. 另一支不含 `_NULL_SENSITIVE_KEYS`（`enum` 等会对 null 也生效的约束）。

    条件 3 是实测补的：`Optional[Literal[...]]` 扁平化后 `enum` 会咬 `null`，
    `create_task.status` / `list_tasks.scope` 传 null 从「合法」变「被拒」——
    那是本单不该引入的误杀（反向判据 4.2 抓到的）。
    多分支 Union（`Optional[Union[str, list[str]]]` 是三分支）不满足条件 1，保留 `anyOf`。
    """
    any_of = schema.get("anyOf")
    if not isinstance(any_of, list) or len(any_of) != 2:
        return schema

    null_branches = [branch for branch in any_of if branch == {"type": "null"}]
    others = [branch for branch in any_of if branch != {"type": "null"}]
    if len(null_branches) != 1 or len(others) != 1:
        return schema

    inner = others[0]
    if not isinstance(inner, dict) or not isinstance(inner.get("type"), str):
        # 无 `type` 的分支（如 `Optional[Literal[...]]` 之外的形态）：保留 anyOf，别猜。
        return schema
    if _NULL_SENSITIVE_KEYS & set(inner):
        # `enum` 等会对 null 也生效 ⇒ 扁平化会让 null 被这些约束拒绝。
        return schema

    flattened = dict(inner)
    flattened["type"] = [inner["type"], "null"]
    return flattened


def _normalize_schema(node: Any) -> Any:
    """TypeAdapter 产物的两处规范化：`const` → `enum`、`anyOf` 形态的 null 扁平化。

    1. **`const` → `enum:[v]`**：TypeAdapter 对 `Literal['reviewer_id']` 产
       `{"const": "reviewer_id"}`，而 `_describe` 只会把 `enum` 译成
       「只能取 [...] 之一」，`const` 会退化成「不满足 const 约束」——错误文案降级。
       涉及参数：`update_task.clear_fields`（`list[Literal['reviewer_id']]`）。
    2. **null 扁平化**：见 `_flatten_nullable`。
    """
    if not isinstance(node, dict):
        return node

    result: Dict[str, Any] = {}
    for key, value in node.items():
        if key == "properties" and isinstance(value, dict):
            result[key] = {name: _normalize_schema(sub) for name, sub in value.items()}
        elif key in _SCHEMA_DICT_KEYS:
            result[key] = _normalize_schema(value) if isinstance(value, dict) else value
        elif key in _SCHEMA_LIST_KEYS and isinstance(value, list):
            result[key] = [_normalize_schema(sub) for sub in value]
        else:
            result[key] = value

    if "const" in result:
        result["enum"] = [result.pop("const")]
    # `additionalProperties: true` 是 JSON Schema 的**默认值**，写出来零信息（只增字节、
    # 让读 schema 的人误以为这里有什么约束）。裸 `dict` 注解会产出它；
    # `dict[str, int]` 产出的是带真值的 dict，必须保留。
    if result.get("additionalProperties") is True:
        result.pop("additionalProperties")
    return _flatten_nullable(result)


def python_type_to_json_schema(python_type: Any) -> Dict[str, Any]:
    """将 Python 类型转换为 JSON Schema 类型定义（基于 pydantic `TypeAdapter`）。

    #195 改造前后对照（三个洞）：

    | 注解 | 改造前 | 改造后 |
    | --- | --- | --- |
    | `Optional[str]` | `{"type": "string"}`（null 合法性丢失）| `{"type": ["string", "null"]}` |
    | `dict[str, int]` | `{"type": "object"}` | `{"type": "object", "additionalProperties": {"type": "integer"}}` |
    | `Union[str, int]` | `{"type": "object"}`（什么信息都没有）| `{"anyOf": [{"type": "string"}, {"type": "integer"}]}` |

    **返回值每次都新建**（TypeAdapter 每个注解一份新 dict，本函数再复制结构），
    因此调用方 `get_function_metadata` 往里写 `description` 不会串到别的参数上 ——
    这也是这里**不加 `lru_cache`** 的原因：缓存会把 `description` 的写入带进共享对象，
    同一个 `str` 注解的几十个参数会互相污染说明文案。
    """
    return _normalize_schema(_type_adapter_json_schema(python_type))



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
    #: 静态基准可见性（`ToolVisibility`）。受 D2 开关控制的工具在 core 层按
    #: `resolve_effective_visibility()` 动态解析，这里只声明「设计上的基准」。
    visibility: ToolVisibility = ToolVisibility.TOOL_ONLY

    def to_openai_tool(self) -> llmApiUtil.OpenAITool:
        """将当前工具转换为 OpenAI 工具格式。"""
        metadata = get_function_metadata(self.name, self.callable)
        return llmApiUtil.OpenAITool(
            visibility=self.visibility,
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

"""tool call 参数的 JSON Schema 校验（#189）。

## 为什么需要这一层

改造前，模型给错参数（类型错、漏必填、枚举值非法）时，错误只能靠 `func(**args)` 抛的
`TypeError` 兜住，最终变成 `Invalid arguments for function xxx: got an unexpected ...`
这类**英文解释器文案**返回给模型 —— 既不含「哪个字段、期望什么、收到什么」的字段级
信息，也不是稳定结构（模型难以自纠，人排查时只能靠猜）。本模块把「按 schema 校验」
收敛成唯一实现，返回**字段级 + 稳定 error_code** 的结构化失败返回体。

## 单一事实源（硬约束）

schema 一律来自已有信息，**禁止为任何工具另写一份校验规则**：

- 本地工具：`funcToolType.get_function_metadata()` 的 `parameters`
  （与 `FuncTool.to_openai_tool()` 下发给模型的是同一个函数产出的同一份结构）；
- TSP 远端工具：`OpenAITool.function.parameters`，它由 `tspDriver._load_tsp_tools()`
  用 `OpenAIFunctionParameter(**tool.input_schema)` 构造，同样是下发给模型的同源结构。

## 兜底口径（小马哥 1015 / 1020 裁定）

schema **不可用**（提取抛异常、结构不是 dict、`check_schema` 判定非法）时：
**记一条 warning + 跳过校验**，绝不拦死正常调用。理由是远端 schema 由对端决定，
后端无法保证其合法性；「因为 schema 坏了所以工具永远调不通」比「这次没校验」严重得多。

warning 文案固定为「工具参数 schema 不可用，已跳过校验」+ `tool=` + `reason=`，
便于 grep 与用例断言。**兜底只作用于出问题的那个工具**：解析发生在按工具取 schema 的
那一处，不会有「整个校验入口 except: pass」这种全局失能写法。
"""

from __future__ import annotations

import copy
import inspect
import logging
import re
from typing import Any, Callable, Dict, List, Optional

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from . import funcToolType
from toolResult import INVALID_TOOL_ARGS, failure, truncate

logger = logging.getLogger(__name__)


#: schema 不可用 ⇒ 跳过校验。**文案固定**：用例与 grep 都靠它定位。
_SCHEMA_UNAVAILABLE_TMPL = "工具参数 schema 不可用，已跳过校验: tool=%s, reason=%s"

#: 从 jsonschema 的 required 错误文案里取缺失参数名（`"'x' is a required property"`）。
_REQUIRED_NAME_PATTERN = re.compile(r"'([^']+)' is a required property")

#: schema 缓存：key = 可调用对象身份（见 `_callable_identity`），value = parameters dict。
_schema_cache: Dict[tuple, Dict[str, Any]] = {}


# --------------------------------------------------------------------------- #
# 缓存
# --------------------------------------------------------------------------- #


def _callable_identity(func: Callable[..., Any]) -> tuple:
    """返回与「这份函数定义」绑定的缓存键。

    为什么不能只用 `id(func)`：热重载会让**新函数对象复用旧地址**，`id` 会撞成同一个
    键，于是校验继续用旧 schema —— 这正是「改了签名但校验没跟上」的静默漂移。

    所以取 `(module, qualname, code, 注解指纹)`：

    - `code` 对象按值比较/哈希（`co_code`、`co_consts`、`co_names`…），重载后代码变了
      就换键；
    - docstring 在 `co_consts` 里 ⇒ 改说明也会换键；
    - 但**类型注解不在 code 对象里**（`__annotations__` 是函数属性），所以额外拼注解
      指纹；否则「只改注解不改函数体」会命中旧缓存。
    - 没有 `__code__` 的可调用对象（`functools.partial`、C 扩展、Mock）退化成 `id`：
      它们本就无法被热重载，`id` 是唯一可用句柄。

    已知局限（写进报告）：注解写成**字符串别名**时，别名指向的对象被替换不会反映到
    指纹里；那种情况下需重启或调 `clear_schema_cache()`。
    """

    code = getattr(func, "__code__", None)
    if code is None:
        return ("id", id(func))
    annotations = getattr(func, "__annotations__", None) or {}
    annotation_fingerprint = tuple(
        sorted((name, repr(value)) for name, value in annotations.items())
    )
    return (
        getattr(func, "__module__", None),
        getattr(func, "__qualname__", None),
        code,
        annotation_fingerprint,
    )


def clear_schema_cache() -> None:
    """清空 schema 缓存（仅供测试：判据必须在**冷缓存**下施加，否则 monkeypatch 读不到）。"""
    _schema_cache.clear()


def get_cached_schema_keys() -> List[tuple]:
    """返回当前缓存里的键（只读，供用例核对「校验用的就是按身份缓存的这一份」）。"""
    return list(_schema_cache)


def _drop_variadic_params(
    func: Callable[..., Any],
    schema: Dict[str, Any],
) -> Dict[str, Any]:
    """把 `*args` / `**kwargs` 从 schema 里摘掉（`get_function_metadata` 会把它们当必填字段）。

    生成器只认「按名字 + 注解」的参数：`def f(**kwargs)` 会产出
    `required: ["kwargs"], properties: {"kwargs": {"type": "string"}}` —— 一个**不存在的
    必填参数**。真实 37 个工具都没有变长参数（已实测），这种形状只会出现在
    「测试替身 / 装饰器包装」的可调用对象上。若不摘，`run_tool_call("{}")` 会对这类
    替身一律报「缺少必填参数 kwargs」，把原本能跑的本地/转发路径打断。

    只摘变长参数本身，**其余具名参数照旧校验** —— 比整份跳过更严；`**kwargs` 语义上
    接受任意关键字参数，而 schema 没设 `additionalProperties: false`，所以摘掉后行为一致。
    """

    try:
        parameters = inspect.signature(func).parameters
    except (TypeError, ValueError):  # 拿不到签名（C 可调用）：无从摘，原样返回
        return schema

    variadic = {
        name for name, param in parameters.items()
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    }
    if not variadic:
        return schema

    properties = schema.get("properties")
    required = schema.get("required")
    if not isinstance(properties, dict):
        return schema
    if not variadic & set(properties):
        return schema

    cleaned = copy.deepcopy(schema)
    for name in variadic:
        cleaned.get("properties", {}).pop(name, None)
    if isinstance(required, list):
        cleaned["required"] = [name for name in required if name not in variadic]
    return cleaned


def get_tool_parameters_schema(
    tool_name: str,
    func: Callable[..., Any],
) -> Optional[Dict[str, Any]]:
    """取本地工具的参数 JSON Schema（按可调用对象身份缓存）。

    返回 `None` 表示 schema 不可用 —— 调用方应**跳过校验**（warning 已在此处记一条）。
    失败结果**不缓存**：这样修好签名后下一次调用就能恢复校验，也不会让「一次脏读取」
    永久污染这个工具。
    """

    key = _callable_identity(func)
    cached = _schema_cache.get(key)
    if cached is not None:
        return cached

    try:
        metadata = funcToolType.get_function_metadata(tool_name, func)
    except Exception as e:  # noqa: BLE001 —— 提取失败即「不可用」，不影响工具本身
        logger.warning(_SCHEMA_UNAVAILABLE_TMPL, tool_name, f"get_function_metadata 抛异常: {e!r}")
        return None

    schema = metadata.get("parameters") if isinstance(metadata, dict) else None
    if not isinstance(schema, dict):
        logger.warning(
            _SCHEMA_UNAVAILABLE_TMPL,
            tool_name,
            f"metadata.parameters 不是 dict: {type(schema).__name__}",
        )
        return None

    schema = _drop_variadic_params(func, schema)
    _schema_cache[key] = schema
    return schema


def get_remote_tool_parameters_schema(
    tool_name: str,
    openai_tool: Any,
) -> Optional[Dict[str, Any]]:
    """取 TSP 远端工具的参数 JSON Schema（来自 `OpenAITool.function.parameters`）。

    远端 schema 由对端给出，可能不合规范 ⇒ 任何取不到/取到非 dict 的情况都返回 `None`
    并记 warning，由调用方跳过校验。**不缓存**：`_tsp_tools` 在每次重连后重建，
    schema 随对端变化，缓存的收益（几十微秒）不值得引入一次「对端升级了但我们还在用
    旧 schema」的漂移窗口。
    """

    parameters = getattr(getattr(openai_tool, "function", None), "parameters", None)
    if parameters is None:
        logger.warning(
            _SCHEMA_UNAVAILABLE_TMPL, tool_name, "远端工具缺少 function.parameters"
        )
        return None

    model_dump = getattr(parameters, "model_dump", None)
    if callable(model_dump):  # pydantic 模型（正常路径）
        try:
            parameters = model_dump()
        except Exception as e:  # noqa: BLE001 —— 序列化失败也只是「不可用」
            logger.warning(
                _SCHEMA_UNAVAILABLE_TMPL, tool_name, f"model_dump 抛异常: {e!r}"
            )
            return None

    if not isinstance(parameters, dict):
        logger.warning(
            _SCHEMA_UNAVAILABLE_TMPL,
            tool_name,
            f"远端 parameters 不是 dict: {type(parameters).__name__}",
        )
        return None
    return parameters


# --------------------------------------------------------------------------- #
# 校验
# --------------------------------------------------------------------------- #


def _build_validator(
    tool_name: str,
    schema: Dict[str, Any],
) -> Optional[Draft202012Validator]:
    """先用 `check_schema` 自检，再构造 validator；schema 非法 ⇒ 返回 None（跳过校验）。

    自检不是形式主义：`iter_errors()` 遇到非法 schema 会抛 `SchemaError`，那会把
    「远端 schema 不合规范」变成「工具永远调不通」。这里把非法 schema 降级成 skip。
    """

    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as e:
        logger.warning(_SCHEMA_UNAVAILABLE_TMPL, tool_name, f"check_schema 判定非法: {e.message}")
        return None
    return Draft202012Validator(schema)


def _truncate(text: str) -> str:
    """本地别名：字段级展示同样受 `toolResult.MAX_INSTANCE_REPR` 约束（口径只有一份）。"""
    return truncate(text)


def _is_ignorable_null(error: ValidationError, schema: Dict[str, Any]) -> bool:
    """【已停用 · #195，仅作历史证据保留】`null` 打到**单个非必填字段** ⇒ 忽略该错误。

    ⚠️ `validate_tool_args` **不再调用本函数**。保留原因：《本单正文 §3.2》要求
    H1/H2 两轮打回的历史注释不删。**不要以为它还生效**——它现在的唯一作用是
    让人知道「当年为什么加了这条豁免」，以及它本身错在哪（见下方「为什么这条思路
    从根上不成立」）。

    ## 为什么这条思路从根上不成立（#195 的根因）

    它的前提是「schema 里表达不出 null 的合法性」。这个前提在 #195 之前是真的：
    `python_type_to_json_schema` 把 `Optional[T]` 剥成 `T`，于是 `{"timezone": null}`
    只能靠豁免放行。但豁免面按**「是否必填」**切，而 null 合法性该按**「注解是否含
    None」**切——两条线不重合的地方就是 19 个注解不含 None 的字段（`create_task.description`
    / `.note` / `list_tasks.open_only` …）。它们本不该接受 null，却被一并放行，
    一路撞到 `NOT NULL constraint failed`，用户看到裸数据库错误。

    ⇒ 正确修法是让 schema 表达 nullability（TypeAdapter），**豁免随之失去存在理由**。
    「把豁免面再收窄一点」是错方向：它治的是症状（哪一类错误被吃掉），不是病因
    （schema 不知道自己能不能 null）。

    ## 豁免面必须恰好是「路径长度为 1 的字段」（H1 + H2，38 两轮打回）【历史记录】

    ⚠️ 以下三条是**当年**的处置口径，现在整条豁免已停用，请不要照它实现。

    本函数只该豁免**一个具名参数整体为 `null`** 这一种情形。`absolute_path` 长度
    决定了「错在哪一层」，三者必须分开处理：

    | `len(path)` | 含义 | 处置 |
    | --- | --- | --- |
    | `0` | **根节点**：整份参数不是对象（`function_args` 字面量就是 `null`）| **报错**（H1）|
    | `1` | 单个参数整体为 `null`（`{"timezone": null}`）| 非必填 ⇒ 豁免；必填 ⇒ 报错 |
    | `≥2` | **容器元素级** `null`（`{"tags": [null]}`、`{"a":{"b":null}}`）| **报错**（H2）|

    ### H1：根节点漏网（38 首次打回）
    只判 `instance is None` 时，根节点的 `type: object` 错误（`path=[]`、`instance=None`）
    落到 `return True` 被豁免 ⇒ `func(**None)` ⇒ 英文原话 + 无 `error_code`。
    实测修前 `get_tools(include_container_tools=True)` 的 **37 个工具喂 `'null'` 全漏**。

    ### H2：元素级漏网（38 二次打回）
    修 H1 时只补了 `if not path`，**`path` 长度 ≥2 仍落到 `return True`**：
    `{"depends_on": [null]}`（`items: integer`）被豁免 ⇒ `int(None)` ⇒ 同一旧形态。
    实测修前 **10 个 array 型参数喂 `[None]` 全部放行**；**TSP 远端更严重** ——
    `call_tool` 真的被调用了，**非法参数已越过进程边界发给对端**。

    两次都是同一个假设（「null 都可以放过」）的两半，所以这里一次性把豁免面
    收到**恰为长度 1**，而不是再逐个补 `len(path)==1` / `len(path)==2` 的分支。

    ### 为什么不会误杀（实测）
    真实 37 个工具里 `object` 型参数只有 4 个（`save_agent.i18n` 等），它们的 schema
    **都没有嵌套 `properties`**，`array` 也没有 `items.type == object` 的用例 ⇒
    本项目不存在「嵌套对象内的非必填字段传 null 且应被允许」的合法签名。
    反向对照：`{"timezone": null}`、`{"depends_on": null}`（字段整体 null）仍放行。
    """

    if error.instance is not None or error.validator not in ("type", "enum"):
        return False
    path = list(error.absolute_path)
    if len(path) != 1:
        # 0 = 根节点（H1）；≥2 = 容器元素级（H2）。两者都不是「单个非必填字段」。
        return False
    required = schema.get("required") or []
    if path[0] in required:
        return False
    return True


def _describe(error: ValidationError) -> str:
    """把一条 jsonschema 错误翻成「字段 + 期望 + 实收」的中文短句。

    `required` 单独处理：jsonschema 对**每个**缺失的必填参数各报一条错误
    （`validator_value` 都是完整的 required 列表，`absolute_path` 是空），
    所以这里只能从 `message`（`"'x' is a required property"`）取具体缺的那个名字
    —— 若照通用模板拼 `validator_value`，两个缺失参数会拼出重复的整串列表（实测踩过）。
    """

    if error.validator == "required":
        missing = _REQUIRED_NAME_PATTERN.search(error.message)
        name = missing.group(1) if missing else "?"
        return f"缺少必填参数 '{name}'"

    field = ".".join(str(part) for part in error.absolute_path) or "(参数本身)"
    actual = _truncate(repr(error.instance))

    if error.validator == "type":
        return f"参数 {field} 应为 {error.validator_value}，实际收到 {actual}"
    if error.validator == "enum":
        return f"参数 {field} 只能取 {error.validator_value} 之一，实际收到 {actual}"
    return (
        f"参数 {field} 不满足 {error.validator} 约束 "
        f"{_truncate(repr(error.validator_value))}，实际收到 {actual}"
    )


def validate_tool_args(
    tool_name: str,
    args: Any,
    *,
    func: Optional[Callable[..., Any]] = None,
    schema: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """校验一次 tool call 的参数。

    Args:
        tool_name: 工具名，仅用于日志与错误文案。
        args: `json.loads(function_args)` 的结果（正常是 dict；模型给 `[]` / `5` 时也可能
            不是 dict，那本身就该被 schema 的 `type: object` 拦下）。
        func: 本地工具的可调用对象；给了就按身份缓存取 schema。
        schema: 已解析好的 schema（TSP 远端路径用）。**给了就不再用 `func`**。

    Returns:
        通过返回 `None`；不通过返回结构化失败返回体
        `{"success": False, "error_code": "invalid_tool_args", "message": <字段级>}`。
        schema 不可用时也返回 `None`（warning 已由取 schema 的函数记过）。
    """

    if schema is None:
        if func is None:
            # 没有任何 schema 来源 ⇒ 无从校验（不当作失败）。
            return None
        schema = get_tool_parameters_schema(tool_name, func)
        if schema is None:
            # 已由取 schema 的函数记过 warning；这里是**该工具**的兜底，不涉及别的工具。
            return None

    validator = _build_validator(tool_name, schema)
    if validator is None:
        return None

    # 不再豁免任何 null 错误（#195）：schema 生成器改用 pydantic `TypeAdapter` 后，
    # `Optional[T]` 已由 schema 自身表达成 `type: [T, "null"]`，null 的合法性回到了
    # schema 里；`_is_ignorable_null` 的存在前提（「schema 里表达不出 null」）消失了。
    # 保留该函数及其 H1/H2 历史注释作为**历史证据**（见函数 docstring），
    # 但实现处不再调用 —— 若是留着「非必填字段 null 豁免」，19 个注解不含 None 的字段
    # （`create_task.description` 等）会继续放行 null，一路撞到 `NOT NULL constraint failed`。
    errors = list(validator.iter_errors(args))
    if not errors:
        return None

    # 稳定顺序：字段路径 → 约束类型，避免同一份错误在不同运行里文案顺序漂移。
    errors.sort(key=lambda error: ([str(part) for part in error.absolute_path], error.validator))
    details = "；".join(_describe(error) for error in errors)
    return failure(
        f"参数校验失败（tool={tool_name}）：{details}。请按提示修正参数后重试。",
        INVALID_TOOL_ARGS,
    )

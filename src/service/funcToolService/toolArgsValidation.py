"""按 JSON Schema 校验 tool call 的参数，返回「哪个字段、期望什么、收到什么」加固定 error_code。

不校验的话，模型给错参数只能等 `func(**args)` 抛 `TypeError`，返回给它的是英文解释器文案，既没有字段级信息也不是固定结构，模型没法自己改。校验规则不在这里手写：schema 只有一处来源——本地工具取 `get_function_metadata()` 的 `parameters`，TSP 远端取 `OpenAITool.function.parameters`，同一份下发给模型。

schema 提取失败或 `check_schema` 判定非法时只记一条 warning 并跳过校验、不要拦死调用——这条兜底是小马哥 1015 / 1020 裁定的：远端 schema 由对端决定，后端保证不了它的合法性，「因为 schema 坏了所以工具永远调不通」比「这次没校验」严重得多。兜底只作用于出问题的那个工具，warning 文案与 `tool=` / `reason=` 保持固定（用例按它断言）。"""

from __future__ import annotations

import copy
import inspect
import logging
import re
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, cast


from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from . import funcToolType
from toolResult import INVALID_TOOL_ARGS, failure, truncate

logger = logging.getLogger(__name__)


#: schema 不可用 ⇒ 跳过校验。**文案固定**：用例与 grep 都靠它定位。
_SCHEMA_UNAVAILABLE_TMPL = "工具参数 schema 不可用，已跳过校验: tool=%s, reason=%s"

#: schema 层拒绝文案的固定前缀。**这是唯一来源**：测试要判「这次拒绝是 schema 层还是
#: 运行时层」时必须从这里 import（`from ...toolArgsValidation import SCHEMA_LAYER_MARK`），
#: 不许在测试里再抄一份字符串。
#:
#: 为什么这件事必须单点：`error_code == "invalid_tool_args"` 有**两个**产出层 ——
#: schema 层（本模块）与运行时层（`tools.py` 里的 locator / 新建必填缺失，同码是
#: 小马哥 12:02 的硬条件 1，让调用方一个码就能判「补参数重试」）。判据要问的是
#: 「**schema 层**放不放行 null」，只看码会把运行时的拒绝误认成 schema 的拒绝
#: （实测出现过 27 处假红）。所以必须按层文案区分，而「层文案」这个字符串只能有一处
#: 定义 —— 抄两份的话，文案改了、两份常量一起改，判据照样绿，等于没测。
SCHEMA_LAYER_MARK = "参数校验失败（tool="

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
    必填参数**。真实 49 个工具都没有变长参数（已实测），这种形状只会出现在
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
    """本地别名：字段级展示同样受 `toolResult.MAX_INSTANCE_REPR` 限制（这个上限只有一处定义）。"""
    return truncate(text)


def _is_ignorable_null(error: ValidationError, schema: Dict[str, Any]) -> bool:
    """单个参数整体传 `null` 时报的错在这里放过，只对 `schema=` 传进来的外部 schema 生效。

    放过范围一放宽，用户就会看到数据库原样抛出的 `NOT NULL constraint failed`；放宽到数组元素上，`{"tags": [null]}` 这种非法参数会直接发给 TSP 远端。只有对方给的 schema 说不清「这个字段能不能是 null」时才有必要放过，本仓库的 schema 由 TypeAdapter 生成、已经写清，所以 `func=` 路径一律严格。

    唯一不能动的是 `len(error.absolute_path) != 1` 这一支直接返回 `False`；改完跑 `tests/unit/service/funcToolService/test_tool_schema_nullable_tsp.py`，判据表与两次打回的实测见 `docs/tech/12_common/tool_args_validation.md`。"""

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

    `anyOf` 的 **Optional 形态**单独处理（#195 返工，38 的 S1）：见 `_unwrap_nullable_any_of`。
    """

    if error.validator == "required":
        missing = _REQUIRED_NAME_PATTERN.search(error.message)
        name = missing.group(1) if missing else "?"
        return f"缺少必填参数 '{name}'"

    error = _unwrap_nullable_any_of(error)
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


def _unwrap_nullable_any_of(error: ValidationError) -> ValidationError:
    """把 `anyOf:[X, {"type": "null"}]` 的错误换回 X 自身的错误，只为保住报错文案。

    `Optional[Literal[...]]` 的值不合法时，报错会落在 `validator="anyOf"`，用户看到的是「不满足 anyOf 约束 [{...}, {...}]」，比原来的「只能取 ['TODO','ON_HOLD'] 之一」难读得多。`enum` 属于 `_NULL_SENSITIVE_KEYS`、不能压成 `type: [string, null]`（否则 enum 会咬住 null），所以 schema 里必须保留 anyOf 两支。

    只在 `validator == "anyOf"`、恰好两支、且其中一支正是 `{"type": "null"}` 时展开；`Union[str, int]` 这种多分支不展开。展开只影响文案，是否放行由外层判。"""

    if error.validator != "anyOf":
        return error
    branches = error.validator_value
    if not isinstance(branches, list) or len(branches) != 2:
        return error
    non_null = [b for b in branches if b != {"type": "null"}]
    if len(non_null) != 1:
        return error

    inner = non_null[0]
    for key in ("enum", "type", "const"):
        if key in inner:
            # 借 jsonschema 的字段名做一份同形对象：只需 validator / validator_value /
            # absolute_path / instance 四个属性，`_describe` 之后只读它们。
            return cast(
                ValidationError,
                SimpleNamespace(
                    validator=key,
                    validator_value=inner[key],
                    absolute_path=error.absolute_path,
                    instance=error.instance,
                ),
            )
    return error


def validate_tool_args(
    tool_name: str,
    args: Any,
    *,
    func: Optional[Callable[..., Any]] = None,
    schema: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """校验一次 tool call 的参数：通过返回 `None`，不通过返回 `{"success": False, "error_code": "invalid_tool_args", ...}`。

    放不放行取决于 schema 从哪来：`func=` 的由本仓库生成、写清了能不能为 null，一律严格；`schema=` 的由对端提供、说不清，才用 `_is_ignorable_null` 放过单个参数整体为 null。看的是「有没有传 `schema`」，不是「字段是否必填」——把它收紧，`read_file{encoding: null}` / `grep_search{path: null}` 这种 gtsp 本体实测 ACCEPT 的写法会被整批拒掉（38 报的阻断项 B1）。

    改完跑 `tests/unit/service/funcToolService/test_tool_schema_nullable_tsp.py`；两条路径的分流表与参数语义见 `docs/tech/12_common/tool_args_validation.md`。"""

    # ⚠️ 分流依据必须是「schema 从哪来」，不能看「字段是否必填」。
    # `schema is None` 进来时才是本地路径（下面用 `func` 现取 schema）；
    # 外部传入的 schema 不豁免的话，gtsp 那 26 个「非必填但 schema 无 null」字段
    # 传 null 会全部被拒 —— 而 gtsp 本体实测接受它们，属新误杀（38 的阻断项 B1）。
    is_external_schema = schema is not None

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

    errors = list(validator.iter_errors(args))
    if is_external_schema:
        # 外部 schema：保留豁免（其生成器不归本仓库管，表达不出 null）。
        errors = [error for error in errors if not _is_ignorable_null(error, schema)]
    if not errors:
        return None

    # 稳定顺序：字段路径 → 约束类型，避免同一份错误在不同运行里文案顺序漂移。
    errors.sort(key=lambda error: ([str(part) for part in error.absolute_path], error.validator))
    details = "；".join(_describe(error) for error in errors)
    return failure(
        f"{SCHEMA_LAYER_MARK}{tool_name}）：{details}。请按提示修正参数后重试。",
        INVALID_TOOL_ARGS,
    )

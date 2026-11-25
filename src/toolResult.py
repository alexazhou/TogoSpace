"""构造工具失败返回体的唯一位置：`{"success": False, ...}` 只在这里拼。

转换散在四处（`tools._tool_error_result`、`core.run_tool_call` 兜底、`toolRegistry.execute_tool_call` 兜底、`tspDriver._execute_tsp_tool`）时，同一类异常走不同路径给出不同结构，模型没法按 `error_code` 自纠，人也只能照文案 grep。收敛成一个函数后只有一份实现：前缀、要不要带 `error_code`、键的顺序都不再各写一份。

`prefix` 仍由调用点传入（那是对外文案，有用例和提示词依赖），本模块不改前缀、也不吞 `ForwardToolCall`（它是控制流，要在调用点先放行）。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

#: 「实收参数原文」在报错里的最大展示长度，避免超长参数灌进模型上下文。
MAX_INSTANCE_REPR = 80


def truncate(text: str) -> str:
    """按 `MAX_INSTANCE_REPR` 截断展示文本。"""
    return text if len(text) <= MAX_INSTANCE_REPR else f"{text[:MAX_INSTANCE_REPR - 3]}..."


def failure(message: str, error_code: Any = None) -> Dict[str, Any]:
    """最基础的成功/失败返回体构造：`success: False` + 文案 +（可选）错误码。"""

    result: Dict[str, Any] = {"success": False, "message": message}
    if error_code is not None:
        result["error_code"] = error_code
    return result


def error_result(
    exc: BaseException,
    *,
    prefix: str = "",
    message: Optional[str] = None,
) -> Dict[str, Any]:
    """把异常转成工具层的结构化失败返回体（**唯一实现**）。

    Args:
        exc: 待转换的异常。
        prefix: 该调用点的既有文案前缀（如 `"函数执行失败: "`）；默认无前缀。
        message: 覆盖 message 正文（不给则取 `error_message` 属性，其次 `str(exc)`）。

    为什么取 `error_message` 优先：`TogoException.__str__` 只吐业务消息、`error_code` 单独
    走字段；但别的异常类型也可能带同名属性（`error_message` 是更精确的人读文本）。
    为什么用 `getattr` 而不是 `isinstance(TogoException)`：本模块要保持叶子级依赖
    （`tools` / `core` / `toolRegistry` / `tspDriver` 都 import 它），不能反向依赖业务异常类。
    """

    body = message
    if body is None:
        body = getattr(exc, "error_message", None) or str(exc)
    return failure(f"{prefix}{body}", getattr(exc, "error_code", None))


def invalid_json_args(tool_name: str, raw_args: Any, error: BaseException) -> Dict[str, Any]:
    """入参不是合法 JSON 时的失败返回体（`run_tool_call` 与 `_execute_tsp_tool` 共用）。

    改造前 `run_tool_call` 在这里**静默 `args = {}` 继续执行**：模型给了坏 JSON，工具却用
    一堆默认值跑起来（`create_container` 这种全可选参数的工具会真的动手建容器）。
    现在降级成结构化失败：给出错误码 + 原文片段，模型可据此重发一整串参数。
    """

    return failure(
        f"参数 JSON 解析失败（tool={tool_name}）：{error}。"
        f"收到的原文：{truncate(repr(raw_args))}；请检查引号/逗号后重发。",
        INVALID_JSON_ARGS,
    )


#: 入参不是合法 JSON 对象（与「类型不符」区分：这种要重发整串参数）。
INVALID_JSON_ARGS = "invalid_json_args"

#: 参数不合法（类型/枚举/必填不符）。
INVALID_TOOL_ARGS = "invalid_tool_args"

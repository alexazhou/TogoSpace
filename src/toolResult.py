"""工具失败返回体的**唯一构造点**（#189：简化异常处理逻辑）。

## 为什么必须只有一处

改造前，「异常 → `{"success": False, ...}`」的手工转换散在四个地方，各自决定：

| 位置 | 文案前缀 | 是否带 `error_code` |
| --- | --- | --- |
| `tools._tool_error_result` | 无 | 有 |
| `core.run_tool_call` 兜底 | `函数执行失败: ` | 有 |
| `toolRegistry.execute_tool_call` 兜底 | `工具调用失败: ` | **无**（错误码在这里丢） |
| `tspDriver._execute_tsp_tool` | `TSP 工具调用失败: ` | 无 |

同一类业务异常在不同路径上给出不同结构，模型侧无法按 `error_code` 稳定自纠，人排查时
也只能按文案 grep。本模块把这四处的**形状**收敛成一个函数：message 的前缀由调用点用
`prefix` 指定（那是既有的对外文案契约，不归本单改），其余（取哪段文本、要不要带
`error_code`、键的顺序）只有一份实现。

## 不做的事

- **不改文案前缀**：`函数执行失败:` / `工具调用失败:` 都有既有用例与模型提示词依赖。
- 不吞 `ForwardToolCall`：它是控制流（#160），必须在调用点先放行，本模块不参与。
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

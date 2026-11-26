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

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

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

# ---------------------------------------------------------------------------
# 业务通用码（#376 B4）：跨模块共用的一小组语义码
# ---------------------------------------------------------------------------
# 为什么只有这一小组：码是**调用方自纠的依据**，每一种码都必须对应一种不同的下一步
# 动作。再细分（如把「任务不存在」和「成员不存在」分成两个码）不会带来任何不同动作，
# 只会让调用方多一张表要维护。具体场景仍靠 `message` 说清楚。
#
# 与 `INVALID_*` 的分界：**参数本身不合法**（类型/枚举/必填）走 `invalid_*`；
# 参数合法但**对象状态不允许/对象不存在/身份不够**走这里的业务码。

#: 对象不存在（任务/事件/成员/部门/房间/角色模板）。**刻意不区分"存在但不属于你"**：
#: 区分的代价是泄漏存在性（见 #376 裁定：二义文案一律不拆）。
NOT_FOUND = "not_found"

#: 身份不被允许（跨团队访问、改 SYSTEM 内置模板、非创建人改依赖等）。
PERMISSION_DENIED = "permission_denied"

#: 对象存在但**当前状态**不允许该动作（如终态任务再流转、房间成员不足）。
INVALID_STATE = "invalid_state"

#: 缺少执行必需的上下文（无团队 / 房间 / 当前 Agent 上下文）。
MISSING_CONTEXT = "missing_context"

#: 通用兜底码（#376 B4）：**只表示"这次失败了、但没有更具体的码"**，不携带语义。
OPERATION_FAILED = "operation_failed"


def ensure_error_code(
    result: Any,
    *,
    code: str = OPERATION_FAILED,
    where: str = "",
) -> Any:
    """出口兜底：**失败的返回体必须有 `error_code`**（#376 B4）。

    为什么需要这道兜底：调用方（模型 / 前端 / 上层 Agent）只按 `error_code` 稳定自纠，
    手写失败字典漏了码就等于"失败了但说不出为什么"，模型只能读文案猜；而手写字典有
    226 处（见 `#376` 盘点），靠逐个补齐必然漏。这里在唯一出口上补一次。
    注意：补的是**通用码**，不是"猜出来的语义码" —— 见 `OPERATION_FAILED` 的注释，
    兜底一旦开始猜，就从"保证有码"变成"制造错误信息"。

    三条边界：
    1. **已有 code 一律不覆盖**（含 `invalid_tool_args` / `invalid_json_args` /
       业务码等所有具体码）：兜底只填空白，不改判。
    2. **只看 `success is False`**：成功体、以及没有 `success` 键的自定义返回体都不动
       （它们不是"失败返回体"，不该被塞一个码）。
    3. **只处理 dict**：返回体形状不是本函数的职责，非 dict 原样返回。

    Args:
        result: 工具返回体。
        code: 要补的码，默认通用码；调用点不应传业务码（见第 1 条）。
        where: 用于日志定位（一般传工具名），不写进返回体——返回体形状由
            `failure` / `error_result` 定，这里不新增字段。
    """

    if not isinstance(result, dict):
        return result
    if result.get("success") is not False:
        return result
    if result.get("error_code"):
        return result

    logger.warning(
        "工具失败返回体缺 error_code，补通用码: code=%s, tool=%s, message=%r",
        code,
        where or "<unknown>",
        result.get("message"),
    )
    patched = dict(result)
    patched["error_code"] = code
    return patched

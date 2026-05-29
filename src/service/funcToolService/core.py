import inspect
import json
import logging
import re
from typing import Any, Callable, Iterable, Optional, Union, get_args, get_origin, get_type_hints

from constants import ToolCategory
from util import llmApiUtil
from service.roomService import ToolCallContext
from .funcToolType import FuncTool
from .tools import (
    create_task,
    delete_dept,
    delete_role_template,
    delete_room,
    finish_action,
    get_agent_info,
    get_dept_info,
    get_role_template,
    get_room_info,
    get_task,
    get_time,
    list_role_templates,
    list_tasks,
    reload_team,
    save_agent,
    save_dept,
    start_chat,
    save_role_template,
    save_room,
    send_chat_msg,
    update_task,
    wake_up_agent,
)

logger = logging.getLogger(__name__)

# ─── LLM 工具参数幻觉防御 ─────────────────────────────────
# 部分 LLM（尤其是 DeepSeek）在生成 tool_calls 时会出现以下幻觉模式：
# 1. {"arguments": {"param": "value"}} — 将参数包装在 arguments 键下
# 2. 生成乱码 token（如 </｜DSML｜parameter）导致 JSON 解析失败
# 3. 使用错误的参数名（如 offset 代替 start_line）
# 这些辅助函数用于在调用工具前进行预处理和错误恢复。

_GARBLED_TOKENS = (
    "</｜DSML｜parameter",
    "</|DSML|parameter",
    "</｜DSML｜",
    "</|DSML|",
)


def _clean_garbled_json(raw: str) -> str:
    """移除 LLM 生成 JSON 中已知的乱码 token。"""
    cleaned = raw
    for token in _GARBLED_TOKENS:
        cleaned = cleaned.replace(token, "")
    return cleaned


def _unwrap_arguments_wrapper(args: dict, function_name: str) -> tuple[dict, bool]:
    """检测并解包 {"arguments": {...}} 包装模式。

    部分 LLM 会将工具参数嵌套在 "arguments" 键下，例如：
      {"arguments": {"confirm_no_need_talk": true}}
    而非正确的顶层格式：
      {"confirm_no_need_talk": true}

    Returns:
        (unwrapped_args, was_unwrapped)
    """
    if "arguments" not in args:
        return args, False

    func_tool = get_func_tool(function_name)
    if func_tool is None:
        return args, False

    sig = inspect.signature(func_tool.callable)
    valid_param_names = {
        p.name for p in sig.parameters.values()
        if not p.name.startswith("_")
    }

    # 如果 "arguments" 是函数的合法参数名，不解包
    if "arguments" in valid_param_names:
        return args, False

    other_keys = {k for k in args if k != "arguments"}

    # 如果除了 arguments 外还有其他合法参数名，可能是正常调用，不解包
    if other_keys and other_keys.intersection(valid_param_names):
        return args, False

    inner = args["arguments"]
    if isinstance(inner, dict):
        logger.warning(
            "检测到 arguments 包装模式(dict)，已自动解包: tool=%s, 原始 keys=%s, 解包后 keys=%s",
            function_name, list(args.keys()), list(inner.keys()),
        )
        return inner, True
    elif isinstance(inner, str):
        try:
            inner_parsed = json.loads(inner)
            if isinstance(inner_parsed, dict):
                logger.warning(
                    "检测到 arguments 包装模式(string)，已自动解包: tool=%s, 原始 keys=%s, 解包后 keys=%s",
                    function_name, list(args.keys()), list(inner_parsed.keys()),
                )
                return inner_parsed, True
        except json.JSONDecodeError:
            pass

    return args, False


def _format_type_hint(hint: Any) -> str:
    """将 Python 类型提示格式化为可读字符串。"""
    origin = get_origin(hint)
    if origin is Union or (hasattr(hint, "__class__") and hint.__class__.__name__ == "_UnionGenericAlias"):
        args = get_args(hint)
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return f"{_format_type_hint(non_none[0])} 或 null"
    if origin is list:
        args = get_args(hint)
        if args:
            return f"list[{_format_type_hint(args[0])}]"
        return "list"
    if origin is dict:
        return "dict"
    if hint is str:
        return "string"
    if hint is int:
        return "integer"
    if hint is float:
        return "number"
    if hint is bool:
        return "boolean"
    if hasattr(hint, "__name__"):
        return hint.__name__
    return str(hint)


def _levenshtein_distance(a: str, b: str) -> int:
    """计算两个字符串的 Levenshtein 编辑距离。"""
    if len(a) < len(b):
        return _levenshtein_distance(b, a)
    if len(b) == 0:
        return len(a)
    prev_row = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr_row = [i + 1]
        for j, cb in enumerate(b):
            curr_row.append(min(
                prev_row[j + 1] + 1,
                curr_row[j] + 1,
                prev_row[j] + (0 if ca == cb else 1),
            ))
        prev_row = curr_row
    return prev_row[-1]


def _find_close_match(wrong_key: str, valid_keys: set[str]) -> str | None:
    """在有效参数名中找到最接近的匹配。"""
    if not valid_keys:
        return None
    best_key = None
    best_dist = float("inf")
    threshold = max(1, len(wrong_key) // 3)
    for valid_key in valid_keys:
        dist = _levenshtein_distance(wrong_key, valid_key)
        if dist < best_dist and dist <= threshold:
            best_dist = dist
            best_key = valid_key
    return best_key


def _extract_unexpected_kwargs(error_str: str) -> list[str]:
    """从 TypeError 消息中提取多余的参数名。

    匹配模式：
    - "func() got an unexpected keyword argument 'foo'"
    - "func() got unexpected keyword arguments 'foo', 'bar'"
    """
    match = re.search(r"unexpected keyword arguments?\s+(.+)", error_str)
    if match:
        return re.findall(r"'([^']+)'", match.group(1))
    return []


def _extract_missing_args(error_str: str) -> list[str]:
    """从 TypeError 消息中提取缺失的必填参数名。

    匹配模式：
    - "func() missing 1 required positional argument: 'foo'"
    - "func() missing 2 required positional arguments: 'foo' and 'bar'"
    """
    match = re.search(r"missing \d+ required positional arguments?:\s+(.+)", error_str)
    if match:
        return re.findall(r"'([^']+)'", match.group(1))
    return []


def _build_schema_error_message(
    function_name: str,
    func: Callable[..., Any],
    unexpected_keys: list[str] | None = None,
    missing_keys: list[str] | None = None,
    raw_error: str | None = None,
) -> str:
    """构造包含完整参数列表的 schema 感知错误消息，帮助 LLM 自纠正。"""
    try:
        type_hints = get_type_hints(func)
    except Exception:
        type_hints = {}

    sig = inspect.signature(func)

    lines = [f"函数 {function_name} 参数错误"]
    if raw_error:
        lines.append(f"原因: {raw_error}")

    lines.append("")
    lines.append("期望的参数列表:")
    for param_name, param in sig.parameters.items():
        if param_name.startswith("_"):
            continue
        param_type = type_hints.get(param_name)
        type_name = _format_type_hint(param_type) if param_type else "any"
        is_required = param.default == inspect.Parameter.empty
        req_label = "必填" if is_required else "可选"
        default_info = "" if is_required else f", 默认值={param.default!r}"
        lines.append(f"  - {param_name} ({type_name}, {req_label}{default_info})")

    if unexpected_keys:
        lines.append("")
        lines.append(f"多余的参数: {', '.join(unexpected_keys)}")
        valid_names = {
            p.name for p in sig.parameters.values()
            if not p.name.startswith("_")
        }
        for wrong_key in unexpected_keys:
            suggestion = _find_close_match(wrong_key, valid_names)
            if suggestion:
                lines.append(f"  提示: 参数 \"{wrong_key}\" 是否应为 \"{suggestion}\"?")

    if missing_keys:
        lines.append("")
        lines.append(f"缺少的必填参数: {', '.join(missing_keys)}")

    return "\n".join(lines)


def build_tools(func_tools: Iterable[FuncTool]) -> list[llmApiUtil.OpenAITool]:
    """遍历 FuncTool 定义，构建并返回工具列表。"""
    return [func_tool.to_openai_tool() for func_tool in func_tools]


_func_tools: dict[str, FuncTool] = {}


def load_func_tools() -> dict[str, FuncTool]:
    global _func_tools
    _registry: dict[str, Any] = {
        "get_time": get_time,
        "send_chat_msg": send_chat_msg,
        "finish_action": finish_action,
        "get_dept_info": get_dept_info,
        "get_room_info": get_room_info,
        "get_agent_info": get_agent_info,
        "wake_up_agent": wake_up_agent,
        "start_chat": start_chat,
        "reload_team": reload_team,
        "list_role_templates": list_role_templates,
        "get_role_template": get_role_template,
        "save_agent": save_agent,
        "save_dept": save_dept,
        "delete_dept": delete_dept,
        "save_room": save_room,
        "delete_room": delete_room,
        "save_role_template": save_role_template,
        "delete_role_template": delete_role_template,
        "create_task": create_task,
        "update_task": update_task,
        "get_task": get_task,
        "list_tasks": list_tasks,
    }
    _func_tools = {}
    for name, func in _registry.items():
        _func_tools[name] = FuncTool(name, func)
    return _func_tools


def get_func_tool(name: str) -> FuncTool | None:
    return _func_tools.get(name)


async def startup() -> None:
    """加载启用的函数列表，须在首次调用 get_tools 前调用一次。"""
    load_func_tools()


def get_tools() -> list[llmApiUtil.OpenAITool]:
    """返回已初始化的工具列表。"""
    return build_tools(_func_tools.values())


def get_tools_by_names(
    names: list[str],
) -> list[llmApiUtil.OpenAITool]:
    """根据名称列表从注册表构建并返回对应工具的 schema 列表。"""
    return build_tools([
        _func_tools[name]
        for name in names
        if name in _func_tools
    ])


async def run_tool_call(
    function_args: str,
    context: Optional[ToolCallContext] = None,
) -> dict[str, Any]:
    """解析 function_args JSON 字符串并执行函数，返回结果字典。"""
    function_name = context.tool_name if context is not None else ""
    if not function_name:
        logger.error("函数执行失败: tool_name 为空")
        return {"success": False, "message": "函数执行失败: tool_name 为空"}

    # ── 阶段 1：JSON 解析 ──
    # 先尝试直接解析；失败后清理已知乱码 token 再试；仍失败则返回明确错误。
    raw_args = function_args.strip()
    try:
        args: dict = json.loads(raw_args)
    except json.JSONDecodeError:
        cleaned = _clean_garbled_json(raw_args)
        if cleaned != raw_args:
            try:
                args = json.loads(cleaned)
                logger.warning(
                    "工具参数 JSON 解析成功（清理乱码 token 后）: tool=%s, original_len=%d, cleaned_len=%d",
                    function_name, len(raw_args), len(cleaned),
                )
            except json.JSONDecodeError as e2:
                logger.warning(
                    "工具参数 JSON 解析失败: tool=%s, args=%r, error=%s",
                    function_name, raw_args[:200], e2,
                )
                return {
                    "success": False,
                    "message": (
                        f"函数 {function_name} 的参数 JSON 格式错误，无法解析。"
                        f"请检查参数格式是否正确。错误详情: {e2}"
                    ),
                }
        else:
            logger.warning(
                "工具参数 JSON 解析失败: tool=%s, args=%r",
                function_name, raw_args[:200],
            )
            return {
                "success": False,
                "message": (
                    f"函数 {function_name} 的参数 JSON 格式错误，无法解析。"
                    f"请检查参数格式是否正确。原始参数: {raw_args[:200]!r}"
                ),
            }

    # 确保 args 是 dict（LLM 可能生成 list 或其他类型）
    if not isinstance(args, dict):
        logger.warning(
            "工具参数不是 JSON 对象: tool=%s, type=%s", function_name, type(args).__name__,
        )
        return {
            "success": False,
            "message": (
                f"函数 {function_name} 的参数必须是 JSON 对象（键值对），"
                f"实际传入的是 {type(args).__name__} 类型。"
                f"请使用 {{\"key\": \"value\"}} 格式传递参数。"
            ),
        }

    # ── 阶段 2：arguments 包装解包 ──
    # 部分 LLM 会将参数嵌套在 "arguments" 键下，检测并自动解包。
    args, was_unwrapped = _unwrap_arguments_wrapper(args, function_name)

    caller = context.agent_id if context is not None else "unknown"
    logger.info("use_tool: caller_id=%s, tool=%s, args=%s", caller, function_name, args)

    try:
        func_tool = get_func_tool(function_name)
        func = func_tool.callable if func_tool is not None else None

        if func is None:
            raise ValueError(f"Function {function_name} not found")

        if not callable(func):
            raise ValueError(f"{function_name} is not callable")

        if context and "_context" in inspect.signature(func).parameters:
            args = {**args, "_context": context}

        result = func(**args)

        if inspect.isawaitable(result):
            result = await result

        if not isinstance(result, dict):
            result = {"success": True, "result": result}

        logger.info("函数执行结果: %s", result)
        return result

    except TypeError as e:
        # ── 阶段 3：Schema 感知的错误消息 ──
        # 不再只返回 Python 原始 TypeError，而是构造包含完整参数列表的消息，
        # 帮助 LLM 理解正确的参数格式并自纠正。
        error_str = str(e)
        unexpected_keys = _extract_unexpected_kwargs(error_str)
        missing_keys = _extract_missing_args(error_str)

        if func is not None:
            schema_msg = _build_schema_error_message(
                function_name=function_name,
                func=func,
                unexpected_keys=unexpected_keys,
                missing_keys=missing_keys,
                raw_error=error_str,
            )
        else:
            schema_msg = f"Invalid arguments for function {function_name}: {error_str}"

        logger.error("函数执行失败: %s", e)
        return {"success": False, "message": f"函数执行失败: {schema_msg}"}

    except Exception as e:
        error = str(e)
        logger.error("函数执行失败: %s", e)
        return {"success": False, "message": f"函数执行失败: {error}"}


def shutdown() -> None:
    """清空工具列表，程序退出前调用。"""
    global _func_tools
    _func_tools = {}
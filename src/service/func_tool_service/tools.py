from __future__ import annotations
import ast
from typing import Literal, Optional, List
import datetime
import logging
import operator
from zoneinfo import ZoneInfo

from model.chat_context import ChatContext
from service import room_service

logger = logging.getLogger(__name__)


def get_weather(location: str, unit: Literal["celsius", "fahrenheit"] = "celsius") -> str:
    """获取指定地点的天气信息

    Args:
        location: 城市 or 地点名称
        unit: 温度单位，celsius 或 fahrenheit
    """
    if unit == "celsius":
        return f"{location} 的天气: 25°C, 晴朗"
    else:
        return f"{location} 的天气: 77°F, 晴朗"


def get_time(timezone: Optional[str] = None) -> str:
    """获取当前时间

    Args:
        timezone: 可选的时区名称，如 "Asia/Shanghai"，默认使用本地时区
    """
    if timezone:
        try:
            tz = ZoneInfo(timezone)
            now = datetime.datetime.now(tz)
            return f"当前时间（时区 {timezone}）: {now.strftime('%Y-%m-%d %H:%M:%S')}"
        except Exception:
            return f"未知时区: {timezone}"
    else:
        now = datetime.datetime.now()
        return f"当前本地时间: {now.strftime('%Y-%m-%d %H:%M:%S')}"


_SAFE_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
}


def _safe_eval(node):
    if isinstance(node, ast.Constant):
        if not isinstance(node.value, (int, float)):
            raise ValueError("只支持数值常量")
        return node.value
    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _SAFE_OPS:
            raise ValueError(f"不支持的运算符: {op_type.__name__}")
        return _SAFE_OPS[op_type](_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _SAFE_OPS:
            raise ValueError(f"不支持的运算符: {op_type.__name__}")
        return _SAFE_OPS[op_type](_safe_eval(node.operand))
    raise ValueError(f"不支持的表达式类型: {type(node).__name__}")


def calculate(expression: str) -> str:
    """计算数学表达式

    Args:
        expression: 数学表达式字符串，如 "2 + 3 * 4"
    """
    try:
        tree = ast.parse(expression, mode="eval")
        result = _safe_eval(tree.body)
        return f"计算结果: {expression} = {result}"
    except Exception as e:
        return f"计算错误: {e}"


def get_agent_list(_context: ChatContext = None) -> List[str]:
    """返回当前聊天室的 agent 列表（历史发言者，排除 system）

    """
    logger.info(f"获取 agent 列表")
    if _context is None:
        return []
    return list(dict.fromkeys(
        m.sender_name for m in _context.chat_room.messages
        if m.sender_name != "system"
    ))


async def send_chat_msg(room_name: str, msg: str, _context: ChatContext = None) -> str:
    """向聊天窗口发送消息

    Args:
        room_name: 要发送消息的窗口名称
        msg: 要发送的消息

    Returns:
        成功返回 "success"
    """
    if _context is None:
        logger.warning("发送消息失败，聊天室上下文未设置")
        return "error: 当前没有可用的房间上下文。"

    room_key = room_name if "@" in room_name else f"{room_name}@{_context.team_name}"
    logger.info(f"发送消息: sender={_context.agent_name}, room={room_name}, msg={msg}")

    try:
        target_room = room_service.get_room(room_key)
    except Exception:
        logger.warning(f"send_chat_msg: 目标房间不存在 {room_key}")
        return f"error: 目标房间不存在: {room_key}"

    await target_room.add_message(_context.agent_name, msg)

    if target_room is _context.chat_room:
        return "success: 消息已发送，本轮发言结束。"

    assert _context.chat_room is not None, "send_chat_msg: 跨房间发言时 chat_room 不应为 None"

    return (
        f"success: 消息已发送到 {target_room.name}。"
        f"你还需要调用 send_chat_msg 向当前房间 {_context.chat_room.name} 发言，或调用 skip_chat_msg 跳过。"
    )


def skip_chat_msg(_context: ChatContext = None) -> str:
    """跳过本次发言。当你觉得当前话题不需要回复，或者没有话要说时调用此工具。

    Returns:
        成功返回 "success"
    """
    if _context is None or _context.chat_room is None:
        logger.warning("跳过发言失败，聊天室上下文未设置")
        return "error: 当前没有激活的房间上下文。"

    logger.info(f"Agent 跳过发言: agent={_context.agent_name}")
    ok = _context.chat_room.skip_turn(sender=_context.agent_name)

    if not ok:
        current = _context.chat_room.get_current_turn_agent()
        logger.warning(f"跳过发言失败，当前应由 {current} 发言: agent={_context.agent_name}")
        return f"error: 现在不是你的发言轮次（当前应由 {current} 发言），请勿再调用任何工具。"

    return "success: 已跳过本轮发言。"


def task_done() -> None:
    """通知任务完成
    """
    logger.info(f"task_done")
    return


FUNCTION_REGISTRY: dict[str, callable] = {
    "get_weather": get_weather,
    "get_time": get_time,
    "calculate": calculate,
    "get_agent_list": get_agent_list,
    "send_chat_msg": send_chat_msg,
    "skip_chat_msg": skip_chat_msg,
    "task_done": task_done,
}

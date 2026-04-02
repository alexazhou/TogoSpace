from __future__ import annotations
import ast
from typing import Callable, Literal, Optional, List
import datetime
import logging
import operator
from zoneinfo import ZoneInfo

from dal.db import gtRoomManager, gtTeamManager
from service.roomService import ToolCallContext
import service.roomService as roomService
from constants import SpecialAgent

logger = logging.getLogger(__name__)

# Tool 返回值规范
# 所有 tool 函数统一返回 dict，由 funcToolService.run_tool_call 序列化为 JSON 字符串后交给 LLM。
# 必填字段：
#   success: bool  — 操作是否成功
# 可选字段（按情况选用，不强制两者都有）：
#   message: str   — 文本信息（成功提示、错误说明等）
#   <其他字段>     — 结构化数据，字段名与语义一致，如 agents: list


def get_weather(location: str, unit: Literal["celsius", "fahrenheit"] = "celsius") -> dict:
    """获取指定地点的天气信息

    Args:
        location: 城市 or 地点名称
        unit: 温度单位，celsius 或 fahrenheit
    """
    if unit == "celsius":
        return {"success": True, "message": f"{location} 的天气: 25°C, 晴朗"}
    else:
        return {"success": True, "message": f"{location} 的天气: 77°F, 晴朗"}


def get_time(timezone: Optional[str] = None) -> dict:
    """获取当前时间

    Args:
        timezone: 可选的时区名称，如 "Asia/Shanghai"，默认使用本地时区
    """
    if timezone:
        try:
            tz = ZoneInfo(timezone)
            now = datetime.datetime.now(tz)
            return {"success": True, "message": f"当前时间（时区 {timezone}）: {now.strftime('%Y-%m-%d %H:%M:%S')}"}
        except Exception:
            return {"success": False, "message": f"未知时区: {timezone}"}
    else:
        now = datetime.datetime.now()
        return {"success": True, "message": f"当前本地时间: {now.strftime('%Y-%m-%d %H:%M:%S')}"}


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


def calculate(expression: str) -> dict:
    """计算数学表达式

    Args:
        expression: 数学表达式字符串，如 "2 + 3 * 4"
    """
    try:
        tree = ast.parse(expression, mode="eval")
        result = _safe_eval(tree.body)
        return {"success": True, "message": f"计算结果: {expression} = {result}"}
    except Exception as e:
        return {"success": False, "message": f"计算错误: {e}"}


def get_agent_list(_context: ToolCallContext = None) -> dict:
    """返回当前聊天室的 agent 列表（历史发言者，排除 system）"""
    logger.info(f"获取 agent 列表")
    if _context is None:
        return {"success": True, "agents": []}
    agents = list(dict.fromkeys(
        m.sender_name for m in _context.chat_room.messages
        if m.sender_name != SpecialAgent.SYSTEM.name
    ))
    return {"success": True, "agents": agents}


async def send_chat_msg(room_name: str, msg: str, _context: ToolCallContext = None) -> dict:
    """向聊天窗口发送消息

    Args:
        room_name: 要发送消息的窗口名称
        msg: 要发送的消息
    """
    if _context is None:
        logger.warning("发送消息失败，聊天室上下文未设置")
        return {"success": False, "message": "当前没有可用的房间上下文。"}

    logger.info(f"发送消息: sender={_context.agent_name}, room={room_name}, msg={msg}")

    try:
        target_room = roomService.get_room_by_key(f"{room_name}@{_context.team_name}")
    except Exception:
        try:
            team_row = await gtTeamManager.get_team(_context.team_name)
            room_config = None
            if team_row:
                team_rooms = await gtRoomManager.get_rooms_by_team(team_row.id)
                room_config = next((room for room in team_rooms if room.name == room_name), None)
            target_room = roomService.get_room(room_config.id) if room_config else None
        except Exception:
            target_room = None

        if target_room is None:
            logger.warning(f"send_chat_msg: 目标房间不存在 {room_name}@{_context.team_name}")
            return {"success": False, "message": f"目标房间不存在: {room_name}@{_context.team_name}"}

    if _context.chat_room is not None and target_room.room_id != _context.chat_room.room_id:
        if not target_room.can_post_message(_context.agent_name):
            logger.warning(
                "send_chat_msg: 发言者不在目标房间成员中 sender=%s room=%s@%s members=%s",
                _context.agent_name,
                room_name,
                _context.team_name,
                target_room.members,
            )
            return {"success": False, "message": f"你不在目标房间 {target_room.name} 中，发送失败。"}

    await target_room.add_message(_context.agent_name, msg)

    if target_room is _context.chat_room:
        return {"success": True, "message": f"消息已发送到 {_context.chat_room.name}。你可以继续调用工具，或者调用 finish_chat_turn 结束本轮行动。"}

    assert _context.chat_room is not None, "send_chat_msg: 跨房间发言时 chat_room 不应为 None"

    return {"success": True, "message": (
        f"消息已发送到 {target_room.name}。你还需要在 {_context.chat_room.name} 房间回复。你还可以继续调用工具，或者调用 finish_chat_turn 结束本轮行动。"
    )}


def finish_chat_turn(_context: ToolCallContext = None) -> dict:
    """结束本轮行动。当你完成所有发言和工具调用后，必须调用此工具来把行动机会让给下一位成员。
    如果你觉得当前话题不需要回复，或者没有话要说，请直接调用此工具来跳过本轮。"""
    if _context is None or _context.chat_room is None:
        logger.warning("结束行动失败，聊天室上下文未设置")
        return {"success": False, "message": "当前没有激活的房间上下文。"}

    logger.info(f"Agent 结束行动: agent={_context.agent_name}")
    ok = _context.chat_room.finish_turn(sender=_context.agent_name)

    if not ok:
        current = _context.chat_room.get_current_turn_agent()
        logger.warning(f"结束行动失败，当前应由 {current} 发言: agent={_context.agent_name}")
        return {"success": False, "message": f"现在不是你的发言轮次（当前应由 {current} 发言），请勿再调用任何工具。"}

    return {"success": True, "message": "已结束本轮行动。"}


def task_done() -> dict:
    """通知任务完成"""
    logger.info(f"task_done")
    return {"success": True}


FUNCTION_REGISTRY: dict[str, Callable[..., dict] | Callable[..., object]] = {
    "get_weather": get_weather,
    "get_time": get_time,
    "calculate": calculate,
    "get_agent_list": get_agent_list,
    "send_chat_msg": send_chat_msg,
    "finish_chat_turn": finish_chat_turn,
    "task_done": task_done,
}

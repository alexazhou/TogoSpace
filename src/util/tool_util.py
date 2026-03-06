from typing import Literal, Optional, List, TYPE_CHECKING

if TYPE_CHECKING:
    from model.chat_context import ChatContext
import ast
import datetime
import logging
import operator
from zoneinfo import ZoneInfo


def get_weather(location: str, unit: Literal["celsius", "fahrenheit"] = "celsius") -> str:
    """获取指定地点的天气信息

    Args:
        location: 城市或地点名称
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


def get_agent_list(_context: "ChatContext" = None) -> List[str]:
    """返回当前聊天室的 agent 列表（历史发言者，排除 system）

    """
    logging.info(f"get_agent_list: 获取 agent 列表")
    if _context is None:
        return []
    senders = []
    seen = set()
    for msg in _context.chat_room.messages:
        if msg.sender != "system" and msg.sender not in seen:
            seen.add(msg.sender)
            senders.append(msg.sender)
    return senders


def create_chat(room_name: str, _context: "ChatContext" = None) -> str:
    """切换到已存在的聊天室，返回房间名称；房间不存在则返回错误提示

    Args:
        room_name: 要切换到的聊天室名称
    """
    logging.info(f"create_chat: 切换到聊天室 {room_name}")
    if _context is None:
        return "错误：无法访问聊天室上下文"
    try:
        _context.get_room(room_name)
        return room_name
    except Exception:
        return f"错误：聊天室 '{room_name}' 不存在"


def send_chat_msg(chat_windows_name: str, msg: str, _context: "ChatContext" = None) -> str:
    """向聊天窗口发送消息

    Args:
        chat_windows_name: 要发送消息的窗口名称
        msg: 要发送的消息

    Returns:
        成功返回 "success"
    """
    logging.info(f"send_chat_msg: 向 {chat_windows_name} 发送消息: {msg}")

    if _context is not None:
        try:
            target_room = _context.get_room(chat_windows_name)
            target_room.add_message(_context.agent_name, msg)
        except Exception:
            logging.warning(f"send_chat_msg: 聊天室 '{chat_windows_name}' 不存在，消息已忽略")
    else:
        logging.warning("send_chat_msg: 聊天室上下文未设置")

    return "success"

def task_done() -> None:
    """通知任务完成
    """
    logging.info(f"task_done")
    return


for _f in (get_agent_list, create_chat, send_chat_msg):
    _f.needs_context = True

FUNCTION_REGISTRY: dict[str, callable] = {
    "get_weather": get_weather,
    "get_time": get_time,
    "calculate": calculate,
    "get_agent_list": get_agent_list,
    "create_chat": create_chat,
    "send_chat_msg": send_chat_msg,
    "task_done": task_done,
}
from typing import Literal, Optional, List
import ast
import datetime
import logging
import operator


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
        # 简化处理，实际应使用 pytz 或 zoneinfo
        now = datetime.datetime.now(datetime.timezone.utc)
        return f"当前时间（时区 {timezone}）: {now.strftime('%Y-%m-%d %H:%M:%S')} UTC"
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


def get_agent_list() -> List[str]:
    """返回 agent 列表

    """
    logging.info(f"get_agent_list: 获取 agent 列表")
    return ['agent1','agent2','agent3']


def create_chat(agent_name: str) -> str:
    """创建和一个 agent 的聊天，返回创建的聊天窗口名称

    Args:
        agent_name: 发起聊天的目标 agent 名称
    """
    logging.info(f"create_chat: 创建与 {agent_name} 的聊天")
    return f"to_{agent_name}_room"


def send_chat_msg(chat_windows_name: str, msg: str, _chat_room=None, _agent_name=None) -> str:
    """向聊天窗口发送消息

    Args:
        chat_windows_name: 要发送消息的窗口名称
        msg: 要发送的消息

    Returns:
        成功返回 "success"
    """
    logging.info(f"send_chat_msg: 向 {chat_windows_name} 发送消息: {msg}")

    if _chat_room is not None:
        _chat_room.add_message(_agent_name, msg)
    else:
        logging.warning("send_chat_msg: 聊天室上下文未设置")

    return "success"

def task_done() -> None:
    """通知任务完成
    """
    logging.info(f"task_done")
    return


send_chat_msg.needs_context = True

FUNCTION_REGISTRY: dict[str, callable] = {
    "get_weather": get_weather,
    "get_time": get_time,
    "calculate": calculate,
    "get_agent_list": get_agent_list,
    "create_chat": create_chat,
    "send_chat_msg": send_chat_msg,
    "task_done": task_done,
}
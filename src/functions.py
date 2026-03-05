from typing import Literal, Optional, List
import datetime
import logging

# 全局变量：当前聊天室上下文
_current_chat_room = None
_current_agent_name = None


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


def calculate(expression: str) -> str:
    """计算数学表达式

    Args:
        expression: 数学表达式字符串，如 "2 + 3 * 4"
    """
    try:
        result = eval(expression, {"__builtins__": {}}, {})
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


def set_chat_context(chat_room, agent_name: str) -> None:
    """设置函数执行的上下文

    Args:
        chat_room: 聊天室对象
        agent_name: 当前 agent 名称
    """
    global _current_chat_room, _current_agent_name
    _current_chat_room = chat_room
    _current_agent_name = agent_name
    logging.info(f"set_chat_context: 设置上下文 - agent={agent_name}")


def send_chat_msg(chat_windows_name: str, msg: str) -> str:
    """向聊天窗口发送消息

    Args:
        chat_windows_name: 要发送消息的窗口名称
        msg: 要发送的消息

    Returns:
        成功返回 "success"
    """
    global _current_chat_room, _current_agent_name
    logging.info(f"send_chat_msg: 向 {chat_windows_name} 发送消息: {msg}")

    # 通过聊天室添加消息
    if _current_chat_room is not None:
        _current_chat_room.add_message(_current_agent_name, msg)
    else:
        logging.warning("send_chat_msg: 聊天室上下文未设置")

    return "success"

def task_done() -> None:
    """通知任务完成
    """
    logging.info(f"task_done")
    return
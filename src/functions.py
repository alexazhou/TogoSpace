from typing import Literal, Optional
import datetime


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

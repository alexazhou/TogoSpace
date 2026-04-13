"""Windows 平台实现。"""

import sys
from typing import Any

if sys.platform != "win32":
    raise ImportError("Windows PAL 只能在 Windows 上加载")

import pystray


def _setup_app() -> Any:
    """初始化 Windows 应用，无特殊处理。"""
    return None


def _get_icon_kwargs() -> dict:
    """获取 pystray.Icon 的 Windows 特定参数，返回空 dict。"""
    return {}


def _apply_tray_icon(icon: pystray.Icon) -> bool:
    """Windows 托盘图标处理，使用 pystray 默认行为。"""
    return False
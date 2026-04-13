"""Linux 平台实现。"""

import sys
from typing import Any

if sys.platform not in ("linux", "linux2"):
    raise ImportError("Linux PAL 只能在 Linux 上加载")

import pystray


def _setup_app() -> Any:
    """初始化 Linux 应用，无特殊处理。"""
    return None


def _get_icon_kwargs() -> dict:
    """获取 pystray.Icon 的 Linux 特定参数，返回空 dict。"""
    return {}


def _apply_tray_icon(icon: pystray.Icon) -> bool:
    """Linux 托盘图标处理，使用 pystray 默认行为。"""
    return False
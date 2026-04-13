"""平台抽象层核心接口。

提供统一的平台无关接口，内部根据平台加载对应实现。
"""

import sys
from typing import Any

import pystray

# ── 平台检测 ───────────────────────────────────────────────────────────────

_current_platform = sys.platform

if _current_platform == "darwin":
    from .macos import _setup_app, _apply_tray_icon, _get_icon_kwargs
elif _current_platform == "win32":
    from .windows import _setup_app, _apply_tray_icon, _get_icon_kwargs
elif _current_platform in ("linux", "linux2"):
    from .linux import _setup_app, _apply_tray_icon, _get_icon_kwargs
else:
    raise ImportError(f"不支持的平台: {_current_platform}")

# ── 公开接口 ───────────────────────────────────────────────────────────────


def setup_app() -> Any:
    """初始化平台应用，macOS 返回 NSApplication，其他平台返回 None。"""
    return _setup_app()


def apply_tray_icon(icon: pystray.Icon) -> bool:
    """应用托盘图标，macOS 尝试 SF Symbols，其他平台使用默认图标。"""
    return _apply_tray_icon(icon)


def get_icon_kwargs() -> dict:
    """获取 pystray.Icon 的平台特定参数，macOS 返回 nsapplication，其他平台返回空 dict。"""
    return _get_icon_kwargs()


__all__ = ["setup_app", "apply_tray_icon", "get_icon_kwargs"]
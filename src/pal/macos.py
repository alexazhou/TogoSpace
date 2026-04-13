"""macOS 平台实现。"""

import sys
from typing import Any

if sys.platform != "darwin":
    raise ImportError("macOS PAL 只能在 macOS 上加载")

import AppKit
import pystray


def _setup_app() -> Any:
    """初始化 macOS 应用，设置 Accessory 模式（无 Dock 图标）。"""
    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    return app


def _get_icon_kwargs() -> dict:
    """获取 pystray.Icon 的 macOS 特定参数。"""
    return {"nsapplication": _setup_app()}


def _apply_tray_icon(icon: pystray.Icon) -> bool:
    """应用托盘图标，尝试 SF Symbols，失败则回退到模板模式。"""
    button = icon._status_item.button()

    if button is None:
        return False

    # 检查 SF Symbols 支持
    symbol_factory = getattr(AppKit.NSImage, "imageWithSystemSymbolName_accessibilityDescription_", None)

    if symbol_factory is None:
        _fallback_tray_icon(icon)
        return True

    # 创建 pawprint 图标
    symbol = symbol_factory("pawprint.fill", None)

    if symbol is None:
        _fallback_tray_icon(icon)
        return True

    # 配置大小和权重
    config_factory = getattr(AppKit.NSImageSymbolConfiguration, "configurationWithPointSize_weight_scale_", None)

    if config_factory is not None:
        symbol = symbol.imageWithSymbolConfiguration_(
            config_factory(13, AppKit.NSFontWeightMedium, AppKit.NSImageSymbolScaleMedium)
        )

    # 设置为模板图标（自动适配深色/浅色模式）
    symbol.setTemplate_(True)
    button.setImage_(symbol)
    return True


def _fallback_tray_icon(icon: pystray.Icon) -> None:
    """将现有图标设为模板模式，适配深色/浅色模式。"""
    button = icon._status_item.button()

    if button is not None and button.image() is not None:
        button.image().setTemplate_(True)
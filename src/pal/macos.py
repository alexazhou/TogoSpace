"""macOS 平台实现。"""

import os
import sys
from typing import Any

if sys.platform != "darwin":
    raise ImportError("macOS PAL 只能在 macOS 上加载")

import AppKit
import pystray

import appPaths


def _setup_app() -> Any:
    """初始化 macOS 应用，设置 Accessory 模式（无 Dock 图标）。"""
    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    return app


def _get_icon_kwargs() -> dict:
    """获取 pystray.Icon 的 macOS 特定参数。"""
    return {"nsapplication": _setup_app()}


def _apply_tray_icon(icon: pystray.Icon) -> bool:
    """应用托盘图标，优先使用原生 NSImage，避免 pystray 先缩小位图。"""
    button = icon._status_item.button()

    if button is None:
        return False

    image = _load_native_template_image(icon)
    if image is not None:
        button.setImage_(image)
        icon._native_icon_image = image
        return True

    _fallback_tray_icon(icon)
    return True


def _fallback_tray_icon(icon: pystray.Icon) -> None:
    """将现有图标设为模板模式，适配深色/浅色模式。"""
    button = icon._status_item.button()

    if button is not None and button.image() is not None:
        button.image().setTemplate_(True)


def _load_native_template_image(icon: pystray.Icon) -> Any | None:
    """直接加载高分辨率图标并交给 NSStatusBarButton 处理。"""
    icons_dir = os.path.join(appPaths.ASSETS_DIR, "icons")
    icon_candidates = ["togo_status_64.png", "togo_status_32.png", "togo_status_16.png"]

    icon_path = next(
        (
            os.path.join(icons_dir, icon_name)
            for icon_name in icon_candidates
            if os.path.exists(os.path.join(icons_dir, icon_name))
        ),
        None,
    )
    if icon_path is None:
        return None

    image = AppKit.NSImage.alloc().initWithContentsOfFile_(icon_path)
    if image is None:
        return None

    thickness = icon._status_bar.thickness()
    image.setSize_((thickness, thickness))
    image.setTemplate_(True)

    button = icon._status_item.button()
    scaling = getattr(AppKit, "NSImageScaleProportionallyUpOrDown", None)
    if button is not None and scaling is not None:
        button.setImageScaling_(scaling)

    return image

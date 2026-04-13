import asyncio
import os
import shutil
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import messagebox

import pystray
from PIL import Image, ImageDraw

import appPaths
import backend_main
from util import configUtil
from version import __version__

# 后端状态，由后端线程写入，菜单回调读取
_backend_status: str = ""
_tray_icon: pystray.Icon | None = None
_web_url: str = "http://localhost:8080"  # 启动后更新

# ── 托盘界面翻译 ──────────────────────────────────────────────────────────────

_TRAY_STRINGS: dict[str, dict[str, str]] = {
    "status_starting": {"zh-CN": "启动中…", "en": "Starting…"},
    "status_running": {"zh-CN": "运行中", "en": "Running"},
    "status_stopped": {"zh-CN": "已停止", "en": "Stopped"},
    "status_error": {"zh-CN": "启动失败: {e}", "en": "Failed to start: {e}"},
    "status_label": {"zh-CN": "状态: {s}", "en": "Status: {s}"},
    "open_web": {"zh-CN": "打开 Web 界面", "en": "Open Web UI"},
    "open_config_dir": {"zh-CN": "打开配置目录", "en": "Open Config Dir"},
    "reset_data": {"zh-CN": "重置数据", "en": "Reset Data"},
    "version": {"zh-CN": "版本: v{v}", "en": "Version: v{v}"},
    "quit": {"zh-CN": "退出", "en": "Quit"},
    "confirm_reset": {
        "zh-CN": "确定要重置所有数据吗？\n所有聊天室、成员、消息记录将被删除，此操作不可撤销。",
        "en": "Are you sure you want to reset all data?\nAll chat rooms, members, and messages will be deleted. This cannot be undone.",
    },
    "reset_done_title": {"zh-CN": "重置成功", "en": "Reset Complete"},
    "reset_done_body": {"zh-CN": "数据已清除，请重新启动程序。", "en": "Data cleared. Please restart the application."},
    "language_menu": {"zh-CN": "语言 / Language", "en": "Language / 语言"},
    "lang_zh": {"zh-CN": "简体中文", "en": "简体中文 (Chinese)"},
    "lang_en": {"zh-CN": "English", "en": "English"},
}


def _tray_t(key: str, **kwargs: object) -> str:
    lang = configUtil.get_language() if configUtil.is_initialized() else "zh-CN"
    entry = _TRAY_STRINGS.get(key, {})
    text = entry.get(lang) or entry.get("zh-CN") or key
    if kwargs:
        text = text.format(**kwargs)
    return text


def _set_status(status: str) -> None:
    global _backend_status
    _backend_status = status
    if _tray_icon is not None:
        _tray_icon.update_menu()


# ── 后端线程 ──────────────────────────────────────────────────────────────────

def _run_backend() -> None:
    global _web_url

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # 加载配置获取 bind_host 和 bind_port
    app_config = configUtil.load()
    bind_host = app_config.setting.bind_host
    bind_port = app_config.setting.bind_port
    _web_url = f"http://localhost:{bind_port}"

    try:
        _set_status(_tray_t("status_running"))
        loop.run_until_complete(backend_main.main(port=bind_port))
        _set_status(_tray_t("status_stopped"))
    except Exception as e:
        _set_status(_tray_t("status_error", e=e))
    finally:
        loop.close()


# ── 图标 & 菜单 ───────────────────────────────────────────────────────────────

def _make_icon() -> Image.Image:
    import os
    icons_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "icons")
    icon_candidates = ["togo_status_32.png", "togo_status_64.png", "togo_status_16.png"]
    for icon_name in icon_candidates:
        icon_path = os.path.join(icons_dir, icon_name)
        if os.path.exists(icon_path):
            return Image.open(icon_path)
    
    img = Image.new("RGBA", (22, 22), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rectangle((4, 6,  18, 8),  fill=(0, 0, 0, 255))
    draw.rectangle((4, 11, 18, 13), fill=(0, 0, 0, 255))
    draw.rectangle((4, 16, 18, 18), fill=(0, 0, 0, 255))
    return img


def _apply_macos_status_symbol(icon: pystray.Icon) -> None:
    import AppKit

    button = icon._status_item.button()
    if button is None:
        return

    symbol_factory = getattr(AppKit.NSImage, "imageWithSystemSymbolName_accessibilityDescription_", None)
    if symbol_factory is None:
        return

    symbol = symbol_factory("pawprint.fill", None)
    if symbol is None:
        return

    config_factory = getattr(AppKit.NSImageSymbolConfiguration, "configurationWithPointSize_weight_scale_", None)
    if config_factory is not None:
        symbol = symbol.imageWithSymbolConfiguration_(
            config_factory(13, AppKit.NSFontWeightMedium, AppKit.NSImageSymbolScaleMedium)
        )

    symbol.setTemplate_(True)
    button.setImage_(symbol)


def _status_text(item) -> str:
    return _tray_t("status_label", s=_backend_status)


def _on_open(icon, item) -> None:
    webbrowser.open(_web_url, new=0)


def _on_quit(icon, item) -> None:
    backend_main.request_shutdown()
    icon.stop()


def _on_open_config_dir(icon, item) -> None:
    config_dir = os.path.expanduser("~/.togo_agent")
    os.makedirs(config_dir, exist_ok=True)
    if sys.platform == "darwin":
        subprocess.Popen(["open", config_dir])
    elif sys.platform == "win32":
        subprocess.Popen(["explorer", config_dir])
    else:
        subprocess.Popen(["xdg-open", config_dir])


def _tk_dialog(fn, *args, **kwargs):
    """在隐藏的 Tk 根窗口上弹出对话框，完成后销毁根窗口。"""
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        return fn(*args, parent=root, **kwargs)
    finally:
        root.destroy()


def _confirm(message: str) -> bool:
    return _tk_dialog(messagebox.askyesno, "TogoAgent", message, icon="warning")


def _alert(title: str, message: str) -> None:
    _tk_dialog(messagebox.showinfo, title, message)


def _on_reset_data(icon, item) -> None:
    if not _confirm(_tray_t("confirm_reset")):
        return

    backend_main.request_shutdown()

    data_dir = appPaths.DATA_DIR
    if os.path.isdir(data_dir):
        shutil.rmtree(data_dir)

    _alert(_tray_t("reset_done_title"), _tray_t("reset_done_body"))
    icon.stop()


def _on_set_language(lang: str) -> None:
    """切换语言并重建菜单。"""
    configUtil.set_language(lang)
    if _tray_icon is not None:
        _tray_icon.menu = _build_menu()
        _tray_icon.update_menu()


def _build_menu() -> pystray.Menu:
    current_lang = configUtil.get_language() if configUtil.is_initialized() else "zh-CN"
    return pystray.Menu(
        pystray.MenuItem(_status_text, None, enabled=False),
        pystray.MenuItem(_tray_t("open_web"), _on_open),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(_tray_t("open_config_dir"), _on_open_config_dir),
        pystray.MenuItem(_tray_t("reset_data"), _on_reset_data),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            _tray_t("language_menu"),
            pystray.Menu(
                pystray.MenuItem(
                    _tray_t("lang_zh"),
                    lambda icon, item: _on_set_language("zh-CN"),
                    checked=lambda item: current_lang == "zh-CN",
                    radio=True,
                ),
                pystray.MenuItem(
                    _tray_t("lang_en"),
                    lambda icon, item: _on_set_language("en"),
                    checked=lambda item: current_lang == "en",
                    radio=True,
                ),
            ),
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(_tray_t("version", v=__version__), None, enabled=False),
        pystray.MenuItem(_tray_t("quit"), _on_quit),
    )


def _setup(icon: pystray.Icon) -> None:
    global _tray_icon
    _tray_icon = icon

    icon.visible = True
    if sys.platform == "darwin":
        try:
            _apply_macos_status_symbol(icon)
        except Exception:
            # Fall back to the packaged PNG icon if SF Symbols are unavailable.
            button = icon._status_item.button()
            if button is not None and button.image() is not None:
                button.image().setTemplate_(True)

    _set_status(_tray_t("status_starting"))
    threading.Thread(target=_run_backend, daemon=True).start()


def _build_icon() -> pystray.Icon:
    kwargs = {}
    if sys.platform == "darwin":
        import AppKit
        app = AppKit.NSApplication.sharedApplication()
        app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
        kwargs["nsapplication"] = app

    return pystray.Icon(
        name="TogoAgent",
        icon=_make_icon(),
        title="TogoAgent",
        menu=_build_menu(),
        **kwargs,
    )


# ── 入口 ──────────────────────────────────────────────────────────────────────

def main():
    # 打包模式：静态资源指向 _MEIPASS/assets/，可写数据指向 ~/.togo_agent/
    if getattr(sys, "frozen", False):
        appPaths.ASSETS_DIR    = os.path.join(sys._MEIPASS, "assets")
        appPaths.PRESET_DIR    = os.path.join(appPaths.ASSETS_DIR, "preset")
        _user_dir              = os.path.expanduser("~/.togo_agent")
        appPaths.DATA_DIR      = os.path.join(_user_dir, "data")
        appPaths.LOGS_DIR      = os.path.join(_user_dir, "logs", "backend")
        appPaths.WORKSPACE_ROOT = os.path.join(_user_dir, "workspace")
    icon = _build_icon()
    icon.run(setup=_setup)


if __name__ == "__main__":
    main()

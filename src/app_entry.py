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
_backend_status: str = "启动中…"
_backend_loop: asyncio.AbstractEventLoop | None = None
_tray_icon: pystray.Icon | None = None
_web_url: str = "http://localhost:8080"  # 启动后更新


def _set_status(status: str) -> None:
    global _backend_status
    _backend_status = status
    if _tray_icon is not None:
        _tray_icon.update_menu()


# ── 后端线程 ──────────────────────────────────────────────────────────────────

def _run_backend() -> None:
    global _backend_loop, _web_url

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _backend_loop = loop

    # 加载配置获取 bind_host 和 bind_port
    app_config = configUtil.load()
    bind_host = app_config.setting.bind_host
    bind_port = app_config.setting.bind_port
    _web_url = f"http://localhost:{bind_port}"

    try:
        _set_status("运行中")
        loop.run_until_complete(backend_main.main(port=bind_port))
        _set_status("已停止")
    except Exception as e:
        _set_status(f"启动失败: {e}")
    finally:
        loop.close()
        _backend_loop = None


# ── 图标 & 菜单 ───────────────────────────────────────────────────────────────

def _make_icon() -> Image.Image:
    img = Image.new("RGBA", (22, 22), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rectangle((4, 6,  18, 8),  fill=(0, 0, 0, 255))
    draw.rectangle((4, 11, 18, 13), fill=(0, 0, 0, 255))
    draw.rectangle((4, 16, 18, 18), fill=(0, 0, 0, 255))
    return img


def _status_text(item) -> str:
    return f"状态: {_backend_status}"


def _on_open(icon, item) -> None:
    webbrowser.open(_web_url)


def _on_quit(icon, item) -> None:
    if _backend_loop and not _backend_loop.is_closed():
        _backend_loop.call_soon_threadsafe(_backend_loop.stop)
    icon.stop()


def _on_open_config_dir(icon, item) -> None:
    config_dir = os.path.expanduser("~/.agent_team")
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
    return _tk_dialog(messagebox.askyesno, "AgentTeam", message, icon="warning")


def _alert(title: str, message: str) -> None:
    _tk_dialog(messagebox.showinfo, title, message)


def _on_reset_data(icon, item) -> None:
    if not _confirm("确定要重置所有数据吗？\n所有聊天室、成员、消息记录将被删除，此操作不可撤销。"):
        return

    if _backend_loop and not _backend_loop.is_closed():
        _backend_loop.call_soon_threadsafe(_backend_loop.stop)

    data_dir = appPaths.DATA_DIR
    if os.path.isdir(data_dir):
        shutil.rmtree(data_dir)

    _alert("重置成功", "数据已清除，请重新启动程序。")
    icon.stop()


def _setup(icon: pystray.Icon) -> None:
    global _tray_icon
    _tray_icon = icon

    icon.visible = True
    if sys.platform == "darwin":
        icon._status_item.button().image().setTemplate_(True)

    threading.Thread(target=_run_backend, daemon=True).start()


def _build_icon() -> pystray.Icon:
    kwargs = {}
    if sys.platform == "darwin":
        import AppKit
        app = AppKit.NSApplication.sharedApplication()
        app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
        kwargs["nsapplication"] = app

    return pystray.Icon(
        name="AgentTeam",
        icon=_make_icon(),
        title="AgentTeam",
        menu=pystray.Menu(
            pystray.MenuItem(_status_text, None, enabled=False),
            pystray.MenuItem("打开 Web 界面", _on_open),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("打开配置目录", _on_open_config_dir),
            pystray.MenuItem("重置数据", _on_reset_data),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(f"版本: v{__version__}", None, enabled=False),
            pystray.MenuItem("退出", _on_quit),
        ),
        **kwargs,
    )


# ── 入口 ──────────────────────────────────────────────────────────────────────

def main():
    # 打包模式：静态资源指向 _MEIPASS/assets/，可写数据指向 ~/.agent_team/
    if getattr(sys, "frozen", False):
        appPaths.ASSETS_DIR    = os.path.join(sys._MEIPASS, "assets")
        _user_dir              = os.path.expanduser("~/.agent_team")
        appPaths.DATA_DIR      = os.path.join(_user_dir, "data")
        appPaths.LOGS_DIR      = os.path.join(_user_dir, "logs", "backend")
        appPaths.WORKSPACE_ROOT = os.path.join(_user_dir, "workspace")
    icon = _build_icon()
    icon.run(setup=_setup)


if __name__ == "__main__":
    main()

import asyncio
import sys
import threading
import webbrowser

import pystray
from PIL import Image, ImageDraw

import backend_main
from version import __version__

_DEFAULT_PORT = 8080
_WEB_URL = f"http://localhost:{_DEFAULT_PORT}"

# 后端状态，由后端线程写入，菜单回调读取
_backend_status: str = "启动中…"
_backend_loop: asyncio.AbstractEventLoop | None = None
_tray_icon: pystray.Icon | None = None


def _set_status(status: str) -> None:
    global _backend_status
    _backend_status = status
    if _tray_icon is not None:
        _tray_icon.update_menu()


# ── 后端线程 ──────────────────────────────────────────────────────────────────

def _run_backend() -> None:
    global _backend_loop

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _backend_loop = loop

    try:
        _set_status("运行中")
        loop.run_until_complete(backend_main.main(port=_DEFAULT_PORT))
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
    webbrowser.open(_WEB_URL)


def _on_quit(icon, item) -> None:
    if _backend_loop and not _backend_loop.is_closed():
        _backend_loop.call_soon_threadsafe(_backend_loop.stop)
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
            pystray.MenuItem(f"版本: v{__version__}", None, enabled=False),
            pystray.MenuItem("退出", _on_quit),
        ),
        **kwargs,
    )


# ── 入口 ──────────────────────────────────────────────────────────────────────

def main():
    icon = _build_icon()
    icon.run(setup=_setup)


if __name__ == "__main__":
    main()

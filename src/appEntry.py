"""托盘入口。

负责托盘图标创建、后端线程启动、菜单管理。
平台特定逻辑通过 PAL 模块封装，支持 macOS / Windows / Linux。
"""

import asyncio
import logging
import os
import threading

import pystray
from PIL import Image, ImageDraw

import backend_main
import pal
from trayMenu import TrayMenu
from util import configUtil, i18nUtil
from version import __version__

_logger = logging.getLogger(__name__)

# ── 全局状态 ───────────────────────────────────────────────────────────────

_tray_icon: pystray.Icon | None = None
_web_url: str = "http://localhost:8080"
_tray_menu: TrayMenu | None = None
_backend_thread: threading.Thread | None = None

# ── 后端线程 ───────────────────────────────────────────────────────────────

def _run_backend() -> None:
    global _web_url

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # 加载配置
    app_config = configUtil.load()
    bind_host = app_config.setting.bind_host
    bind_port = app_config.setting.bind_port
    _web_url = f"http://localhost:{bind_port}"

    # 配置加载后重建菜单，应用正确的语言设置
    if _tray_icon is not None and _tray_menu is not None:
        _tray_icon.menu = _tray_menu.build()
        _tray_icon.update_menu()

    try:
        _tray_menu.set_status("status_running")
        loop.run_until_complete(backend_main.main(port=bind_port))
        _tray_menu.set_status("status_stopped")
    except Exception as e:
        _logger.error("后端启动失败: %s", e, exc_info=True)
        _tray_menu.set_status("status_error", e=e)
    finally:
        loop.close()

def wait_backend_shutdown(timeout: float = 5.0) -> bool:
    """等待后端线程关闭完成。

    Args:
        timeout: 最大等待时间（秒）

    Returns:
        True 如果后端已关闭，False 如果超时
    """
    global _backend_thread
    if _backend_thread is None:
        return True
    _backend_thread.join(timeout=timeout)
    return not _backend_thread.is_alive()

# ── 图标 ───────────────────────────────────────────────────────────────────

def _make_icon() -> Image.Image:
    """加载图标文件，若不存在则绘制简单图形。"""
    icons_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "icons")
    icon_candidates = ["togo_status_32.png", "togo_status_64.png", "togo_status_16.png"]
    for icon_name in icon_candidates:
        icon_path = os.path.join(icons_dir, icon_name)
        if os.path.exists(icon_path):
            return Image.open(icon_path)

    img = Image.new("RGBA", (22, 22), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rectangle((4, 6, 18, 8), fill=(0, 0, 0, 255))
    draw.rectangle((4, 11, 18, 13), fill=(0, 0, 0, 255))
    draw.rectangle((4, 16, 18, 18), fill=(0, 0, 0, 255))
    return img

# ── 回调 ───────────────────────────────────────────────────────────────────

def _on_quit(icon: pystray.Icon) -> None:
    """退出程序：停止后端，关闭托盘。"""
    backend_main.request_shutdown()
    icon.stop()

def _on_reset() -> bool:
    """请求后端关闭（不等待完成）。"""
    backend_main.request_shutdown()
    # 返回 True 表示已请求关闭，不等待后端线程完成
    # daemon 线程会在进程退出时被强制终止
    return True

# ── 托盘生命周期 ───────────────────────────────────────────────────────────

def _on_tray_ready(icon: pystray.Icon) -> None:
    """托盘图标就绪后启动后端线程。"""
    global _tray_icon, _backend_thread

    _tray_icon = icon
    icon.visible = True

    # 应用平台特定的托盘图标处理
    pal.apply_tray_icon(icon)

    _tray_menu.set_status("status_starting")
    _backend_thread = threading.Thread(target=_run_backend, daemon=True)
    _backend_thread.start()


def build_tray_icon() -> pystray.Icon:
    global _tray_menu

    # 创建菜单管理器
    _tray_menu = TrayMenu(tray_icon=None, web_url=_web_url, on_quit=_on_quit, on_reset=_on_reset)
    _tray_menu.set_version(__version__)

    icon = pystray.Icon(
        name="TogoSpace",
        icon=_make_icon(),
        title="TogoSpace",
        menu=_tray_menu.build(),
        **pal.get_icon_kwargs())
    _tray_menu._icon = icon
    return icon

# ── 入口 ───────────────────────────────────────────────────────────────────

def main():
    icon = build_tray_icon()
    icon.run(setup=_on_tray_ready)


if __name__ == "__main__":
    main()

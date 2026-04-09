import sys
import webbrowser

import pystray
from PIL import Image, ImageDraw

VERSION = "0.1.0"
WEB_URL = "http://localhost:8080"


def _make_icon() -> Image.Image:
    img = Image.new("RGBA", (22, 22), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rectangle((4, 6,  18, 8),  fill=(0, 0, 0, 255))
    draw.rectangle((4, 11, 18, 13), fill=(0, 0, 0, 255))
    draw.rectangle((4, 16, 18, 18), fill=(0, 0, 0, 255))
    return img


def _setup(icon: pystray.Icon) -> None:
    icon.visible = True
    if sys.platform == "darwin":
        # macOS template image 自动适配深/浅色菜单栏
        icon._status_item.button().image().setTemplate_(True)


def _on_open(icon, item) -> None:
    webbrowser.open(WEB_URL)


def _on_quit(icon, item) -> None:
    icon.stop()


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
            pystray.MenuItem("状态: 启动中…", None, enabled=False),
            pystray.MenuItem("打开 Web 界面", _on_open),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(f"版本: v{VERSION}", None, enabled=False),
            pystray.MenuItem("退出", _on_quit),
        ),
        **kwargs,
    )


def main():
    icon = _build_icon()
    icon.run(setup=_setup)


if __name__ == "__main__":
    main()

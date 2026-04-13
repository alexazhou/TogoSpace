"""平台抽象层。

封装平台特定逻辑，支持 macOS / Windows / Linux。

使用方式：
    from pal import get_icon_kwargs, apply_tray_icon

内部根据平台自动加载对应实现，外层无需关心平台差异。
"""

from .core import apply_tray_icon, get_icon_kwargs, setup_app

__all__ = ["setup_app", "apply_tray_icon", "get_icon_kwargs"]
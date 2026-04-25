"""托盘菜单封装。

将菜单构建、回调处理、状态显示封装在一个类中。
"""

import os
import shutil
import subprocess
import sys
import webbrowser
from typing import Callable

import pystray

import appPaths
from util import configUtil, i18nUtil


class TrayMenu:
    """托盘菜单管理，负责菜单构建、回调处理和状态显示。"""

    def __init__(
        self,
        tray_icon: pystray.Icon | None,
        web_url: str,
        on_quit: Callable[[pystray.Icon], None],
        on_reset: Callable[[], bool] | None = None,
    ):
        """
        Args:
            tray_icon: pystray Icon 实例，用于更新菜单
            web_url: Web 界面地址
            on_quit: 退出回调，用于停止后端和托盘
            on_reset: 重置数据回调，负责停止后端并等待关闭完成，返回 True 表示成功关闭
        """
        self._icon = tray_icon
        self._web_url = web_url
        self._on_quit = on_quit
        self._on_reset = on_reset
        self._status_key: str = ""
        self._status_kwargs: dict[str, object] = {}
        self._version: str = ""

    # ── 状态管理 ─────────────────────────────────────────────────────────────

    def set_status(self, status_key: str, **kwargs: object) -> None:
        """更新状态并刷新菜单显示。"""
        self._status_key = status_key
        self._status_kwargs = kwargs
        if self._icon is not None:
            self._icon.update_menu()

    def set_version(self, version: str) -> None:
        """设置版本号，用于菜单底部显示。"""
        self._version = version

    # ── 回调 ────────────────────────────────────────────────────────────────

    def _cb_status(self, item) -> str:
        """状态栏显示回调。"""
        status_text = i18nUtil.t(self._status_key, **self._status_kwargs) if self._status_key else ""
        return i18nUtil.t("status_label", s=status_text)

    def _cb_open_web(self, icon, item) -> None:
        """打开 Web 界面。"""
        webbrowser.open(self._web_url, new=0)

    def _cb_open_config_dir(self, icon, item) -> None:
        """打开配置目录。"""
        config_dir = appPaths.CONFIG_DIR
        os.makedirs(config_dir, exist_ok=True)
        if sys.platform == "darwin":
            subprocess.Popen(["open", config_dir])
        elif sys.platform == "win32":
            subprocess.Popen(["explorer", config_dir])
        else:
            subprocess.Popen(["xdg-open", config_dir])

    def _cb_quit(self, icon, item) -> None:
        """退出程序。"""
        self._on_quit(icon)

    def _cb_set_language(self, lang: str) -> None:
        """切换语言并重建菜单。"""
        configUtil.set_language(lang)

    def _cb_reset_data(self, icon, item) -> None:
        """重置所有数据。"""
        import subprocess

        # 使用 macOS 原生对话框（避免 tkinter 与 AppKit 死锁）
        if sys.platform == "darwin":
            result = subprocess.run(
                [
                    "osascript", "-e",
                    f'display dialog "{i18nUtil.t("confirm_reset")}" buttons {{\"取消\", \"确认\"}} default button \"取消\" with icon caution'
                ],
                capture_output=True, text=True
            )
            confirmed = "确认" in result.stdout
        else:
            # 其他平台使用 tkinter（在独立窗口中）
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            try:
                confirmed = messagebox.askyesno(
                    "TogoSpace",
                    i18nUtil.t("confirm_reset"),
                    icon="warning",
                )
            finally:
                root.destroy()

        if not confirmed:
            return

        # 先请求后端关闭
        if self._on_reset:
            self._on_reset()

        # 等待一小段时间让后端有机会开始 shutdown
        import time
        time.sleep(0.5)

        # 删除数据目录
        data_dir = appPaths.DATA_DIR
        if os.path.isdir(data_dir):
            shutil.rmtree(data_dir)

        # 显示完成提示（macOS 原生对话框）
        if sys.platform == "darwin":
            subprocess.run(
                [
                    "osascript", "-e",
                    f'display notification "{i18nUtil.t("reset_done_body")}" with title "{i18nUtil.t("reset_done_title")}"'
                ],
                capture_output=True
            )
        else:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            try:
                messagebox.showinfo(
                    i18nUtil.t("reset_done_title"),
                    i18nUtil.t("reset_done_body"),
                )
            finally:
                root.destroy()

        # 使用 os._exit 强制退出
        os._exit(0)

    # ── 构建 ────────────────────────────────────────────────────────────────

    def build(self) -> pystray.Menu:
        """构建托盘菜单。"""
        current_lang = configUtil.get_language() if configUtil.is_loaded() else "zh-CN"

        return pystray.Menu(
            pystray.MenuItem(self._cb_status, None, enabled=False),
            pystray.MenuItem(i18nUtil.t("open_web"), self._cb_open_web),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(i18nUtil.t("open_config_dir"), self._cb_open_config_dir),
            pystray.MenuItem(i18nUtil.t("reset_data"), self._cb_reset_data),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                i18nUtil.t("language_menu"),
                pystray.Menu(
                    pystray.MenuItem(
                        i18nUtil.t("lang_zh"),
                        lambda icon, item: self._cb_set_language("zh-CN"),
                        checked=lambda item: current_lang == "zh-CN",
                        radio=True,
                    ),
                    pystray.MenuItem(
                        i18nUtil.t("lang_en"),
                        lambda icon, item: self._cb_set_language("en"),
                        checked=lambda item: current_lang == "en",
                        radio=True,
                    ),
                ),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(i18nUtil.t("version", v=self._version), None, enabled=False),
            pystray.MenuItem(i18nUtil.t("quit"), self._cb_quit),
        )

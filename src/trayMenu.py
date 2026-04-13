"""托盘菜单封装。

将菜单构建、回调处理、状态显示封装在一个类中。
"""

import os
import shutil
import subprocess
import sys
import webbrowser

import pystray

import appPaths
import backend_main
from util import configUtil, i18nUtil


class TrayMenu:
    """托盘菜单管理，负责菜单构建、回调处理和状态显示。"""

    def __init__(self, tray_icon: pystray.Icon | None, web_url: str, on_quit: callable):
        """
        Args:
            tray_icon: pystray Icon 实例，用于更新菜单
            web_url: Web 界面地址
            on_quit: 退出回调，用于停止后端和托盘
        """
        self._icon = tray_icon
        self._web_url = web_url
        self._on_quit = on_quit
        self._status: str = ""
        self._version: str = ""

    # ── 状态管理 ─────────────────────────────────────────────────────────────

    def set_status(self, status: str) -> None:
        """更新状态并刷新菜单显示。"""
        self._status = status
        if self._icon is not None:
            self._icon.update_menu()

    def set_version(self, version: str) -> None:
        """设置版本号，用于菜单底部显示。"""
        self._version = version

    # ── 回调 ────────────────────────────────────────────────────────────────

    def _cb_status(self, item) -> str:
        """状态栏显示回调。"""
        return i18nUtil.tray_t("status_label", s=self._status)

    def _cb_open_web(self, icon, item) -> None:
        """打开 Web 界面。"""
        webbrowser.open(self._web_url, new=0)

    def _cb_open_config_dir(self, icon, item) -> None:
        """打开配置目录 (~/.togo_agent)。"""
        config_dir = os.path.expanduser("~/.togo_agent")
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
        if self._icon is not None:
            self._icon.menu = self.build()
            self._icon.update_menu()

    def _cb_reset_data(self, icon, item) -> None:
        """重置所有数据。"""
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            confirmed = messagebox.askyesno(
                "TogoAgent",
                i18nUtil.tray_t("confirm_reset"),
                icon="warning",
                parent=root,
            )
        finally:
            root.destroy()

        if not confirmed:
            return

        backend_main.request_shutdown()

        data_dir = appPaths.DATA_DIR
        if os.path.isdir(data_dir):
            shutil.rmtree(data_dir)

        # 显示完成提示
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            messagebox.showinfo(
                i18nUtil.tray_t("reset_done_title"),
                i18nUtil.tray_t("reset_done_body"),
                parent=root,
            )
        finally:
            root.destroy()

        icon.stop()

    # ── 构建 ────────────────────────────────────────────────────────────────

    def build(self) -> pystray.Menu:
        """构建托盘菜单。"""
        current_lang = configUtil.get_language() if configUtil.is_loaded() else "zh-CN"

        return pystray.Menu(
            pystray.MenuItem(self._cb_status, None, enabled=False),
            pystray.MenuItem(i18nUtil.tray_t("open_web"), self._cb_open_web),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(i18nUtil.tray_t("open_config_dir"), self._cb_open_config_dir),
            pystray.MenuItem(i18nUtil.tray_t("reset_data"), self._cb_reset_data),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                i18nUtil.tray_t("language_menu"),
                pystray.Menu(
                    pystray.MenuItem(
                        i18nUtil.tray_t("lang_zh"),
                        lambda icon, item: self._cb_set_language("zh-CN"),
                        checked=lambda item: current_lang == "zh-CN",
                        radio=True,
                    ),
                    pystray.MenuItem(
                        i18nUtil.tray_t("lang_en"),
                        lambda icon, item: self._cb_set_language("en"),
                        checked=lambda item: current_lang == "en",
                        radio=True,
                    ),
                ),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(i18nUtil.tray_t("version", v=self._version), None, enabled=False),
            pystray.MenuItem(i18nUtil.tray_t("quit"), self._cb_quit),
        )
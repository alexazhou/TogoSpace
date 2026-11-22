"""桌面构建脚本公共工具（供 `build_desktop.py` 与 `build_backend.py` 复用）。"""

from __future__ import annotations

import platform
import sys
import sysconfig


def is_windows() -> bool:
    return sys.platform == "win32"


def is_macos() -> bool:
    return sys.platform == "darwin"


def platform_key() -> str:
    """sys.platform 归一：Windows=win32 / macOS=darwin / 其他=linux。"""
    if is_windows():
        return "win32"
    if is_macos():
        return "darwin"
    return "linux"


def configure_stdio() -> None:
    """Windows 控制台重定向输出时默认用本地代码页（如 cp936），emoji 会抛
    UnicodeEncodeError；保留编码，只替换不可编码字符。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def arch_from_sysconfig_platform(plat: str) -> str:
    """把 `sysconfig.get_platform()` 的结果归一为 `arm64` / `x86_64`。

    `sysconfig.get_platform()` 在编译 Python 解释器时确定（如 `win-amd64` / `win-arm64`），
    不受 Windows on ARM 环境变量 `PROCESSOR_ARCHITECTURE` 干扰。
    """
    normalized = plat.strip().lower()
    if normalized.endswith(("arm64", "aarch64")):
        return "arm64"
    return "x86_64"


def host_arch() -> str:
    """默认架构跟随当前解释器：PyInstaller 只能产出与解释器同架构的 sidecar。"""
    if is_windows():
        return arch_from_sysconfig_platform(sysconfig.get_platform())
    return "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x86_64"

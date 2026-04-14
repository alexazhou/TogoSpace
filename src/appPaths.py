"""
运行时路径模块。

引入 STORAGE_ROOT 统一管理所有可写目录：
- 打包模式：~/.togo_agent
- 开发模式：仓库根目录下的 .togo_agent

静态资源（只读）在打包时指向 _MEIPASS，开发时指向仓库 assets/。
"""
import os
import platform
import sys

_SRC = os.path.dirname(os.path.abspath(__file__))   # = repo/src/
_ROOT = os.path.join(_SRC, "..")                     # = repo/
_IS_FROZEN = bool(getattr(sys, "frozen", False))
_MEIPASS = str(getattr(sys, "_MEIPASS", ""))
STORAGE_ROOT: str = os.path.expanduser("~/.togo_agent") if _IS_FROZEN else os.path.abspath(os.path.join(_ROOT, "dev_storage_root"))

# 静态资源（只读）- 打包时指向 _MEIPASS，开发时指向仓库 assets/
ASSETS_DIR: str = os.path.join(_MEIPASS, "assets") if _IS_FROZEN else os.path.abspath(os.path.join(_ROOT, "assets"))

# 所有可写路径统一基于 STORAGE_ROOT
DATA_DIR: str       = os.path.join(STORAGE_ROOT, "data")
LOGS_DIR: str       = os.path.join(STORAGE_ROOT, "logs", "backend")
WORKSPACE_ROOT: str = os.path.join(STORAGE_ROOT, "workspace")
CONFIG_DIR: str     = STORAGE_ROOT  # 配置文件也在 storage_root

# Preset 目录（role_templates / teams），可通过环境变量覆盖
PRESET_DIR: str = os.path.abspath(os.environ.get("TEAMAGENT_PRESET_DIR") or os.path.join(ASSETS_DIR, "preset"))


def get_gtsp_binary_path() -> str:
    """
    根据当前平台返回 gtsp 可执行文件路径。

    支持的平台：
    - macOS (darwin): amd64 / arm64
    """
    system = platform.system().lower()
    machine = platform.machine().lower()

    # 映射架构名称
    arch_map = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "arm64": "arm64",
        "aarch64": "arm64",
    }
    arch = arch_map.get(machine, machine)

    # 构建二进制文件名
    binary_name = f"gtsp-{system}-{arch}"
    binary_path = os.path.join(ASSETS_DIR, "execute", "gtsp", binary_name)

    if not os.path.exists(binary_path):
        raise FileNotFoundError(
            f"gtsp binary not found for current platform: {binary_path}"
        )

    return binary_path

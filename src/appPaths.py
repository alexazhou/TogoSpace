"""
运行时路径模块。

引入 STORAGE_ROOT 统一管理所有可写目录：
- 打包模式：~/.togospace
- 开发模式：仓库根目录下的 dev_storage_root/

静态资源（只读）在打包时指向 _MEIPASS，开发时指向仓库 assets/。
"""
import os
import platform
import sys

_SRC = os.path.dirname(os.path.abspath(__file__))   # = repo/src/
_ROOT = os.path.join(_SRC, "..")                     # = repo/
_IS_FROZEN = bool(getattr(sys, "frozen", False))
_MEIPASS = str(getattr(sys, "_MEIPASS", ""))
IS_DEV_MODE = not _IS_FROZEN
# 由 Tauri 外壳拉起的后端进程：强制只监听 127.0.0.1 并开启 token 鉴权。
IS_DESKTOP_MODE = os.environ.get("TOGOSPACE_DESKTOP") == "1"


def _resolve_assets_dir() -> str:
    """定位静态资源目录。

    PyInstaller 6 onedir 下 ``_MEIPASS`` 指向 ``_internal``，datas 声明的 ``assets/``
    会落在 ``_internal/assets``；部分打包形态会把资源放在可执行文件同级。这里按优先级
    依次探测，避免打包结构差异导致后端启动即找不到 preset / migrate。
    """
    if not _IS_FROZEN:
        return os.path.abspath(os.path.join(_ROOT, "assets"))

    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    candidates = [
        os.path.join(_MEIPASS, "assets"),
        os.path.join(exe_dir, "assets"),
        os.path.join(exe_dir, "_internal", "assets"),
    ]
    for candidate in candidates:
        if os.path.isdir(candidate):
            return candidate
    return candidates[0]

STORAGE_ROOT: str
ASSETS_DIR: str
DATA_DIR: str
LOGS_DIR: str
WORKSPACE_ROOT: str
CONFIG_DIR: str
PRESET_DIR: str

if _IS_FROZEN:
    STORAGE_ROOT = os.path.expanduser("~/.togospace")
else:
    STORAGE_ROOT = os.path.abspath(os.path.join(_ROOT, "dev_storage_root"))
ASSETS_DIR = _resolve_assets_dir()

# 环境变量优先级最高（用于 Docker 等场景）
_ENV_STORAGE_ROOT = os.environ.get("STORAGE_ROOT")
if _ENV_STORAGE_ROOT:
    STORAGE_ROOT = _ENV_STORAGE_ROOT
DATA_DIR = os.path.join(STORAGE_ROOT, "data")
LOGS_DIR = os.path.join(STORAGE_ROOT, "logs", "backend")
WORKSPACE_ROOT = os.path.join(STORAGE_ROOT, "workspace")
BUILTIN_SKILLS_DIR = os.path.join(ASSETS_DIR, "skills")
USER_SKILLS_DIR = os.path.join(STORAGE_ROOT, "skills")
CONFIG_DIR = STORAGE_ROOT
PRESET_DIR = os.path.abspath(os.environ.get("TEAMAGENT_PRESET_DIR") or os.path.join(ASSETS_DIR, "preset"))


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

    # 构建二进制文件名，Windows 使用 .exe 后缀，其他平台不加后缀
    suffix = ".exe" if system == "windows" else ""
    binary_name = f"gtsp-{system}-{arch}{suffix}"
    binary_path = os.path.join(ASSETS_DIR, "execute", "gtsp", binary_name)

    if not os.path.exists(binary_path):
        raise FileNotFoundError(
            f"gtsp binary not found for current platform: {binary_path}"
        )

    return binary_path

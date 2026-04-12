"""
运行时路径模块。

所有变量在开发模式下指向仓库内默认位置，打包模式下由
app_entry.py 在后端启动前覆盖为用户目录（~/.team_agent/）。
"""
import os
import platform

_SRC = os.path.dirname(os.path.abspath(__file__))   # = repo/src/
_ROOT = os.path.join(_SRC, "..")                     # = repo/

# 静态资源（prompts / preset / migrate 等，只读）
ASSETS_DIR: str    = os.path.abspath(os.path.join(_ROOT, "assets"))

# Preset 目录（role_templates / teams），可通过环境变量覆盖
PRESET_DIR: str    = os.path.abspath(os.environ.get("TEAMAGENT_PRESET_DIR") or os.path.join(ASSETS_DIR, "preset"))

# 运行数据（SQLite 等，可写）
DATA_DIR: str      = os.path.abspath(os.path.join(_ROOT, "data"))

# 后端日志（可写）
LOGS_DIR: str      = os.path.abspath(os.path.join(_ROOT, "logs", "backend"))

# Agent 工作目录根（可写）
WORKSPACE_ROOT: str = os.path.abspath(os.path.join(_ROOT, "workspace"))


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

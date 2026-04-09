"""
运行时路径模块。

所有变量在开发模式下指向仓库内默认位置，打包模式下由
app_entry.py 在后端启动前覆盖为用户目录（~/.agent_team/）。
"""
import os

_SRC = os.path.dirname(os.path.abspath(__file__))   # = repo/src/
_ROOT = os.path.join(_SRC, "..")                     # = repo/

# 静态资源（prompts / preset / migrate 等，只读）
ASSETS_DIR: str    = os.path.abspath(os.path.join(_ROOT, "assets"))

# 运行数据（SQLite 等，可写）
DATA_DIR: str      = os.path.abspath(os.path.join(_ROOT, "data"))

# 后端日志（可写）
LOGS_DIR: str      = os.path.abspath(os.path.join(_ROOT, "logs", "backend"))

# Agent 工作目录根（可写）
WORKSPACE_ROOT: str = os.path.abspath(_ROOT)

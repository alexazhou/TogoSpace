"""
运行时资源路径模块。

ASSETS_DIR 指向 assets/ 目录的绝对路径：
  - 开发环境（backend_main.py 直接运行）：默认指向仓库根目录下的 assets/
  - 打包运行（app_entry.py）：由 app_entry.py 在后端启动前覆盖为 sys._MEIPASS
"""
import os

# 默认：相对于本文件（src/appPaths.py）的 ../assets/
ASSETS_DIR: str = os.path.abspath(os.path.join(os.path.dirname(__file__), "../assets"))

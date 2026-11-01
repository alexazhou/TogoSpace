# -*- mode: python ; coding: utf-8 -*-
"""Tauri 桌面形态的 Python 后端打包（PyInstaller onedir）。

- 入口是 src/desktopEntry.py（纯后端，无托盘、无 GUI）
- onedir，但不产 BUNDLE：.app 由 Tauri 产出，后端整棵目录树作为 Resources/backend
- console=True：stdout 是 Rust 启动状态机的通道
"""

import fnmatch
import os
import platform
import re

# SPECPATH 是 PyInstaller 内置变量，指向本 spec 文件所在目录
REPO_ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

# 读取版本号
_ver_src = open(os.path.join(REPO_ROOT, "src", "version.py")).read()
APP_VERSION = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', _ver_src).group(1)

# 从环境变量读取目标架构，默认为当前机器架构
_default_arch = "arm64" if platform.machine() == "arm64" else "x86_64"
TARGET_ARCH = os.environ.get("TARGET_ARCH", _default_arch)
print(f"ℹ️  target_arch: {TARGET_ARCH}")

# 获取 litellm 路径
import litellm
LITELLM_PATH = os.path.dirname(litellm.__file__)
print(f"ℹ️  litellm_path: {LITELLM_PATH}")

# ── Analysis ──────────────────────────────────────────────────────────────────

a = Analysis(
    [os.path.join(REPO_ROOT, "src", "desktopEntry.py")],
    pathex=[os.path.join(REPO_ROOT, "src")],
    binaries=[],
    datas=[
        (os.path.join(REPO_ROOT, "assets"), "assets"),
        (LITELLM_PATH, "litellm"),
    ],
    hiddenimports=[
        # tornado
        "tornado",
        "tornado.platform.asyncio",
        "tornado.routing",
        "tornado.httputil",
        # pydantic
        "pydantic",
        "pydantic_core",
        # database
        "aiosqlite",
        "aiosqlite.core",
        "peewee",
        "peewee_async",
        # project-specific
        "pytspclient",
        # tiktoken plugin (namespace package, needs explicit import)
        "tiktoken_ext",
        "tiktoken_ext.openai_public",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[
        os.path.join(SPECPATH, "rthook_tiktoken.py"),
    ],
    excludes=["textual", "mypy", "pystray", "AppKit", "Foundation", "objc", "PyObjCTools", "PIL", "tkinter"],
    noarchive=False,
)

# ── 事后过滤：排除不必要的文件 ────────────────────────────────────────────────

EXCLUDE_PATTERNS = [
    # litellm proxy 管理面板 UI (19M)
    "litellm/proxy/_experimental/out",
    # litellm 内容护栏 (3.6M)
    "litellm/proxy/guardrails",
    # litellm Swagger API 文档 (1.6M)
    "litellm/proxy/swagger",
    # litellm 第三方服务集成 (3.6M)
    "litellm/integrations",
    # litellm HuggingFace 适配器 (1.4M)
    "litellm/llms/huggingface",
    # tcl/tk GUI 库（桌面形态无 GUI）
    "_tcl_data",
    "_tk_data",
    "tcl8",
    # gtsp 的**运行期日志目录**（`assets/execute/gtsp/logs/`）。
    #
    # 排的是这个子目录，不是 gtsp 本体（下面那段说不能排除 `gtsp-linux-*`，两件事）。
    #
    # 为什么必须排：本 spec 的 datas 声明的是整棵 `assets/`，PyInstaller **不做
    # .gitignore 感知**。该目录被 `.gitignore` 的 `logs` 规则忽略、不入库，但开发机
    # 上物理存在（本仓库当前 26 个文件 / 212K）——生产构建 `tauri/scripts/build_backend.py`
    # 从工作区主目录打包，日志会被原样打进安装包。
    #
    # 后果不只是体积：日志里有本机绝对路径（`/Volumes/PDATA/...`）与用户业务文件名
    # （实测含 `wechat.JPG` 这类终端用户的真实图片名），等于把开发机信息带给终端用户。
    #
    # ⚠️ 措辞更正（勿再写回「base64 图片数据」）：日志里形如
    # `"base64":"iVBORw0KGgoAAAANSUhEUgAA...[base64:100620 chars]"` 的片段，
    # `[base64:N chars]` 是 **gtsp 自己打的截断占位符**，不是数据本体。实测
    # 26 个日志：`[A-Za-z0-9+/]{200,}` 0 命中、`data:image/...;base64` 0 命中、
    # `iVBOR` 后连续 base64 字符恒 24 个。真正敏感的是路径（暴露机器）与文件名
    # （暴露用户业务内容）。
    #
    # 功能上无损：gtsp 运行期日志落 `appPaths.LOGS_DIR`（`~/.togospace/logs/backend`），
    # 与包内这个目录无关；这里只是历史调试日志的残留。
    "execute/gtsp/logs",
    # ⚠️ 这里**不能**排除 `gtsp-linux-*`。
    #
    # 容器沙箱（§3.2）用 `docker cp` 把 Linux 产物注入容器，靠的是
    # `appPaths.get_gtsp_linux_binary_path()` 从 ASSETS_DIR 读取。ASSETS_DIR 在
    # 打包态指向 `_MEIPASS/assets`，而本 spec 的 datas 只声明了整棵 `assets/`，
    # 所以排除规则一旦命中，安装包里就没有 Linux 产物 →
    # `get_gtsp_linux_binary_path` 抛 FileNotFoundError → 容器功能在打包形态下
    # 整体不可用（开发态因为读仓库 assets/ 反而正常，最难发现的一类差异）。
    #
    # 曾排除过（`56ddaede` 加桌面外壳时，那时还没有容器功能），总计约 12.7M；
    # 现在它是功能必需物，不排除。
    #
    # ── 下面两条同源：都是「.gitignore 忽略、但开发机上物理存在」的东西 ──
    # 字节码缓存目录。PyInstaller 同样不读 `.gitignore`，且 `.pyc` 的 `co_filename`
    # 记录**构建机绝对路径**（实测 1470 个 .pyc 100% 含 `/Volumes/PDATA/GitDB`）。
    # 排掉顺带省 20M；`fnmatch(basename)` 分支天然覆盖任意层级，不用逐层列举。
    # 只按 basename 匹配 `__pycache__`，不会误伤 `.py` 源码。
    "__pycache__",
    # macOS Finder 元数据。同样不入库但物理存在（assets/ 下当前 5 个：
    # assets/、assets/icons/、assets/execute/、assets/execute/gtsp/、
    # assets/execute/gtsp/logs/），会被整棵 `assets/` 的 datas 原样带进包。
    #
    # ⚠️ 这条是**冗余保险，故意不删**（小马哥 2026-11-01 裁定）。
    # PyInstaller 6.20.0 的 `Analysis.assemble()` 里确实有个 `if is_darwin:` 分支会
    # 按 `basename(src_name) != '.DS_Store'` 自过滤，实测本机构建产物 .DS_Store 为 0。
    # 但那是**构建平台判断**，不是目标平台：一旦 PyInstaller 换版本改掉这段，或改在
    # Linux 上构建（CI 换 runner），就会静默回流。留着成本是一行 pattern，收益是
    # 「不依赖上游默认行为 + 单测钉住 spec 自身契约」。
    # 判据顺序：显式防护 > 依赖上游默认行为。别因为「上游已经兜了」就删掉这条。
    ".DS_Store",
]

# tcl/tk 动态库文件名
TCLTK_BINARIES = ["libtcl8.6.dylib", "libtk8.6.dylib"]

# 过滤 datas
filtered_datas = []
for item in a.datas:
    dest_path = item[0]
    src_path = item[1]
    excluded = False
    for pattern in EXCLUDE_PATTERNS:
        if pattern in dest_path or fnmatch.fnmatch(os.path.basename(src_path), pattern):
            print(f"🗑️ Excluding: {dest_path}")
            excluded = True
            break
    if not excluded:
        filtered_datas.append(item)
a.datas = filtered_datas

# 过滤 binaries（排除 tcl/tk 动态库）
filtered_binaries = []
for item in a.binaries:
    dest_path = item[0]
    src_path = item[1]
    binary_name = os.path.basename(src_path)
    if binary_name in TCLTK_BINARIES:
        print(f"🗑️ Excluding binary: {binary_name}")
        continue
    filtered_binaries.append(item)
a.binaries = filtered_binaries

# ── 后续构建步骤 ──────────────────────────────────────────────────────────────

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TogoSpace-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=TARGET_ARCH,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="TogoSpace-backend",
)

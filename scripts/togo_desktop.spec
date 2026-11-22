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
import sys

# SPECPATH 是 PyInstaller 内置变量，指向本 spec 文件所在目录
REPO_ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

# Windows 控制台重定向输出时默认用本地代码页（如 cp936），emoji 会抛
# UnicodeEncodeError；保留编码，只替换不可编码字符。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

# 读取版本号
_ver_src = open(os.path.join(REPO_ROOT, "src", "version.py"), encoding="utf-8").read()
APP_VERSION = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', _ver_src).group(1)

# 从环境变量读取目标架构，默认为当前机器架构
_default_arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x86_64"
TARGET_ARCH = os.environ.get("TARGET_ARCH", _default_arch)
print(f"ℹ️  target_arch: {TARGET_ARCH}")

# PyInstaller 的 target_arch 只在 macOS 上有效（用于产 universal2 / 交叉架构）；
# Windows / Linux 上它会因为本机 bootloader 与解释器架构固定而被忽略或直接报错，
# 这两个平台的产物架构由解释器自身决定。
EXE_ARCH_OPTIONS = {"target_arch": TARGET_ARCH} if sys.platform == "darwin" else {}

# 获取 litellm 路径
import litellm
LITELLM_PATH = os.path.dirname(litellm.__file__)
print(f"ℹ️  litellm_path: {LITELLM_PATH}")

# mcp2cli 的包元数据必须一起打进去（#202 接进来的外部调用能力 / #222 B1 补回）。
#
# 为什么要 copy_metadata 而不能只靠 hiddenimports：`mcp2cli` 在 import 时用
# `importlib.metadata.version("mcp2cli")` 取自身版本号，PyInstaller 默认**不收集**
# 第三方包的 .dist-info ⇒ 打包后 import 直接 `PackageNotFoundError`（源码跑没事，
# 只有打包形态炸，本地测试发现不了）。copy_metadata 把 dist-info 作为 datas 带进包，
# 运行期 version() 才找得到。
from PyInstaller.utils.hooks import copy_metadata  # noqa: E402 —— 依赖 PyInstaller 环境

# ── Analysis ──────────────────────────────────────────────────────────────────

a = Analysis(
    [os.path.join(REPO_ROOT, "src", "desktopEntry.py")],
    pathex=[os.path.join(REPO_ROOT, "src")],
    binaries=[],
    datas=[
        (os.path.join(REPO_ROOT, "assets"), "assets"),
        (os.path.join(REPO_ROOT, "src", "service", "agentService", "templates"), "service/agentService/templates"),
        (LITELLM_PATH, "litellm"),
        # mcp2cli 运行期要 `importlib.metadata.version("mcp2cli")`（见上方 import 处说明）
        *copy_metadata("mcp2cli"),
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
    # gtsp 运行期日志：.gitignore 忽略、不入库，但开发机上可能存在。
    # PyInstaller 不读 .gitignore，整棵 assets/ 会原样打包；日志含构建机绝对路径
    # 与用户文件名，必须排除。只排 logs 子目录，不能误伤同目录的 gtsp-linux-*：
    # 容器沙箱要从 ASSETS_DIR 读取它们，排掉会让打包态容器功能整体不可用。
    "execute/gtsp/logs",
    # 字节码缓存：.pyc 的 co_filename 记录构建机绝对路径，顺带省体积
    "__pycache__",
    # macOS Finder 元数据。PyInstaller 当前在 Darwin 上会自动过滤，留作冗余保险
    ".DS_Store",
]

# tcl/tk 动态库文件名
TCLTK_BINARIES = ["libtcl8.6.dylib", "libtk8.6.dylib", "tcl86t.dll", "tk86t.dll"]

# 过滤 datas
filtered_datas = []
for item in a.datas:
    dest_path = item[0]
    src_path = item[1]
    # PyInstaller 在 Windows 上给出的 dest_path 用反斜杠，而排除表统一写 /
    # （POSIX 语义）。不归一化的话，所有含 / 的规则在 Windows 上永远不命中：
    # litellm 的 guardrails 测试数据会被整棵打进包，产生 180+ 字符的路径，
    # 用户用资源管理器解压 zip / NSIS 安装都会报「路径太长」。
    dest_posix = dest_path.replace("\\", "/")
    excluded = False
    for pattern in EXCLUDE_PATTERNS:
        if pattern in dest_posix or fnmatch.fnmatch(os.path.basename(src_path), pattern):
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

# macOS 专属的 EXE 参数（#243 起 Windows 也要打这个 spec）。
# `target_arch` 只对 macOS 有意义（决定产物是 arm64 / x86_64 / universal），
# `argv_emulation` 是 macOS 的 AppleEvent 参数。PyInstaller 在 Windows 上会忽略它们，
# 但「靠它忽略」不是契约：显式只在 darwin 上传，darwin 侧 kwargs 与改动前逐字相同
# （= mac 构建产物不受本单影响），Windows 侧则完全绕开这两项。
_EXE_MACOS_ONLY = {}
if sys.platform == "darwin":
    _EXE_MACOS_ONLY = {"argv_emulation": False, "target_arch": TARGET_ARCH}

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Togobot-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    **_EXE_MACOS_ONLY,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Togobot-backend",
)

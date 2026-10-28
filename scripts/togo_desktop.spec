# -*- mode: python ; coding: utf-8 -*-
"""Tauri 桌面形态的 Python 后端打包（PyInstaller onedir）。

与 legacy 托盘形态（togo_agent.spec）的差异：
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
    # Linux 版本的 gtsp 可执行文件
    "gtsp-linux-*",
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

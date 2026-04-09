# -*- mode: python ; coding: utf-8 -*-
import os
import re
import sys
import platform

# SPECPATH 是 PyInstaller 内置变量，指向本 spec 文件所在目录
REPO_ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

# 读取版本号
_ver_src = open(os.path.join(REPO_ROOT, "src", "version.py")).read()
APP_VERSION = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', _ver_src).group(1)

# 目标架构（可通过环境变量覆盖，默认跟随当前机器）
target_arch = os.environ.get("TARGET_ARCH", "").strip()
if not target_arch:
    target_arch = "arm64" if "arm" in platform.machine().lower() else "x86_64"
print(f"ℹ️  target_arch: {target_arch}")

_icon_path = os.path.join(REPO_ROOT, "assets", "icon.icns")
APP_ICON = _icon_path if os.path.exists(_icon_path) else None
if not APP_ICON:
    print("⚠️  assets/icon.icns 不存在，将使用默认图标")

# ── Analysis ──────────────────────────────────────────────────────────────────

a = Analysis(
    [os.path.join(REPO_ROOT, "src", "app_entry.py")],
    pathex=[
        os.path.join(REPO_ROOT, "src"),
        "/Volumes/PData/GitDB/GTAgentHands/pyTSPClient",  # editable install
    ],
    binaries=[],
    datas=[
        (os.path.join(REPO_ROOT, "assets"), "assets"),
        # litellm 含大量 json/yaml 数据文件，需整包打入
        (os.path.join(REPO_ROOT, ".venv", "lib", "python3.11", "site-packages", "litellm"), "litellm"),
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
        # macOS tray
        "AppKit",
        "Foundation",
        "objc",
        "PyObjCTools",
        "PyObjCTools.MachSignals",
        "pystray",
        "pystray._darwin",
        # image
        "PIL",
        "PIL.Image",
        "PIL.ImageDraw",
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
    excludes=["tkinter", "textual"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AgentTeam",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=target_arch,
    icon=APP_ICON,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="AgentTeam",
)

# ── macOS App Bundle ──────────────────────────────────────────────────────────

app = BUNDLE(
    coll,
    name="AgentTeam.app",
    icon=APP_ICON,
    bundle_identifier="com.agentteam.app",
    info_plist={
        "CFBundleName":               "AgentTeam",
        "CFBundleDisplayName":        "AgentTeam",
        "CFBundleIdentifier":         "com.agentteam.app",
        "CFBundleVersion":            APP_VERSION,
        "CFBundleShortVersionString": APP_VERSION,
        "LSUIElement":                True,       # 无 Dock 图标，菜单栏常驻
        "NSHighResolutionCapable":    True,
    },
)

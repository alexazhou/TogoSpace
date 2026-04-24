# -*- mode: python ; coding: utf-8 -*-
import os
import re
import fnmatch

# SPECPATH 是 PyInstaller 内置变量，指向本 spec 文件所在目录
REPO_ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

# 读取版本号
_ver_src = open(os.path.join(REPO_ROOT, "src", "version.py")).read()
APP_VERSION = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', _ver_src).group(1)

# 从环境变量读取目标架构，默认为当前机器架构
import platform
_default_arch = "arm64" if platform.machine() == "arm64" else "x86_64"
TARGET_ARCH = os.environ.get("TARGET_ARCH", _default_arch)
print(f"ℹ️  target_arch: {TARGET_ARCH}")

_icon_path = os.path.join(REPO_ROOT, "assets", "icon.icns")
APP_ICON = _icon_path if os.path.exists(_icon_path) else None
if not APP_ICON:
    print("⚠️  assets/icon.icns 不存在，将使用默认图标")

# 获取 litellm 路径
import litellm
LITELLM_PATH = os.path.dirname(litellm.__file__)
print(f"ℹ️  litellm_path: {LITELLM_PATH}")

# ── Analysis ──────────────────────────────────────────────────────────────────

a = Analysis(
    [os.path.join(REPO_ROOT, "src", "appEntry.py")],
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
    excludes=["textual", "mypy"],
    noarchive=False,
)

# ── 事后过滤：排除不必要的文件 ────────────────────────────────────────────────

filtered_datas = []
EXCLUDE_PATTERNS = [
    # litellm proxy 管理面板 UI (19M)
    "litellm/proxy/_experimental/out",
    # litellm 内容护栏 (3.6M)
    "litellm/proxy/guardrails",
    # litellm Swagger API 文档 (1.6M)
    "litellm/proxy/swagger",
    # litellm HuggingFace 适配器 (1.4M)
    "litellm/llms/huggingface",
    # Linux 版本的 gtsp 可执行文件
    "gtsp-linux-*",
]

for item in a.datas:
    dest_path = item[0]
    src_path = item[1]
    excluded = False
    for pattern in EXCLUDE_PATTERNS:
        # 检查目标路径是否包含排除模式
        if pattern in dest_path or fnmatch.fnmatch(os.path.basename(src_path), pattern):
            print(f"🗑️ Excluding: {dest_path}")
            excluded = True
            break
    if not excluded:
        filtered_datas.append(item)

a.datas = filtered_datas

# ── 后续构建步骤 ──────────────────────────────────────────────────────────────

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TogoAgent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=TARGET_ARCH,
    icon=APP_ICON,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="TogoAgent",
)

# ── macOS App Bundle ──────────────────────────────────────────────────────────

app = BUNDLE(
    coll,
    name="TogoAgent.app",
    icon=APP_ICON,
    bundle_identifier="com.togoagent.app",
    info_plist={
        "CFBundleName":               "TogoAgent",
        "CFBundleDisplayName":        "TogoAgent",
        "CFBundleIdentifier":         "com.togoagent.app",
        "CFBundleVersion":            APP_VERSION,
        "CFBundleShortVersionString": APP_VERSION,
        "LSUIElement":                True,       # 无 Dock 图标，菜单栏常驻
        "NSHighResolutionCapable":    True,
    },
)
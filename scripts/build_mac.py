#!/usr/bin/env python3
"""
macOS 打包脚本：构建 AgentTeam.app

步骤：
  1. 版本一致性检查（前端 package.json vs 后端 version.py）
  2. 前端构建（npm run build）
  3. 同步前端产物到 assets/frontend/
  4. PyInstaller 打包
  5. 重命名产物为带版本号的 .app
"""

import json
import os
import re
import shutil
import subprocess
import sys

import PyInstaller.__main__

# ── 路径常量 ──────────────────────────────────────────────────────────────────

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT  = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
DIST_PATH  = os.path.join(REPO_ROOT, "dist")
BUILD_PATH = os.path.join(REPO_ROOT, "build")
SPEC_FILE  = os.path.join(SCRIPT_DIR, "agent_team.spec")


# ── 版本读取 ──────────────────────────────────────────────────────────────────

def _read_backend_version() -> str:
    path = os.path.join(REPO_ROOT, "src", "version.py")
    content = open(path).read()
    m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)
    if not m:
        print("❌ 无法从 src/version.py 读取版本号")
        sys.exit(1)
    return m.group(1)


def _read_frontend_version() -> str:
    path = os.path.join(REPO_ROOT, "frontend", "package.json")
    with open(path) as f:
        data = json.load(f)
    version = data.get("version", "")
    if not version:
        print("❌ 无法从 frontend/package.json 读取版本号")
        sys.exit(1)
    return version


# ── 前端构建 ──────────────────────────────────────────────────────────────────

def _build_frontend():
    frontend_dir = os.path.join(REPO_ROOT, "frontend")
    print("✳️  构建前端...")
    subprocess.run(["npm", "run", "build"], cwd=frontend_dir, check=True)
    print("✅ 前端构建完成")


def _sync_frontend():
    src = os.path.join(REPO_ROOT, "frontend", "dist")
    dst = os.path.join(REPO_ROOT, "assets", "frontend")
    print("✳️  同步前端产物 → assets/frontend/")
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    print("✅ 同步完成")


# ── 清理 & 打包 ───────────────────────────────────────────────────────────────

def _clean():
    for path in [DIST_PATH, BUILD_PATH]:
        if os.path.exists(path):
            print(f"🗑️  清理 {os.path.relpath(path, REPO_ROOT)}/")
            shutil.rmtree(path)


def _run_pyinstaller():
    pyinstaller_config = os.path.join(REPO_ROOT, ".pyinstaller")
    os.makedirs(pyinstaller_config, exist_ok=True)
    os.environ["PYINSTALLER_CONFIG_DIR"] = pyinstaller_config

    print("✳️  运行 PyInstaller...")
    PyInstaller.__main__.run([
        SPEC_FILE,
        "--distpath", DIST_PATH,
        "--workpath",  BUILD_PATH,
        "--clean",
        "-y",
    ])
    print("✅ PyInstaller 完成")


def _rename_app(version: str):
    original = os.path.join(DIST_PATH, "AgentTeam.app")
    final    = os.path.join(DIST_PATH, f"AgentTeam-{version}.app")
    if os.path.exists(original):
        os.rename(original, final)
        print(f"✅ 产物：dist/AgentTeam-{version}.app")
    else:
        print(f"❌ 未找到 {original}")
        sys.exit(1)


# ── 主流程 ────────────────────────────────────────────────────────────────────

def main():
    backend_ver  = _read_backend_version()
    frontend_ver = _read_frontend_version()

    print(f"ℹ️  后端版本：{backend_ver}")
    print(f"ℹ️  前端版本：{frontend_ver}")

    if backend_ver != frontend_ver:
        print(f"❌ 版本不一致：后端 {backend_ver} ≠ 前端 {frontend_ver}，请先对齐版本号")
        sys.exit(1)

    print(f"✅ 版本一致：v{backend_ver}")

    _build_frontend()
    _sync_frontend()
    _clean()
    _run_pyinstaller()
    _rename_app(backend_ver)


if __name__ == "__main__":
    main()

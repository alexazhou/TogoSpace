#!/usr/bin/env python3
"""构建桌面形态的 Python 后端并放入 Tauri 资源目录。

流程：
1. PyInstaller onedir 打包 `scripts/togo_desktop.spec` → `dist/TogoSpace-backend/`
2. 整棵目录树复制到 `tauri/build/backend/`，供 tauri.conf.json 的 bundle.resources 引用

用法：
    python3 tauri/scripts/build_backend.py
    python3 tauri/scripts/build_backend.py --arch x86_64
    python3 tauri/scripts/build_backend.py --python /path/to/python
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TAURI_DIR = os.path.dirname(SCRIPT_DIR)
REPO_ROOT = os.path.dirname(TAURI_DIR)

SPEC_FILE = os.path.join(REPO_ROOT, "scripts", "togo_desktop.spec")
DIST_BACKEND = os.path.join(REPO_ROOT, "dist", "TogoSpace-backend")
TARGET_DIR = os.path.join(TAURI_DIR, "build", "backend")


def _default_python() -> str:
    venv_python = os.path.join(REPO_ROOT, ".venv", "bin", "python3")
    if os.path.exists(venv_python):
        return venv_python
    return sys.executable


def _default_arch() -> str:
    return "arm64" if platform.machine() == "arm64" else "x86_64"


def _run(command: list[str], env: dict[str, str]) -> None:
    print(f"🏗️  {' '.join(command)}")
    subprocess.run(command, cwd=REPO_ROOT, env=env, check=True)


def _backend_executable() -> str:
    suffix = ".exe" if os.name == "nt" else ""
    return os.path.join(TARGET_DIR, f"TogoSpace-backend{suffix}")


def build_backend(python: str, arch: str) -> None:
    # build_desktop.py 已构建过时由 Tauri 的 beforeBuildCommand 跳过，避免重复打包
    if os.environ.get("TOGOSPACE_SKIP_BACKEND_BUILD") == "1" and os.path.isfile(_backend_executable()):
        print(f"⏭️  跳过后端构建（已存在）: {_backend_executable()}")
        return

    env = dict(os.environ)
    env["TARGET_ARCH"] = arch

    _run([python, "-m", "PyInstaller", "--noconfirm", "--clean", SPEC_FILE], env)

    if not os.path.isdir(DIST_BACKEND):
        raise SystemExit(f"❌ PyInstaller 产物不存在: {DIST_BACKEND}")

    if os.path.isdir(TARGET_DIR):
        shutil.rmtree(TARGET_DIR)
    os.makedirs(os.path.dirname(TARGET_DIR), exist_ok=True)
    shutil.copytree(DIST_BACKEND, TARGET_DIR, symlinks=True)

    print(f"✅ 后端已就绪: {TARGET_DIR}")


def main() -> int:
    parser = argparse.ArgumentParser(description="构建 Tauri 桌面形态的 Python 后端")
    parser.add_argument("--arch", choices=["arm64", "x86_64"], default=_default_arch())
    parser.add_argument("--python", default=_default_python(), help="用于运行 PyInstaller 的 Python")
    args = parser.parse_args()

    build_backend(args.python, args.arch)
    return 0


if __name__ == "__main__":
    sys.exit(main())

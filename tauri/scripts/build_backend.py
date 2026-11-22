#!/usr/bin/env python3
"""构建桌面形态的 Python 后端并放入 Tauri 资源目录。

流程：
1. PyInstaller onedir 打包 `scripts/togo_desktop.spec` → `build/Togobot-backend/`
2. 整棵目录树复制到 `tauri/build/backend/`，供 tauri.conf.json 的 bundle.resources 引用

用法：
    python3 tauri/scripts/build_backend.py
    python3 tauri/scripts/build_backend.py --arch x86_64
    python3 tauri/scripts/build_backend.py --python /path/to/python
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

from build_common import configure_stdio, host_arch, is_windows

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TAURI_DIR = os.path.dirname(SCRIPT_DIR)
REPO_ROOT = os.path.dirname(TAURI_DIR)

configure_stdio()

SPEC_FILE = os.path.join(REPO_ROOT, "scripts", "togo_desktop.spec")
# 本地构建产物统一放根目录 build/（PyInstaller 中间文件与 onedir 产物同处）
BUILD_DIR = os.path.join(REPO_ROOT, "build")
DIST_BACKEND = os.path.join(BUILD_DIR, "Togobot-backend")
TARGET_DIR = os.path.join(TAURI_DIR, "build", "backend")


def _default_python() -> str:
    candidates = [
        # Windows 虚拟环境布局
        os.path.join(REPO_ROOT, ".venv", "Scripts", "python.exe"),
        # POSIX 虚拟环境布局
        os.path.join(REPO_ROOT, ".venv", "bin", "python3"),
        os.path.join(REPO_ROOT, ".venv", "bin", "python"),
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return sys.executable


def _run(command: list[str], env: dict[str, str]) -> None:
    print(f"🏗️  {' '.join(command)}")
    subprocess.run(command, cwd=REPO_ROOT, env=env, check=True)


def _backend_executable() -> str:
    suffix = ".exe" if is_windows() else ""
    return os.path.join(TARGET_DIR, f"Togobot-backend{suffix}")


def _sync_target_dir(source: str, target: str) -> None:
    """把打包产物同步到资源目录，同时保留 `.gitkeep` 占位文件。

    `.gitkeep` 是空目录占位：没有它，未打包后端时 `cargo check` / `cargo build`
    会因 `bundle.resources` 指向不存在的目录直接失败。不能整棵 rmtree。
    """
    if os.path.isdir(target):
        for entry in os.listdir(target):
            if entry == ".gitkeep":
                continue
            path = os.path.join(target, entry)
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
    else:
        os.makedirs(target, exist_ok=True)
    # Windows 非管理员终端默认无创建符号链接权限，跳过 symlinks 保留避免 WinError 1314
    shutil.copytree(source, target, dirs_exist_ok=True, symlinks=not is_windows())


def _warn_on_long_paths(target: str, limit: int = 200) -> None:
    """Windows 上检查产物里的超长相对路径。

    资源最终会落在 `%LOCALAPPDATA%\\TogoSpace\\backend\\` 这类目录下（约 50 字符），
    再加用户解压/安装的目录深度，相对路径超过 ~200 字符时很容易越过 MAX_PATH=260，
    表现为资源管理器解压 zip 报「路径太长」或 NSIS 安装到一半失败。
    """
    if not is_windows():
        return
    offenders: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(target):
        for name in filenames:
            relative = os.path.relpath(os.path.join(dirpath, name), target)
            if len(relative) > limit:
                offenders.append(relative)
    if not offenders:
        return
    offenders.sort(key=len, reverse=True)
    print(f"⚠️  发现 {len(offenders)} 个超过 {limit} 字符的相对路径，"
          f"Windows 解压/安装可能失败（MAX_PATH=260）：")
    for relative in offenders[:3]:
        print(f"   {len(relative):3d}  {relative}")
    print("   请在 scripts/togo_desktop.spec 的 EXCLUDE_PATTERNS 里排除对应目录")


def build_backend(python: str, arch: str) -> None:
    # build_desktop.py 已构建过时由 Tauri 的 beforeBuildCommand 跳过，避免重复打包
    if os.environ.get("TOGOSPACE_SKIP_BACKEND_BUILD") == "1" and os.path.isfile(_backend_executable()):
        print(f"⏭️  跳过后端构建（已存在）: {_backend_executable()}")
        return

    env = dict(os.environ)
    env["TARGET_ARCH"] = arch

    _run([
        python, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--workpath", BUILD_DIR, "--distpath", BUILD_DIR, SPEC_FILE,
    ], env)

    if not os.path.isdir(DIST_BACKEND):
        raise SystemExit(f"❌ PyInstaller 产物不存在: {DIST_BACKEND}")

    _sync_target_dir(DIST_BACKEND, TARGET_DIR)
    _warn_on_long_paths(TARGET_DIR)

    print(f"✅ 后端已就绪: {TARGET_DIR}")


def main() -> int:
    parser = argparse.ArgumentParser(description="构建 Tauri 桌面形态的 Python 后端")
    parser.add_argument("--arch", choices=["arm64", "x86_64"], default=None)
    parser.add_argument("--python", default=_default_python(), help="用于运行 PyInstaller 的 Python")
    args = parser.parse_args()

    # 未显式指定 --arch 时跟随构建机架构
    arch = args.arch or host_arch()
    build_backend(args.python, arch)
    return 0


if __name__ == "__main__":
    sys.exit(main())

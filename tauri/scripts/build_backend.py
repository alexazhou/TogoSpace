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
import platform
import shutil
import struct
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TAURI_DIR = os.path.dirname(SCRIPT_DIR)
REPO_ROOT = os.path.dirname(TAURI_DIR)

# Windows 控制台重定向输出时默认用本地代码页（如 cp936），emoji 会抛
# UnicodeEncodeError；保留编码，只替换不可编码字符。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

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


def _exe_arch(path: str) -> str | None:
    """读取 Windows PE 头判断可执行文件架构；非 PE（macOS/Linux）返回 None。

    Windows on ARM 上 `platform.machine()` 依赖 PROCESSOR_ARCHITECTURE 环境变量，
    在 x64 模拟进程里可能报成 ARM64，按它选架构会打出错误架构的 sidecar。
    """
    try:
        with open(path, "rb") as file:
            if file.read(2) != b"MZ":
                return None
            file.seek(0x3C)
            offset = struct.unpack("<I", file.read(4))[0]
            file.seek(offset + 4)
            machine = struct.unpack("<H", file.read(2))[0]
    except (OSError, struct.error):
        return None
    return {0x8664: "x86_64", 0xAA64: "arm64"}.get(machine)


def _default_arch() -> str:
    # 默认跟随解释器架构：PyInstaller 只能产出与解释器同架构的产物。
    detected = _exe_arch(sys.executable)
    if detected:
        return detected
    return "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x86_64"


def _python_arch(python: str) -> str:
    """读取解释器自身架构：PyInstaller 只能打包出与解释器同架构的产物。"""
    if os.path.isfile(python):
        detected = _exe_arch(python)
        if detected:
            return detected
    result = subprocess.run(
        [python, "-c", "import platform; print(platform.machine())"],
        capture_output=True, text=True, check=True,
    )
    return "arm64" if result.stdout.strip().lower() in ("arm64", "aarch64") else "x86_64"


def _run(command: list[str], env: dict[str, str]) -> None:
    print(f"🏗️  {' '.join(command)}")
    subprocess.run(command, cwd=REPO_ROOT, env=env, check=True)


def _backend_executable() -> str:
    suffix = ".exe" if os.name == "nt" else ""
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
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
    else:
        os.makedirs(target, exist_ok=True)
    shutil.copytree(source, target, dirs_exist_ok=True, symlinks=True)


def _warn_on_long_paths(target: str, limit: int = 200) -> None:
    """Windows 上检查产物里的超长相对路径。

    资源最终会落在 `%LOCALAPPDATA%\\TogoSpace\\backend\\` 这类目录下（约 50 字符），
    再加用户解压/安装的目录深度，相对路径超过 ~200 字符时很容易越过 MAX_PATH=260，
    表现为资源管理器解压 zip 报「路径太长」或 NSIS 安装到一半失败。
    """
    if os.name != "nt":
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

    # PyInstaller 不支持交叉编译：Windows / Linux 上产物架构由解释器决定，
    # 请求的架构和解释器不一致时必须提前失败，否则会打出一个架构不对的 sidecar。
    if sys.platform != "darwin":
        interpreter_arch = _python_arch(python)
        if interpreter_arch != arch:
            raise SystemExit(
                f"❌ 架构不匹配：请求 {arch}，但 {python} 是 {interpreter_arch}。\n"
                f"   PyInstaller 不支持交叉编译，请改用 {arch} 架构的 Python 环境，"
                f"或去掉 --arch 使用本机架构。")

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
    parser.add_argument("--arch", choices=["arm64", "x86_64"], default=_default_arch())
    parser.add_argument("--python", default=_default_python(), help="用于运行 PyInstaller 的 Python")
    args = parser.parse_args()

    build_backend(args.python, args.arch)
    return 0


if __name__ == "__main__":
    sys.exit(main())

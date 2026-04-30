#!/usr/bin/env python3
"""
macOS 打包脚本：构建 TogoSpace.app

步骤：
  1. 读取后端版本号（src/version.py）
  2. 前端构建（npm run build）
  3. 同步前端产物到 assets/frontend/
  4. PyInstaller 打包
  5. 重命名产物为带版本号的 .app
"""

import os
import re
import shutil
import subprocess
import sys
import stat

import PyInstaller.__main__

# ── 路径常量 ──────────────────────────────────────────────────────────────────

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT  = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
DIST_PATH  = os.path.join(REPO_ROOT, "dist")
BUILD_PATH = os.path.join(REPO_ROOT, "build")
SPEC_FILE  = os.path.join(SCRIPT_DIR, "togo_agent.spec")


# ── 版本读取 ──────────────────────────────────────────────────────────────────

def _read_backend_version() -> str:
    path = os.path.join(REPO_ROOT, "src", "version.py")
    content = open(path).read()
    m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)
    if not m:
        print("❌ 无法从 src/version.py 读取版本号")
        sys.exit(1)
    return m.group(1)


# ── 前端构建 ──────────────────────────────────────────────────────────────────

def _build_frontend():
    frontend_dir = os.path.join(REPO_ROOT, "frontend")
    print("✳️  构建前端...")
    subprocess.run(["npm", "run", "build"], cwd=frontend_dir, check=True)
    print("✅ 前端构建完成")


def _assert_frontend_submodule_clean():
    frontend_dir = os.path.join(REPO_ROOT, "frontend")
    result = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=frontend_dir,
        capture_output=True,
        text=True,
        check=True,
    )
    dirty_lines = [line for line in result.stdout.splitlines() if line.strip()]
    if dirty_lines:
        print("❌ 前端子模块存在已跟踪文件的未提交改动，请先提交或还原后再打包：", file=sys.stderr)
        for line in dirty_lines:
            print(f"   {line}", file=sys.stderr)
        sys.exit(1)


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
    original = os.path.join(DIST_PATH, "TogoSpace.app")
    final    = os.path.join(DIST_PATH, f"TogoSpace-{version}.app")
    if os.path.exists(original):
        os.rename(original, final)
        print(f"✅ 产物：dist/TogoSpace-{version}.app")
    else:
        print(f"❌ 未找到 {original}")
        sys.exit(1)


# ── Quarantine 检查 ────────────────────────────────────────────────────────────

def _check_quarantine_on_executables():
    """检查 assets/execute 目录下的可执行文件是否有 quarantine 属性。

    macOS Gatekeeper 会给从网络下载的文件添加 com.apple.quarantine 属性，
    导致首次执行时被拦截。构建前需确保这些文件没有该属性。
    """
    if sys.platform != "darwin":
        return  # 仅在 macOS 上检查

    execute_dir = os.path.join(REPO_ROOT, "assets", "execute")
    if not os.path.exists(execute_dir):
        return

    quarantine_attr = "com.apple.quarantine"
    errors = []

    for root, dirs, files in os.walk(execute_dir):
        for filename in files:
            filepath = os.path.join(root, filename)

            # 检查是否为可执行文件
            try:
                file_stat = os.stat(filepath)
                if not (file_stat.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)):
                    continue  # 不是可执行文件，跳过
            except OSError:
                continue

            # 检查是否有 quarantine 属性
            result = subprocess.run(
                ["xattr", "-l", filepath],
                capture_output=True,
                text=True,
            )
            if quarantine_attr in result.stdout:
                rel_path = os.path.relpath(filepath, REPO_ROOT)
                errors.append(rel_path)

    if errors:
        print("❌ 以下可执行文件存在 quarantine 属性，请移除后再构建：", file=sys.stderr)
        for path in errors:
            print(f"   {path}", file=sys.stderr)
        print(file=sys.stderr)
        print("移除方法：xattr -d com.apple.quarantine <文件路径>", file=sys.stderr)
        sys.exit(1)


# ── 主流程 ────────────────────────────────────────────────────────────────────

def main():
    backend_ver = _read_backend_version()

    print(f"ℹ️  版本：{backend_ver}")

    _check_quarantine_on_executables()
    _assert_frontend_submodule_clean()
    _build_frontend()
    _sync_frontend()
    _clean()
    _run_pyinstaller()
    _rename_app(backend_ver)


if __name__ == "__main__":
    main()

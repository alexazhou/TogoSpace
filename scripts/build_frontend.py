#!/usr/bin/env python3
"""
前端资源构建脚本：构建、同步产物到 assets/frontend/

用法：
  python scripts/build_frontend.py             # 构建 + sync（默认跳过 npm install）
  python scripts/build_frontend.py --install   # npm install + 构建 + sync
  python scripts/build_frontend.py --no-sync   # 仅构建，不同步到 assets/frontend/
"""

import argparse
import os
import shutil
import subprocess
import sys

SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT    = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
FRONTEND_DIR = os.path.join(REPO_ROOT, "frontend")
DIST_DIR     = os.path.join(FRONTEND_DIR, "dist")
ASSETS_DIR   = os.path.join(REPO_ROOT, "assets", "frontend")


def _check_submodule():
    if not os.path.exists(os.path.join(FRONTEND_DIR, "package.json")):
        print("❌ frontend/ 子模块未初始化，请先运行：", file=sys.stderr)
        print("   git submodule update --init frontend", file=sys.stderr)
        sys.exit(1)


def _run(cmd: list[str], cwd: str = FRONTEND_DIR):
    print(f"  $ {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=cwd)
    if result.returncode != 0:
        print(f"❌ 命令失败（exit {result.returncode}）: {' '.join(cmd)}", file=sys.stderr)
        sys.exit(result.returncode)


def _npm_install():
    print("\n--- 1. npm install ---")
    _run(["npm", "install"])
    print("✅ 依赖安装完成")


def _npm_build():
    print("\n--- 2. npm run build ---")
    _run(["npm", "run", "build"])
    if not os.path.isdir(DIST_DIR):
        print(f"❌ 构建产物目录不存在: {DIST_DIR}", file=sys.stderr)
        sys.exit(1)
    print("✅ 前端构建完成")


def _sync():
    print("\n--- 3. 同步产物 → assets/frontend/ ---")
    if os.path.exists(ASSETS_DIR):
        shutil.rmtree(ASSETS_DIR)
    shutil.copytree(DIST_DIR, ASSETS_DIR)
    size_mb = sum(
        os.path.getsize(os.path.join(dp, f))
        for dp, _, files in os.walk(ASSETS_DIR)
        for f in files
    ) / (1024 * 1024)
    print(f"✅ 同步完成（{size_mb:.1f} MB → assets/frontend/）")


def main():
    parser = argparse.ArgumentParser(description="前端资源构建脚本")
    parser.add_argument("--install", action="store_true", help="执行 npm install")
    parser.add_argument("--no-sync", action="store_true", help="不同步产物到 assets/frontend/")
    args = parser.parse_args()

    _check_submodule()

    if args.install:
        _npm_install()
    else:
        print("⏭️  已跳过 npm install（如需安装依赖请加 --install）")

    _npm_build()

    if not args.no_sync:
        _sync()
    else:
        print(f"⏭️  已跳过同步（产物在 frontend/dist/）")

    print("\n✅ 前端构建完成")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""把 frontend/dist 同步到 assets/frontend，并校验两份产物逐文件哈希一致（#243）。

为什么单独有这个脚本：`tauri/scripts/build_desktop.py` 的 `frontend` 动作本来就干这件事，
但它内部用 `subprocess.run(["npm", ...])` 调 npm —— Windows 上没有 `npm.exe`（只有 `npm.cmd`），
不经过 shell 的 CreateProcess 找不到它，实测报 `FileNotFoundError: [WinError 2]`
（run 43617826032）。`build_desktop.py` 不属本单允许改动的文件，所以：
  - 前端构建（`npm run build`）交给 workflow 的 shell 跑；
  - 同步 + 哈希校验用本脚本复刻同一套判据（同 `_tree_hashes` 口径：逐文件 sha256 比较）。

用法：python scripts/sync_frontend_assets.py [--check-only]
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIST = os.path.join(REPO_ROOT, "frontend", "dist")
BACKEND_ASSETS = os.path.join(REPO_ROOT, "assets", "frontend")
REQUIRED = ("index.html", "splash.html")


def tree_hashes(root: str) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            path = os.path.join(dirpath, name)
            relative = os.path.relpath(path, root)
            digest = hashlib.sha256()
            with open(path, "rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            hashes[relative] = digest.hexdigest()
    return hashes


def main() -> int:
    parser = argparse.ArgumentParser(description="同步前端产物到 assets/frontend 并校验哈希")
    parser.add_argument("--check-only", action="store_true", help="只校验，不复制")
    args = parser.parse_args()

    if not os.path.isdir(FRONTEND_DIST):
        print(f"❌ 前端产物目录不存在: {FRONTEND_DIST}（先跑 npm run build）")
        return 1
    for name in REQUIRED:
        if not os.path.isfile(os.path.join(FRONTEND_DIST, name)):
            print(f"❌ 前端产物缺少 {name}: {FRONTEND_DIST}")
            return 1

    if not args.check_only:
        if os.path.isdir(BACKEND_ASSETS):
            shutil.rmtree(BACKEND_ASSETS)
        shutil.copytree(FRONTEND_DIST, BACKEND_ASSETS)

    left = tree_hashes(FRONTEND_DIST)
    right = tree_hashes(BACKEND_ASSETS)
    if left != right:
        only_left = sorted(set(left) - set(right))
        only_right = sorted(set(right) - set(left))
        changed = sorted(k for k in set(left) & set(right) if left[k] != right[k])
        print(f"❌ 前端双份产物不一致（frontend/dist vs assets/frontend）")
        print(f"   只在 dist: {only_left[:10]}")
        print(f"   只在 assets: {only_right[:10]}")
        print(f"   内容不同: {changed[:10]}")
        return 1

    print(f"✅ 前端产物一致: {len(left)} 个文件（frontend/dist == assets/frontend）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

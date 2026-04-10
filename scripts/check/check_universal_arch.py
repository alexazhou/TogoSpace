#!/usr/bin/env python3
"""
检查 macOS .app 包是否为 universal2 架构。

验证主可执行文件和所有 .dylib 文件同时包含 x86_64 和 arm64。
"""

import os
import subprocess
import sys
import argparse


def check_executable_architecture(app_path: str) -> bool:
    """检查主可执行文件是否为 universal2。"""
    executable = os.path.join(app_path, "Contents", "MacOS", "AgentTeam")

    if not os.path.exists(executable):
        # 尝试查找其他可执行文件
        macos_dir = os.path.join(app_path, "Contents", "MacOS")
        if os.path.isdir(macos_dir):
            for f in os.listdir(macos_dir):
                if os.access(os.path.join(macos_dir, f), os.X_OK):
                    executable = os.path.join(macos_dir, f)
                    break

    if not os.path.exists(executable):
        print(f"❌ 未找到可执行文件: {executable}")
        return False

    print(f"\n--- 检查主可执行文件 ---")
    print(f"路径: {executable}")

    try:
        result = subprocess.run(
            ["lipo", "-info", executable],
            capture_output=True, text=True, check=True
        )
        output = result.stdout.strip()
        print(f"架构: {output}")

        if "x86_64" in output and "arm64" in output:
            print("✅ 主可执行文件为 universal2")
            return True
        else:
            print("❌ 主可执行文件不是 universal2")
            return False
    except subprocess.CalledProcessError as e:
        print(f"❌ lipo 检查失败: {e}")
        return False


def check_dylib_architecture(app_path: str) -> bool:
    """检查 .app 包内所有 .dylib 文件的架构是否为 universal2。"""
    print("\n--- 检查动态链接库架构 ---")

    dylibs = []
    for root, _, files in os.walk(app_path):
        for file in files:
            if file.endswith(".dylib"):
                dylibs.append(os.path.join(root, file))

    if not dylibs:
        print("✅ 未找到 .dylib 文件，跳过检查。")
        return True

    all_universal = True
    checked_count = 0

    for dylib in dylibs:
        try:
            result = subprocess.run(
                ["lipo", "-info", dylib],
                capture_output=True, text=True, check=True
            )
            output = result.stdout.strip()

            rel_path = os.path.relpath(dylib, app_path)

            # 通用二进制文件必须同时包含 x86_64 和 arm64
            if "x86_64" not in output or "arm64" not in output:
                print(f"❌ {rel_path} 不是 universal2: {output}")
                all_universal = False
            else:
                checked_count += 1

        except subprocess.CalledProcessError as e:
            rel_path = os.path.relpath(dylib, app_path)
            print(f"❌ 检查 {rel_path} 时出错: {e}")
            all_universal = False

    if all_universal:
        print(f"✅ 所有 {checked_count} 个 .dylib 文件均为 universal2")
    else:
        print("❌ 一个或多个 .dylib 文件不是 universal2")

    return all_universal


def main():
    parser = argparse.ArgumentParser(
        description="检查 .app bundle 是否为 universal2 架构。"
    )
    parser.add_argument("app_path", help="要检查的 .app 文件路径")
    args = parser.parse_args()

    if not os.path.isdir(args.app_path):
        print(f"❌ 路径不存在: {args.app_path}")
        sys.exit(1)

    if not args.app_path.endswith(".app"):
        print(f"❌ 路径不是 .app bundle: {args.app_path}")
        sys.exit(1)

    print(f"检查应用: {args.app_path}")

    exe_ok = check_executable_architecture(args.app_path)
    dylib_ok = check_dylib_architecture(args.app_path)

    if exe_ok and dylib_ok:
        print("\n✅ 架构检查通过：应用为 universal2，可在 Intel 和 ARM Mac 上运行")
        sys.exit(0)
    else:
        print("\n❌ 架构检查失败：应用不是 universal2")
        sys.exit(1)


if __name__ == "__main__":
    main()
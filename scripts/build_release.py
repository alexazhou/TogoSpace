#!/usr/bin/env python3
"""
macOS Release 构建脚本：构建、签名、公证、打包 AgentTeam.app

步骤：
  1. PyInstaller 构建 AgentTeam.app
  2. 代码签名（Developer ID Application）
  3. 公证（Notarization）
  4. Staple 公证结果
  5. 创建 zip 安装包

用法：
  python scripts/build_release.py                    # 完整构建
  python scripts/build_release.py --skip-build       # 跳过构建，仅签名公证
  python scripts/build_release.py --skip-notarize    # 跳过公证，仅签名打包
  python scripts/build_release.py --arch arm64       # 构建 arm64 版本

配置：scripts/build_config.json
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT  = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
DIST_PATH  = os.path.join(REPO_ROOT, "dist")
CONFIG_FILE = os.path.join(SCRIPT_DIR, "build_config.json")
ENTITLEMENTS_FILE = os.path.join(SCRIPT_DIR, "check", "entitlements.plist")


def run_command(command, check=True, capture_output=False, timeout=None, env=None):
    """执行 shell 命令，实时打印输出。"""
    print(f"🚀 执行: {' '.join(command)}")
    try:
        if capture_output:
            result = subprocess.run(
                command, capture_output=True, text=True, check=check,
                timeout=timeout, env=env or os.environ
            )
            return result.stdout.strip()
        else:
            process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, env=env or os.environ
            )
            try:
                for line in iter(process.stdout.readline, ''):
                    print(line, end='')
                process.stdout.close()
                return_code = process.wait(timeout=timeout)
                if check and return_code != 0:
                    raise subprocess.CalledProcessError(return_code, ' '.join(command))
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                print(f"❌ 命令超时 ({timeout}s)", file=sys.stderr)
                sys.exit(1)
    except FileNotFoundError as e:
        print(f"❌ 命令未找到: {e.filename}", file=sys.stderr)
        sys.exit(1)


def load_config():
    """加载 build_config.json 配置文件。"""
    if not os.path.exists(CONFIG_FILE):
        print(f"❌ 配置文件不存在: {CONFIG_FILE}")
        print(f"   请复制 build_config.json.example 为 build_config.json 并填写配置")
        sys.exit(1)

    with open(CONFIG_FILE) as f:
        config = json.load(f)

    required = ["apple_id", "app_specific_password", "team_id", "signing_identity_hash"]
    for field in required:
        if not config.get(field):
            print(f"❌ 配置字段 '{field}' 未填写或为空", file=sys.stderr)
            sys.exit(1)

    return config


def read_version() -> str:
    """从 src/version.py 读取版本号。"""
    path = os.path.join(REPO_ROOT, "src", "version.py")
    content = open(path).read()
    m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)
    if not m:
        print("❌ 无法从 src/version.py 读取版本号")
        sys.exit(1)
    return m.group(1)


def build_app(arch: str):
    """调用 build_mac.py 构建 app。"""
    env = os.environ.copy()
    if arch:
        env["TARGET_ARCH"] = arch
    print("\n--- 1. 构建 PyInstaller 应用 ---")
    run_command(["python", os.path.join(SCRIPT_DIR, "build_mac.py")], env=env)


def sign_app(app_path: str, identity: str):
    """使用 codesign 签名 app。"""
    print("\n--- 2. 代码签名 ---")
    run_command([
        "codesign", "--deep", "--force", "--options=runtime",
        "--sign", identity,
        "--entitlements", ENTITLEMENTS_FILE,
        app_path
    ])
    print("✅ 签名完成")


def verify_signature(app_path: str):
    """验证签名。"""
    print("\n--- 验证签名 ---")
    run_command(["codesign", "--verify", "--deep", "--strict", app_path])
    print("✅ 签名验证通过")


def notarize_app(app_path: str, config: dict):
    """提交公证并等待结果。"""
    print("\n--- 3. 公证 ---")

    notarize_zip = os.path.join(DIST_PATH, "notarize_temp.zip")
    run_command(["ditto", "-c", "-k", "--keepParent", app_path, notarize_zip])

    print("提交公证并等待结果（可能需要几分钟）...")
    run_command([
        "xcrun", "notarytool", "submit", notarize_zip,
        "--apple-id", config["apple_id"],
        "--password", config["app_specific_password"],
        "--team-id", config["team_id"],
        "--wait"
    ], timeout=600)

    os.remove(notarize_zip)
    print("✅ 公证完成")


def staple_app(app_path: str):
    """Staple 公证结果到 app。"""
    print("\n--- 4. Staple 公证结果 ---")
    run_command(["xcrun", "stapler", "staple", app_path])
    print("✅ Staple 完成")


def create_zip(app_path: str, arch: str, version: str) -> str:
    """创建 zip 安装包。"""
    print("\n--- 5. 创建 zip 安装包 ---")
    zip_name = f"AgentTeam-{version}-macos-{arch}.zip"
    zip_path = os.path.join(DIST_PATH, zip_name)

    if os.path.exists(zip_path):
        os.remove(zip_path)

    run_command(["ditto", "-c", "-k", "--keepParent", app_path, zip_path])

    size_mb = os.path.getsize(zip_path) / (1024 * 1024)
    print(f"✅ 安装包: {zip_name} ({size_mb:.1f} MB)")

    return zip_path


def main():
    parser = argparse.ArgumentParser(description="AgentTeam Release 构建脚本")
    parser.add_argument("--skip-build", action="store_true", help="跳过 PyInstaller 构建步骤")
    parser.add_argument("--skip-notarize", action="store_true", help="跳过公证步骤（仅签名打包）")
    parser.add_argument("--arch", type=str, default=None, choices=["arm64", "x86_64"],
                        help="目标架构（默认自动检测）")
    parser.add_argument("--clean", action="store_true", help="构建前清理 dist 和 build 目录")
    args = parser.parse_args()

    config = load_config()
    version = read_version()

    if args.arch:
        arch = args.arch
    else:
        import platform
        machine = platform.machine().lower()
        arch = "arm64" if machine in ["arm64", "aarch64"] else "x86_64"

    print(f"ℹ️  版本: {version}")
    print(f"ℹ️  架构: {arch}")
    print(f"ℹ️  签名身份: {config['signing_identity_hash']}")

    app_path = os.path.join(DIST_PATH, f"AgentTeam-{version}.app")

    if args.clean and not args.skip_build:
        for path in [DIST_PATH, os.path.join(REPO_ROOT, "build")]:
            if os.path.exists(path):
                shutil.rmtree(path)
                print(f"🗑️  已删除: {os.path.relpath(path, REPO_ROOT)}")

    if args.skip_build:
        if not os.path.exists(app_path):
            print(f"❌ --skip-build 指定，但 app 不存在: {app_path}", file=sys.stderr)
            sys.exit(1)
        print(f"⚠️  跳过构建，使用现有 app: {app_path}")
    else:
        build_app(arch)

    if not os.path.exists(app_path):
        print(f"❌ 构建失败，app 不存在: {app_path}", file=sys.stderr)
        sys.exit(1)

    sign_app(app_path, config["signing_identity_hash"])
    verify_signature(app_path)

    if args.skip_notarize:
        print("\n⚠️  跳过公证步骤")
    else:
        notarize_app(app_path, config)
        staple_app(app_path)

    zip_path = create_zip(app_path, arch, version)

    print("\n" + "=" * 50)
    print("✅ 构建完成!")
    print(f"   安装包: {zip_path}")
    if args.skip_notarize:
        print("   ⚠️  注意：此包未公证，分发前需要完成公证")
    print("=" * 50)


if __name__ == "__main__":
    main()
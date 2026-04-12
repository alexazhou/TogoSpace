#!/usr/bin/env python3
"""
macOS Release 构建脚本：构建、签名、公证、打包 AgentTeam.app

可用 action：
  build     — PyInstaller 构建 AgentTeam.app
  sign      — 代码签名 + 验证（Developer ID Application）
  notarize  — 公证 + Staple
  zip       — 创建 zip 安装包
  check     — 查看当前 app 的签名与公证状态

用法：
  python scripts/build_release.py                          # 完整流程（build,sign,notarize,zip）
  python scripts/build_release.py --action build           # 仅构建
  python scripts/build_release.py --action sign            # 仅签名（app 须已存在）
  python scripts/build_release.py --action notarize        # 仅公证（app 须已签名）
  python scripts/build_release.py --action sign,notarize   # 签名 + 公证
  python scripts/build_release.py --action build,sign,zip  # 构建 + 签名 + 打包（跳过公证）
  python scripts/build_release.py --action check           # 查看签名与公证状态
  python scripts/build_release.py --arch arm64             # 指定架构

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

ALL_ACTIONS = ["build", "sign", "notarize", "zip"]
PIPELINE_ACTIONS = ALL_ACTIONS
EXTRA_ACTIONS = ["check"]
VALID_ACTIONS = ALL_ACTIONS + EXTRA_ACTIONS


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


def check_app(app_path: str):
    """检查 app 的签名与公证状态。"""
    print("\n--- 检查签名与公证状态 ---")

    if not os.path.exists(app_path):
        print(f"❌ app 不存在: {app_path}", file=sys.stderr)
        sys.exit(1)

    # 签名信息
    print("\n📋 签名信息:")
    result = subprocess.run(
        ["codesign", "-dvv", app_path],
        capture_output=True, text=True
    )
    info = result.stderr.strip()
    if not info:
        print("   ⚠️  未签名")
    else:
        for line in info.splitlines():
            if any(k in line for k in [
                "Authority=", "TeamIdentifier=", "Timestamp=",
                "Notarization Ticket=", "flags=", "Identifier=", "Format="
            ]):
                print(f"   {line.strip()}")

    # 签名验证
    print("\n🔍 签名验证:")
    verify = subprocess.run(
        ["codesign", "--verify", "--deep", "--strict", app_path],
        capture_output=True, text=True
    )
    if verify.returncode == 0:
        print("   ✅ 签名有效")
    else:
        print(f"   ❌ 签名无效: {verify.stderr.strip()}")

    # Staple 验证
    print("\n🔍 公证 Staple 验证:")
    staple = subprocess.run(
        ["xcrun", "stapler", "validate", app_path],
        capture_output=True, text=True
    )
    if staple.returncode == 0:
        print("   ✅ 已公证且 Staple 有效")
    else:
        print("   ⚠️  未 Staple 或公证未完成")

    # Gatekeeper 评估
    print("\n🔍 Gatekeeper 评估:")
    gk = subprocess.run(
        ["spctl", "--assess", "--type", "execute", "--verbose=2", app_path],
        capture_output=True, text=True
    )
    gk_output = (gk.stdout + gk.stderr).strip()
    if gk.returncode == 0:
        print(f"   ✅ {gk_output}")
    else:
        print(f"   ❌ {gk_output}")


def parse_actions(raw: str | None) -> list[str]:
    """解析 --action 参数，返回有序去重的 action 列表。"""
    if raw is None:
        return list(PIPELINE_ACTIONS)

    seen: set[str] = set()
    actions: list[str] = []
    for token in raw.split(","):
        name = token.strip().lower()
        if name not in VALID_ACTIONS:
            print(f"❌ 未知 action: '{name}'（可选: {', '.join(VALID_ACTIONS)}）", file=sys.stderr)
            sys.exit(1)
        if name not in seen:
            seen.add(name)
            actions.append(name)

    if not actions:
        print("❌ --action 不能为空", file=sys.stderr)
        sys.exit(1)

    # check 是独立操作，不与流水线步骤混合
    if "check" in seen:
        if len(seen) > 1:
            print("⚠️  check 为独立操作，忽略其他 action")
        return ["check"]

    canonical = [a for a in PIPELINE_ACTIONS if a in seen]
    if canonical != actions:
        print(f"⚠️  action 已按流程重排: {','.join(canonical)}")
        actions = canonical

    return actions


def main():
    parser = argparse.ArgumentParser(description="AgentTeam Release 构建脚本")
    parser.add_argument("--action", type=str, default=None,
                        help=f"要执行的步骤（逗号分隔，可选: {','.join(VALID_ACTIONS)}；默认全部流水线）")
    parser.add_argument("--arch", type=str, default=None, choices=["arm64", "x86_64"],
                        help="目标架构（默认自动检测）")
    parser.add_argument("--clean", action="store_true", help="构建前清理 dist 和 build 目录")
    args = parser.parse_args()

    actions = parse_actions(args.action)
    needs_sign_config = bool({"sign", "notarize"} & set(actions))
    config = load_config() if needs_sign_config else {}
    version = read_version()

    if args.arch:
        arch = args.arch
    else:
        import platform
        machine = platform.machine().lower()
        arch = "arm64" if machine in ["arm64", "aarch64"] else "x86_64"

    print(f"ℹ️  版本: {version}")
    print(f"ℹ️  架构: {arch}")
    print(f"ℹ️  执行步骤: {' → '.join(actions)}")
    if needs_sign_config:
        print(f"ℹ️  签名身份: {config['signing_identity_hash']}")

    app_path = os.path.join(DIST_PATH, f"AgentTeam-{version}.app")

    # --- check（独立操作，直接返回）---
    if "check" in actions:
        check_app(app_path)
        return

    if args.clean and "build" in actions:
        for path in [DIST_PATH, os.path.join(REPO_ROOT, "build")]:
            if os.path.exists(path):
                shutil.rmtree(path)
                print(f"🗑️  已删除: {os.path.relpath(path, REPO_ROOT)}")

    # --- build ---
    if "build" in actions:
        build_app(arch)
    
    # app 必须存在才能执行后续步骤
    remaining = [a for a in actions if a != "build"]
    if remaining and not os.path.exists(app_path):
        print(f"❌ app 不存在: {app_path}", file=sys.stderr)
        print(f"   请先执行 --action build 或确认 dist 目录中已有构建产物")
        sys.exit(1)

    # --- sign ---
    if "sign" in actions:
        sign_app(app_path, config["signing_identity_hash"])
        verify_signature(app_path)

    # --- notarize ---
    if "notarize" in actions:
        notarize_app(app_path, config)
        staple_app(app_path)

    # --- zip ---
    zip_path = None
    if "zip" in actions:
        zip_path = create_zip(app_path, arch, version)

    print("\n" + "=" * 50)
    print(f"✅ 完成! 已执行: {', '.join(actions)}")
    if zip_path:
        print(f"   安装包: {zip_path}")
    if "notarize" not in actions and "sign" in actions:
        print("   ⚠️  注意：此包未公证，分发前需要完成公证")
    print("=" * 50)


if __name__ == "__main__":
    main()
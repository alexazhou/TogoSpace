#!/usr/bin/env python3
"""TogoSpace 桌面应用构建脚本（Tauri 外壳 + PyInstaller 后端）。

可用 action（逗号分隔，按顺序执行）：
  frontend  — 构建前端并同步到 assets/frontend（哈希校验双份一致）
  backend   — PyInstaller onedir 打包后端并复制到 tauri/build/backend
  app       — 同步版本号并执行 `tauri build`（产出 .app）
  sign      — 按「内层 Mach-O → 主程序 → .app」顺序签名（macOS）
  zip       — 生成 DMG（别名 dmg），有签名配置时顺带签 DMG
  notarize  — notarytool 公证 + stapler staple（macOS）
  check     — 打印签名链与 Gatekeeper 评估结果

默认流程：frontend,backend,app,sign,zip,notarize（非 macOS 只跑前三步）。

用法：
  python3 tauri/scripts/build_desktop.py
  python3 tauri/scripts/build_desktop.py --arch x86_64
  python3 tauri/scripts/build_desktop.py --action frontend,app
  python3 tauri/scripts/build_desktop.py --action sign,zip

签名/公证配置按字段取第一个可用来源：
  1. 环境变量 APPLE_ID / APPLE_PASSWORD / APPLE_TEAM_ID / APPLE_SIGNING_IDENTITY
  2. scripts/build_config.json：apple_id / app_specific_password / team_id / signing_identity_hash
两处都不完整时跳过签名与公证，产物仍会生成。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TAURI_DIR = os.path.dirname(SCRIPT_DIR)
REPO_ROOT = os.path.dirname(TAURI_DIR)

FRONTEND_DIR = os.path.join(REPO_ROOT, "frontend")
FRONTEND_DIST = os.path.join(FRONTEND_DIR, "dist")
BACKEND_ASSETS = os.path.join(REPO_ROOT, "assets", "frontend")
TAURI_CONF = os.path.join(TAURI_DIR, "src-tauri", "tauri.conf.json")
CARGO_TOML = os.path.join(TAURI_DIR, "src-tauri", "Cargo.toml")
PACKAGE_JSON = os.path.join(TAURI_DIR, "package.json")
ENTITLEMENTS = os.path.join(SCRIPT_DIR, "entitlements.mac.plist")
BUILD_CONFIG = os.path.join(REPO_ROOT, "scripts", "build_config.json")

TARGET_ROOT = os.path.join(TAURI_DIR, "build", "target")
ARTIFACT_DIR = os.path.join(REPO_ROOT, "dist")
BACKEND_DIR = os.path.join(TAURI_DIR, "build", "backend")

ALL_ACTIONS = ["frontend", "backend", "app", "sign", "zip", "notarize", "check"]
PIPELINE_ACTIONS = ["frontend", "backend", "app", "sign", "zip", "notarize"]
ACTION_ALIASES = {"dmg": "zip"}

ARCH_TO_TRIPLE = {
    "arm64": "aarch64-apple-darwin",
    "x86_64": "x86_64-apple-darwin",
}


# ── 通用工具 ─────────────────────────────────────────────────────────────────


def run_command(command: list[str], cwd: str | None = None, env: dict | None = None,
                timeout: int | None = None) -> None:
    print(f"🚀 {' '.join(command)}")
    subprocess.run(command, cwd=cwd, env=env, check=True, timeout=timeout)


def read_version() -> str:
    path = os.path.join(REPO_ROOT, "src", "version.py")
    content = open(path, encoding="utf-8").read()
    match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)
    if not match:
        raise SystemExit("❌ 无法从 src/version.py 读取版本号")
    return match.group(1)


SIGN_CONFIG_FIELDS = {
    "apple_id": "APPLE_ID",
    "app_specific_password": "APPLE_PASSWORD",
    "team_id": "APPLE_TEAM_ID",
    "signing_identity_hash": "APPLE_SIGNING_IDENTITY",
}


def resolve_signing_config() -> dict | None:
    """签名/公证配置：环境变量优先，build_config.json 按字段回退。

    返回 None 表示凭证不完整，调用方应跳过签名/公证。
    """
    file_config: dict = {}
    if os.path.exists(BUILD_CONFIG):
        with open(BUILD_CONFIG, encoding="utf-8") as file:
            file_config = json.load(file)

    config = {}
    for field, env_name in SIGN_CONFIG_FIELDS.items():
        value = os.environ.get(env_name) or file_config.get(field)
        if value:
            config[field] = value

    missing = [field for field in SIGN_CONFIG_FIELDS if field not in config]
    if missing:
        details = ", ".join(f"{field}(env {SIGN_CONFIG_FIELDS[field]})" for field in missing)
        print(f"⚠️  缺少签名/公证凭证: {details}")
        return None
    return config


def host_arch() -> str:
    return "arm64" if platform.machine() == "arm64" else "x86_64"


def target_triple(arch: str) -> str:
    try:
        return ARCH_TO_TRIPLE[arch]
    except KeyError:
        raise SystemExit(f"❌ 不支持的架构: {arch}（可选 arm64 / x86_64）")


def app_bundle_path(arch: str) -> str:
    triple = target_triple(arch)
    candidates = [
        os.path.join(TARGET_ROOT, triple, "release", "bundle", "macos", "TogoSpace.app"),
        os.path.join(TARGET_ROOT, "release", "bundle", "macos", "TogoSpace.app"),
    ]
    for candidate in candidates:
        if os.path.isdir(candidate):
            return candidate
    return candidates[0]


def dmg_path(arch: str) -> str:
    return os.path.join(ARTIFACT_DIR, f"TogoSpace_{read_version()}_{arch}.dmg")


# ── frontend ─────────────────────────────────────────────────────────────────


def _tree_hashes(root: str) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            if os.path.islink(path):
                continue
            relative = os.path.relpath(path, root)
            digest = hashlib.sha256()
            with open(path, "rb") as file:
                for chunk in iter(lambda: file.read(1024 * 1024), b""):
                    digest.update(chunk)
            hashes[relative] = digest.hexdigest()
    return hashes


def build_frontend() -> None:
    print("\n--- frontend: 构建并同步双份产物 ---")
    run_command(["npm", "run", "build"], cwd=FRONTEND_DIR)

    for name in ("index.html", "splash.html"):
        if not os.path.isfile(os.path.join(FRONTEND_DIST, name)):
            raise SystemExit(f"❌ 前端产物缺少 {name}: {FRONTEND_DIST}")

    if os.path.isdir(BACKEND_ASSETS):
        shutil.rmtree(BACKEND_ASSETS)
    shutil.copytree(FRONTEND_DIST, BACKEND_ASSETS)

    tauri_hashes = _tree_hashes(FRONTEND_DIST)
    backend_hashes = _tree_hashes(BACKEND_ASSETS)
    if tauri_hashes != backend_hashes:
        raise SystemExit("❌ 前端双份产物哈希不一致（frontend/dist vs assets/frontend）")
    print(f"✅ 前端产物一致: {len(tauri_hashes)} 个文件")


# ── backend ──────────────────────────────────────────────────────────────────


def build_backend(arch: str) -> None:
    print("\n--- backend: PyInstaller onedir ---")
    script = os.path.join(SCRIPT_DIR, "build_backend.py")
    run_command([sys.executable, script, "--arch", arch], cwd=REPO_ROOT)


# ── app ──────────────────────────────────────────────────────────────────────


def sync_version(version: str) -> None:
    with open(TAURI_CONF, encoding="utf-8") as file:
        conf = json.load(file)
    if conf.get("version") != version:
        conf["version"] = version
        with open(TAURI_CONF, "w", encoding="utf-8") as file:
            json.dump(conf, file, ensure_ascii=False, indent=2)
            file.write("\n")
        print(f"✅ tauri.conf.json version → {version}")

    cargo = open(CARGO_TOML, encoding="utf-8").read()
    updated_cargo = re.sub(
        r'(?m)^version = "[^"]+"', f'version = "{version}"', cargo, count=1)
    if updated_cargo != cargo:
        open(CARGO_TOML, "w", encoding="utf-8").write(updated_cargo)
        print(f"✅ Cargo.toml version → {version}")

    with open(PACKAGE_JSON, encoding="utf-8") as file:
        package = json.load(file)
    if package.get("version") != version:
        package["version"] = version
        with open(PACKAGE_JSON, "w", encoding="utf-8") as file:
            json.dump(package, file, ensure_ascii=False, indent=2)
            file.write("\n")
        print(f"✅ tauri/package.json version → {version}")


def build_app(arch: str) -> None:
    print("\n--- app: tauri build ---")
    if not os.path.isfile(os.path.join(FRONTEND_DIST, "index.html")):
        raise SystemExit("❌ 前端尚未构建，请先执行 --action frontend")

    sync_version(read_version())

    env = dict(os.environ)
    backend_executable = os.path.join(
        BACKEND_DIR, "TogoSpace-backend.exe" if os.name == "nt" else "TogoSpace-backend")
    if os.path.isfile(backend_executable):
        # beforeBuildCommand 里会再调一次 build_backend.py，已构建则跳过
        env["TOGOSPACE_SKIP_BACKEND_BUILD"] = "1"

    run_command(
        ["npm", "run", "tauri", "--", "build",
         "--target", target_triple(arch), "--bundles", "app"],
        cwd=TAURI_DIR,
        env=env,
    )

    app_path = app_bundle_path(arch)
    if not os.path.isdir(app_path):
        raise SystemExit(f"❌ 未找到构建产物: {app_path}")
    print(f"✅ 应用已生成: {app_path}")


# ── sign ─────────────────────────────────────────────────────────────────────


def _is_macho(path: str) -> bool:
    if os.path.islink(path) or not os.path.isfile(path):
        return False
    try:
        result = subprocess.run(
            ["file", "-b", path], capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return False
    return "Mach-O" in result.stdout


def _codesign(path: str, identity: str, entitlements: str | None = None) -> None:
    command = [
        "codesign", "--force", "--timestamp", "--options=runtime",
        "--sign", identity,
    ]
    if entitlements:
        command += ["--entitlements", entitlements]
    command.append(path)
    run_command(command)


def sign_app(arch: str) -> None:
    print("\n--- sign: 代码签名 ---")
    if sys.platform != "darwin":
        raise SystemExit("❌ 签名仅支持 macOS")
    config = resolve_signing_config()
    if config is None:
        print("⚠️  跳过签名")
        return
    identity = config["signing_identity_hash"]
    app_path = app_bundle_path(arch)
    if not os.path.isdir(app_path):
        raise SystemExit(f"❌ app 不存在: {app_path}")

    contents = os.path.join(app_path, "Contents")
    nested: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(contents):
        if os.path.abspath(dirpath) == os.path.abspath(os.path.join(contents, "MacOS")):
            continue
        for name in filenames:
            path = os.path.join(dirpath, name)
            if _is_macho(path):
                nested.append(path)
    # 先签内层：嵌套可执行文件必须早于外层 bundle
    nested.sort(key=lambda path: path.count(os.sep), reverse=True)
    print(f"🔏 嵌套 Mach-O: {len(nested)} 个")
    for path in nested:
        _codesign(path, identity, ENTITLEMENTS)

    main_executable = os.path.join(contents, "MacOS", "TogoSpace")
    _codesign(main_executable, identity, ENTITLEMENTS)
    _codesign(app_path, identity, ENTITLEMENTS)

    run_command(["codesign", "--verify", "--deep", "--strict", "--verbose=2", app_path])
    print("✅ 签名完成并通过校验")


# ── zip (dmg) ────────────────────────────────────────────────────────────────


def build_dmg(arch: str) -> None:
    print("\n--- zip: 生成 DMG ---")
    if sys.platform != "darwin":
        raise SystemExit("❌ DMG 仅支持 macOS")
    app_path = app_bundle_path(arch)
    if not os.path.isdir(app_path):
        raise SystemExit(f"❌ app 不存在: {app_path}")

    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    target = dmg_path(arch)
    if os.path.exists(target):
        os.remove(target)

    staging = tempfile.mkdtemp(prefix="togospace-dmg-")
    try:
        shutil.copytree(app_path, os.path.join(staging, "TogoSpace.app"), symlinks=True)
        os.symlink("/Applications", os.path.join(staging, "Applications"))
        run_command([
            "hdiutil", "create",
            "-volname", "TogoSpace",
            "-srcfolder", staging,
            "-ov", "-format", "UDZO",
            target,
        ])
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    config = resolve_signing_config()
    if config:
        run_command([
            "codesign", "--force", "--sign", config["signing_identity_hash"], target])
    else:
        print("⚠️  跳过 DMG 签名")

    size_mb = os.path.getsize(target) / (1024 * 1024)
    print(f"✅ DMG: {target} ({size_mb:.1f} MB)")


# ── notarize ─────────────────────────────────────────────────────────────────


def notarize_dmg(arch: str) -> None:
    print("\n--- notarize: 公证 + staple ---")
    if sys.platform != "darwin":
        raise SystemExit("❌ 公证仅支持 macOS")
    config = resolve_signing_config()
    if config is None:
        print("⚠️  跳过公证与 staple")
        return
    target = dmg_path(arch)
    if not os.path.isfile(target):
        raise SystemExit(f"❌ DMG 不存在: {target}（请先执行 --action zip）")

    started = time.time()
    run_command([
        "xcrun", "notarytool", "submit", target,
        "--apple-id", config["apple_id"],
        "--password", config["app_specific_password"],
        "--team-id", config["team_id"],
        "--wait",
    ], timeout=1800)
    print(f"⏱️  公证耗时 {time.time() - started:.0f}s")

    run_command(["xcrun", "stapler", "staple", target])
    run_command(["xcrun", "stapler", "validate", target])
    print("✅ 公证与 staple 完成")


# ── check ────────────────────────────────────────────────────────────────────


def check(arch: str) -> None:
    print("\n--- check: 签名与公证状态 ---")
    app_path = app_bundle_path(arch)
    dmg = dmg_path(arch)

    for label, path, assess in (
        ("app", app_path, ["spctl", "--assess", "--type", "execute", "--verbose=2", app_path]),
        ("dmg", dmg, ["spctl", "--assess", "--type", "open", "--verbose=2", dmg]),
    ):
        print(f"\n📦 {label}: {path}")
        if not os.path.exists(path):
            print("   ❌ 不存在")
            continue

        info = subprocess.run(
            ["codesign", "-dvv", path], capture_output=True, text=True)
        lines = [
            line.strip() for line in info.stderr.splitlines()
            if any(key in line for key in (
                "Identifier=", "Authority=", "TeamIdentifier=", "Timestamp=",
                "Format=", "CodeDirectory", "Signature", "Notarization Ticket="))
        ]
        print("   " + ("\n   ".join(lines) if lines else "⚠️  未签名"))

        verify = subprocess.run(
            ["codesign", "--verify", "--deep", "--strict", path], capture_output=True, text=True)
        print("   ✅ 签名有效" if verify.returncode == 0
              else f"   ❌ 签名无效: {verify.stderr.strip()}")

        if assess[0] == "spctl":
            result = subprocess.run(assess, capture_output=True, text=True)
            output = (result.stdout + result.stderr).strip()
            print(("   ✅ " if result.returncode == 0 else "   ⚠️  ") + output)

        if path.endswith(".dmg"):
            staple = subprocess.run(
                ["xcrun", "stapler", "validate", path], capture_output=True, text=True)
            print("   ✅ 公证票据有效" if staple.returncode == 0
                  else "   ⚠️  未 staple 或公证未完成")


# ── 入口 ─────────────────────────────────────────────────────────────────────


def parse_actions(raw: str | None) -> list[str]:
    if raw is None:
        if sys.platform == "darwin":
            return list(PIPELINE_ACTIONS)
        return ["frontend", "backend", "app"]

    actions: list[str] = []
    for item in raw.split(","):
        name = ACTION_ALIASES.get(item.strip().lower(), item.strip().lower())
        if name not in ALL_ACTIONS:
            raise SystemExit(f"❌ 未知 action: {item}（可选: {', '.join(ALL_ACTIONS)}）")
        if name not in actions:
            actions.append(name)
    return actions


def main() -> int:
    parser = argparse.ArgumentParser(description="构建 TogoSpace 桌面应用")
    parser.add_argument("--arch", choices=["arm64", "x86_64"], default=host_arch())
    parser.add_argument("--action", default=None,
                        help=f"逗号分隔，默认全流程: {','.join(PIPELINE_ACTIONS)}")
    args = parser.parse_args()

    actions = parse_actions(args.action)
    print(f"ℹ️  version={read_version()} arch={args.arch} actions={','.join(actions)}")

    for action in actions:
        if action == "frontend":
            build_frontend()
        elif action == "backend":
            build_backend(args.arch)
        elif action == "app":
            build_app(args.arch)
        elif action == "sign":
            sign_app(args.arch)
        elif action == "zip":
            build_dmg(args.arch)
        elif action == "notarize":
            notarize_dmg(args.arch)
        elif action == "check":
            check(args.arch)

    print("\n🎉 构建流程完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())

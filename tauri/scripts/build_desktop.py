#!/usr/bin/env python3
"""TogoSpace 桌面应用构建脚本（Tauri 外壳 + PyInstaller 后端）。

可用 action（逗号分隔，按顺序执行）：
  frontend  — 构建前端并同步到 assets/frontend（哈希校验双份一致）
  backend   — PyInstaller onedir 打包后端并复制到 tauri/build/backend
  app       — 执行 `tauri build`（产出 .app）
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
VERIFY_VERSIONS_SCRIPT = os.path.join(REPO_ROOT, "scripts", "verify_versions.py")
ENTITLEMENTS = os.path.join(SCRIPT_DIR, "entitlements.mac.plist")
BUILD_CONFIG = os.path.join(REPO_ROOT, "scripts", "build_config.json")

TARGET_ROOT = os.path.join(TAURI_DIR, "build", "target")
# 本地构建产物统一放根目录 build/（PyInstaller 与 DMG）
ARTIFACT_DIR = os.path.join(REPO_ROOT, "build")
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


def verify_versions() -> None:
    """构建前校验所有版本文件一致；不一致时直接失败。"""
    run_command([sys.executable, VERIFY_VERSIONS_SCRIPT])


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


def build_app(arch: str) -> None:
    print("\n--- app: tauri build ---")
    if not os.path.isfile(os.path.join(FRONTEND_DIST, "index.html")):
        raise SystemExit("❌ 前端尚未构建，请先执行 --action frontend")

    env = dict(os.environ)
    # Tauri CLI 会读取 APPLE_* 变量并在 tauri build 阶段自动签名/公证；
    # 此时嵌套二进制尚未签名，公证必然失败。签名与公证统一由本脚本后续步骤处理。
    for key in ("APPLE_CERTIFICATE", "APPLE_CERTIFICATE_PASSWORD", "APPLE_SIGNING_IDENTITY",
                "APPLE_ID", "APPLE_PASSWORD", "APPLE_TEAM_ID",
                "APPLE_API_KEY", "APPLE_API_ISSUER", "APPLE_API_KEY_PATH"):
        env.pop(key, None)
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
        # GitHub macOS runner 上 hdiutil 偶发 Resource busy（刚落盘的文件仍被系统占用），
        # 先 sync 等写入完成，失败后清理残留并退避重试。
        run_command(["sync"])
        create_command = [
            "hdiutil", "create",
            "-volname", "TogoSpace",
            "-srcfolder", staging,
            "-ov", "-format", "UDZO",
            "-nospotlight",
            target,
        ]
        for attempt in range(1, 4):
            try:
                run_command(create_command)
                break
            except subprocess.CalledProcessError:
                if attempt == 3:
                    raise
                print(f"⚠️  hdiutil 第 {attempt} 次失败，5 秒后重试")
                time.sleep(5)
                try:
                    os.remove(target)
                except OSError:
                    pass
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

TICKET_MARKER = "Notarization Ticket=stapled"
NOTARIZED_SOURCE = "Notarized Developer ID"


def _capture(command: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    """跑命令并捕获输出，不抛异常，由调用方按返回码判定。

    超时也折成「失败返回码」，避免半途卡死：本地判据单条最多 60s，
    而这些命令实测都是毫秒级，超时本身就该算异常。
    """
    try:
        return subprocess.run(
            command, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            args=command, returncode=-1, stdout="",
            stderr=f"（命令超时 {timeout}s: {' '.join(command)}）")


def _raw_text(result: subprocess.CompletedProcess) -> str:
    return (result.stdout + result.stderr).strip()


def _print_raw(result: subprocess.CompletedProcess) -> None:
    text = _raw_text(result)
    for line in (text.splitlines() if text else ["（无输出）"]):
        print(f"   {line}")


def verify_stapled_ticket(target: str) -> None:
    """校验 stapled 票据，判据全部是本地读取（不联网）。

    替代原来的 `xcrun stapler validate`：后者会向 Apple 回查票据，该步在
    #146 表现为间歇性、非确定性失败（NSURLErrorDomain -1005 / `Error 68`，
    机制未定），会让已公证成功的 DMG 被判红丢弃。实测断网下 validate 必挂，
    而下面这些判据照常工作。

    硬判据（任一不过即 SystemExit，不降级为警告）：
      ① `codesign --verify --deep --strict <DMG>` 返回码 0 —— 签名完整性
      ② `codesign -dvvv <DMG>` 含 `Notarization Ticket=stapled` —— 主判据
         （票据写在 DMG 容器的签名 blob 里，`.app` 上既无该字段也无 xattr）
    辅助判据（不致命，只打 ::warning::）：
      ③ `spctl --assess --type install <DMG>` 授 Notarized Developer ID
         该项可能命中本机 Gatekeeper 缓存，在无缓存的 CI runner 上不可靠，
         故只作信号，不作门槛（理由见 #148 实测）。

    语义边界：①② 证明「票据已正确 stapled 到 DMG」，不等价于「Apple 认为该包
    公证通过」——后者由前面的 `notarytool submit --wait` 返回码保证。
    """
    print(f"\n--- 票据校验（本地判据）: {target} ---")
    failures: list[str] = []

    print(f"$ codesign --verify --deep --strict {target}")
    verify = _capture(["codesign", "--verify", "--deep", "--strict", target])
    _print_raw(verify)
    if verify.returncode == 0:
        print("   ✅ 硬判据①签名完整")
    else:
        failures.append(
            f"硬判据①签名校验失败 rc={verify.returncode}: "
            f"{_raw_text(verify) or '（无输出）'}")

    print(f"$ codesign -dvvv {target}")
    detail = _capture(["codesign", "-dvvv", target])
    _print_raw(detail)
    raw_detail = _raw_text(detail)
    tickets = [
        line.strip() for line in raw_detail.splitlines()
        if "Notarization Ticket" in line
    ]
    print(f"   ℹ️  Notarization Ticket 行: {' / '.join(tickets) if tickets else '（无）'}")
    # 整行精确匹配而不是子串包含：`...=staple` 是 `...=stapled` 的前缀，
    # 子串匹配会让写错的期望值照样命中（自测 V2 实测到过）。
    if TICKET_MARKER in tickets:
        print("   ✅ 硬判据②票据已 stapled")
    else:
        # 文案按「签名无效 / 无票据 / 读不到签名」三类区分，便于定位是哪一环坏了：
        #   签名无效 —— codesign --verify 已失败（或被篡改致 dvvv 都读不出）
        #   无票据   —— 签名完好（dvvv 正常输出、verify 通过）但缺该行
        #   读不到   —— dvvv 自己非 0 而 verify 通过，异常态
        if verify.returncode != 0 or detail.returncode != 0:
            reason = "签名无效或未签名"
        else:
            reason = "已签名但未 staple，或票据已丢失"
        failures.append(
            f"硬判据②缺少 `{TICKET_MARKER}` 整行（{reason}；"
            f"verify rc={verify.returncode}, dvvv rc={detail.returncode}），"
            f"实际 Notarization Ticket 行: {' / '.join(tickets) if tickets else '（无）'}")

    print(f"$ spctl --assess --type install --verbose=2 {target}")
    assess = _capture(["spctl", "--assess", "--type", "install", "--verbose=2", target])
    _print_raw(assess)
    if assess.returncode == 0 and NOTARIZED_SOURCE in _raw_text(assess):
        print("   ✅ 辅助判据③ Gatekeeper 认可 Notarized Developer ID")
    else:
        print(f"::warning::辅助判据③未通过 rc={assess.returncode}：Gatekeeper 未授 "
              f"{NOTARIZED_SOURCE}。该项依赖本机 Gatekeeper 缓存，不作为失败条件（见 #148）。")

    if failures:
        # 先打印失败判据清单再抛：让日志本身就能分辨「哪几条判据翻了」，
        # 不用靠 traceback 行号反推（两条判据的 raise 落在同一行）。
        print(f"   ❌ 失败判据 {len(failures)} 项:", file=sys.stderr)
        for item in failures:
            print(f"      - {item}", file=sys.stderr)
        raise SystemExit(
            "❌ 公证票据校验失败（DMG 未通过本地判据）:\n   - " + "\n   - ".join(failures))


def staple_with_retry(target: str, attempts: int = 3, backoff_seconds: int = 5) -> None:
    """对 DMG 执行 `xcrun stapler staple`，失败时有限次退避重试。

    为什么要重试：staple 和 validate 一样**要联网**——它要向 Apple 的
    ticket-delivery 取已公证的票据写进 DMG 的签名 blob。网络抖动会报
    `NSURLErrorDomain -1005 "The network connection was lost"` 并以 `Error 68`
    退出（#146 记录过 validate 的同款失败；v0.5.0 的 x86_64 job 则是 staple
    本身被打中，公证已经 Accepted、产物却整份判红丢掉）。

    重试是幂等的：DMG 已经 staple 过时再 staple 只是重写同一份票据。

    失败仍必须判红（发版产物必须带票据），所以 attempts 用尽后**原样抛出**
    `subprocess.CalledProcessError`，不降级为警告、不吞异常。

    只重试 staple，不碰 `notarytool submit`：公证提交重复会浪费公证额度，
    且 `--wait` 已经等到 Accepted，重提交没有意义。
    """
    command = ["xcrun", "stapler", "staple", target]
    for attempt in range(1, attempts + 1):
        try:
            run_command(command)
            if attempt > 1:
                print(f"✅ stapler staple 第 {attempt} 次尝试成功")
            return
        except subprocess.CalledProcessError as exc:
            if attempt == attempts:
                print(f"❌ stapler staple 连续 {attempts} 次失败（最后一次 rc={exc.returncode}）",
                      file=sys.stderr)
                raise
            print(f"⚠️  stapler staple 第 {attempt}/{attempts} 次失败（rc={exc.returncode}），"
                  f"{backoff_seconds} 秒后重试")
            time.sleep(backoff_seconds)


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

    # staple 要联网取票据，同一网络故障（-1005 / Error 68）会咬它，
    # 见 `staple_with_retry` 的说明。#146/#148 只把 validate 换成本地判据，
    # staple 这一步当时没管，所以同样的故障还能再来一次。
    staple_with_retry(target)
    # 原为 `xcrun stapler validate`（联网回查，-1005/Error 68 时丢弃已公证产物）
    verify_stapled_ticket(target)
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
    verify_versions()
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

#!/usr/bin/env python3
"""校验所有发布版本号与 ``src/version.py`` 一致。

版本号唯一来源是 ``src/version.py``。本脚本只做检测、不修改文件；
CI 与本地构建在开始前运行，发现不一致时直接失败。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = REPO_ROOT / "src" / "version.py"

CARGO_TOML = REPO_ROOT / "tauri" / "src-tauri" / "Cargo.toml"
CARGO_LOCK = REPO_ROOT / "tauri" / "src-tauri" / "Cargo.lock"

_VERSION_PATTERN = re.compile(r'(?m)^__version__\s*=\s*["\']([^"\']+)["\']')
_SECTION_PATTERN = re.compile(r"^\[([^\]]+)\]$")


class VersionReadError(ValueError):
    """读取版本字段失败。"""


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def read_source_version() -> str:
    match = _VERSION_PATTERN.search(_read_text(VERSION_FILE))
    if match is None:
        raise VersionReadError(f"无法从 {VERSION_FILE.relative_to(REPO_ROOT)} 读取 __version__")
    return match.group(1)


def read_json_version(path: Path) -> list[tuple[str, str]]:
    data = json.loads(_read_text(path))
    version = data.get("version")
    if not isinstance(version, str) or not version:
        raise VersionReadError("缺少 version 字段")
    return [("version", version)]


def read_package_lock_versions(path: Path) -> list[tuple[str, str]]:
    data = json.loads(_read_text(path))

    version = data.get("version")
    if not isinstance(version, str) or not version:
        raise VersionReadError("缺少顶层 version 字段")

    packages = data.get("packages")
    root_package = packages.get("") if isinstance(packages, dict) else None
    root_version = root_package.get("version") if isinstance(root_package, dict) else None
    if not isinstance(root_version, str) or not root_version:
        raise VersionReadError('缺少 packages[""].version 字段')

    return [
        ("version", version),
        ('packages[""].version', root_version),
    ]


def read_cargo_package_fields(path: Path) -> dict[str, str]:
    """读取 Cargo.toml 中 ``[package]`` 段的简单键值。"""
    fields: dict[str, str] = {}
    in_package = False

    for raw_line in _read_text(path).splitlines():
        line = raw_line.strip()
        section = _SECTION_PATTERN.match(line)
        if section is not None:
            in_package = section.group(1) == "package"
            continue
        if not in_package or "=" not in line:
            continue

        key, value = line.split("=", 1)
        fields[key.strip()] = value.strip().strip('"')

    return fields


def read_cargo_toml_versions(path: Path) -> list[tuple[str, str]]:
    version = read_cargo_package_fields(path).get("version")
    if not version:
        raise VersionReadError("缺少 [package].version 字段")
    return [("package.version", version)]


def read_cargo_lock_versions(path: Path, package_name: str) -> list[tuple[str, str]]:
    """读取 Cargo.lock 中指定 package 的版本。"""
    current_name: str | None = None
    in_package = False

    for raw_line in _read_text(path).splitlines():
        line = raw_line.strip()
        if line == "[[package]]":
            in_package = True
            current_name = None
            continue
        if not in_package:
            continue
        if line.startswith("name = "):
            current_name = line.split("=", 1)[1].strip().strip('"')
            continue
        if line.startswith("version = ") and current_name == package_name:
            version = line.split("=", 1)[1].strip().strip('"')
            return [("package.version", version)]

    raise VersionReadError(f'Cargo.lock 中找不到 package "{package_name}" 的版本')


def build_checks() -> list[tuple[str, Callable[[], list[tuple[str, str]]]]]:
    cargo_package_name = read_cargo_package_fields(CARGO_TOML).get("name")
    if not cargo_package_name:
        raise VersionReadError("Cargo.toml 缺少 [package].name 字段")

    return [
        ("frontend/package.json", lambda: read_json_version(REPO_ROOT / "frontend" / "package.json")),
        (
            "frontend/package-lock.json",
            lambda: read_package_lock_versions(REPO_ROOT / "frontend" / "package-lock.json"),
        ),
        ("tauri/package.json", lambda: read_json_version(REPO_ROOT / "tauri" / "package.json")),
        (
            "tauri/package-lock.json",
            lambda: read_package_lock_versions(REPO_ROOT / "tauri" / "package-lock.json"),
        ),
        (
            "tauri/src-tauri/tauri.conf.json",
            lambda: read_json_version(REPO_ROOT / "tauri" / "src-tauri" / "tauri.conf.json"),
        ),
        (
            "tauri/src-tauri/Cargo.toml",
            lambda: read_cargo_toml_versions(CARGO_TOML),
        ),
        (
            "tauri/src-tauri/Cargo.lock",
            lambda: read_cargo_lock_versions(CARGO_LOCK, cargo_package_name),
        ),
    ]


def main() -> int:
    try:
        source_version = read_source_version()
    except (OSError, VersionReadError) as exc:
        print(f"❌ 版本号检测失败: {exc}")
        return 1

    print(f"版本号来源: src/version.py = {source_version}\n")

    try:
        checks = build_checks()
    except (OSError, VersionReadError) as exc:
        print(f"❌ 版本号检测失败: {exc}")
        return 1

    mismatches = 0
    for label, reader in checks:
        try:
            entries = reader()
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"❌ {label}: 读取失败: {exc}")
            mismatches += 1
            continue

        for field, version in entries:
            if version == source_version:
                print(f"✅ {label} ({field}): {version}")
            else:
                print(f"❌ {label} ({field}): {version}，期望 {source_version}")
                mismatches += 1

    if mismatches:
        print(f"\n❌ 检测到 {mismatches} 处版本号不一致，请统一为 {source_version}")
        return 1

    print(f"\n✅ 所有版本号一致: {source_version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

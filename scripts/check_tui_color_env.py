#!/usr/bin/env python3
"""Inspect terminal color capabilities relevant to the Textual TUI."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from importlib import metadata

from rich.console import Console


def _print_kv(key: str, value: object) -> None:
    print(f"{key}: {value}")


def _safe_import_version(module_name: str) -> str:
    try:
        module = __import__(module_name)
    except Exception as exc:
        return f"unavailable ({type(exc).__name__}: {exc})"
    version = getattr(module, "__version__", None)
    if version:
        return str(version)
    try:
        return metadata.version(module_name)
    except metadata.PackageNotFoundError:
        return "unknown"


def _run_tput_colors() -> str:
    if not shutil.which("tput"):
        return "unavailable (tput not found)"
    try:
        result = subprocess.run(
            ["tput", "colors"],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception as exc:
        return f"unavailable ({type(exc).__name__}: {exc})"
    return result.stdout.strip() or "empty output"


def _print_truecolor_demo() -> None:
    print("\nTruecolor demo:")
    if not sys.stdout.isatty():
        print("stdout is not a TTY; skipping ANSI color demo.")
        return
    for i in range(0, 256, 8):
        print(f"\x1b[38;2;{i};180;210mTRUECOLOR-{i:03d}\x1b[0m")


def _print_rich_console_info() -> None:
    console = Console()
    print("\nRich console:")
    _print_kv("rich.is_terminal", console.is_terminal)
    _print_kv("rich.color_system", console.color_system)
    _print_kv("rich.legacy_windows", console.legacy_windows)


def _print_ansi16_demo() -> None:
    print("\nANSI 16-color demo:")
    if not sys.stdout.isatty():
        print("stdout is not a TTY; skipping ANSI color demo.")
        return
    for i in range(16):
        print(f"\x1b[48;5;{i}m {i:>2} \x1b[0m", end=" ")
    print()


def _print_256_demo() -> None:
    print("\n256-color demo:")
    if not sys.stdout.isatty():
        print("stdout is not a TTY; skipping ANSI color demo.")
        return
    steps = [16, 21, 27, 33, 39, 45, 51, 87, 123, 159, 195, 231]
    for i in steps:
        print(f"\x1b[48;5;{i}m {i:>3} \x1b[0m", end=" ")
    print()


def main() -> int:
    _print_kv("python", sys.version.replace("\n", " "))
    _print_kv("platform", platform.platform())
    _print_kv("textual", _safe_import_version("textual"))
    _print_kv("rich", _safe_import_version("rich"))
    _print_kv("TERM", os.environ.get("TERM", ""))
    _print_kv("COLORTERM", os.environ.get("COLORTERM", ""))
    _print_kv("TERM_PROGRAM", os.environ.get("TERM_PROGRAM", ""))
    _print_kv("tput colors", _run_tput_colors())
    _print_kv("isatty(stdout)", sys.stdout.isatty())
    _print_rich_console_info()
    _print_ansi16_demo()
    _print_256_demo()
    _print_truecolor_demo()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

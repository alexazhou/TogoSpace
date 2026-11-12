"""#204 改名契约判据：TogoSpace → Togobot（构建链路 + 可见名），并守住「不改项」。

判据分两类，性质不同，不要混为一谈：

- ``TestRenameMustBeRed``：**改名前必须红**。它们断言改名目标已达成。
- ``TestCompatibilityGuard``：**改名前就是绿的，改名后必须仍然绿**。
  它们守的是「不该被顺手改掉」的兼容面（存储根 / 容器 label / session namespace /
  GitHub 仓库名）。若它们变红，说明改名越界了。

反向安全性：全部断言都用「提取名字的集合相等」，不绑行号、不绑上下文，
正确实现不会被误判红。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

# 品牌名只有一个出口：大小写若要改（Togobot ↔ TogoBot），只改这一行。
EXPECTED_NAME = "Togobot"
OLD_NAME = "TogoSpace"

BACKEND_BASE = f"{EXPECTED_NAME}-backend"
OLD_BACKEND_BASE = f"{OLD_NAME}-backend"

_BACKEND_RE = re.compile(r"([A-Za-z][A-Za-z0-9]*)-backend(?:\.exe)?")
_BACKEND_PATH_RE = re.compile(r"backend/([A-Za-z][A-Za-z0-9]*)-backend(?:\.exe)?")

# 构建链路里五处必须同时出现后端可执行名；漏一处则打包后 sidecar 路径失配。
BACKEND_NAME_ANCHORS = {
    "scripts/togo_desktop.spec": _BACKEND_RE,
    "tauri/scripts/build_backend.py": _BACKEND_RE,
    "tauri/scripts/build_desktop.py": _BACKEND_RE,
    "tauri/src-tauri/src/sys/macos.rs": _BACKEND_PATH_RE,
    "tauri/src-tauri/src/sys/linux.rs": _BACKEND_PATH_RE,
    "tauri/src-tauri/src/sys/windows.rs": _BACKEND_PATH_RE,
    ".github/workflows/build-desktop.yml": _BACKEND_PATH_RE,
}

_SCAN_SKIP_DIRS = {
    ".git",
    "node_modules",
    "build",
    "dist",
    "__pycache__",
    ".venv",
    ".mypy_cache",
    ".pytest_cache",
    ".lg204",
}
_SCAN_SUFFIXES = {
    ".py", ".rs", ".toml", ".json", ".yml", ".yaml", ".md", ".txt", ".sh",
    ".html", ".vue", ".ts", ".js", ".ini",
}


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def _repo_text_files() -> list[Path]:
    out: list[Path] = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _SCAN_SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in _SCAN_SUFFIXES:
            out.append(path)
    return out


class TestRenameMustBeRed:
    """以下断言在改名前应当全部失败——它们就是「改完了」的定义。"""

    def test_tauri_product_name_and_window_title(self) -> None:
        conf = _read("tauri/src-tauri/tauri.conf.json")
        product_names = re.findall(r'"productName"\s*:\s*"([^"]+)"', conf)
        assert product_names == [EXPECTED_NAME], f"productName 实际={product_names}"

        titles = re.findall(r'"label"\s*:\s*"splashscreen"[\s\S]{0,120}?"title"\s*:\s*"([^"]+)"', conf)
        assert titles == [EXPECTED_NAME], f"splash 窗口标题实际={titles}"

    def test_cargo_bin_name_matches_product_name(self) -> None:
        """``Contents/MacOS/<name>`` 由 [[bin]].name 决定，.app 名由 productName 决定，两者必须同名。"""
        cargo = _read("tauri/src-tauri/Cargo.toml")
        bin_section = re.search(r"\[\[bin\]\]([\s\S]*?)(?:\n\[|\Z)", cargo)
        assert bin_section is not None, "Cargo.toml 里找不到 [[bin]] 段"
        bin_names = re.findall(r'^\s*name\s*=\s*"([^"]+)"', bin_section.group(1), re.MULTILINE)
        assert bin_names == [EXPECTED_NAME], f"[[bin]].name 实际={bin_names}"

    def test_backend_executable_name_aligned_across_build_chain(self) -> None:
        """七处（spec/两个构建脚本/三平台 sidecar/一条 CI）提取出的后端名必须全等于 Togobot。"""
        bad: dict[str, set[str]] = {}
        for rel, pattern in BACKEND_NAME_ANCHORS.items():
            names = set(pattern.findall(_read(rel)))
            assert names, f"{rel} 里一个 *-backend 名字都没提取到，锚点口径失效"
            if names != {EXPECTED_NAME}:
                bad[rel] = names
        assert not bad, f"后端可执行名未对齐（应为 {EXPECTED_NAME}）: {bad}"

    def test_no_legacy_backend_name_left_anywhere(self) -> None:
        hits = [
            str(path.relative_to(REPO_ROOT))
            for path in _repo_text_files()
            if OLD_BACKEND_BASE in path.read_text(encoding="utf-8", errors="ignore")
        ]
        assert hits == [], f"仍残留 {OLD_BACKEND_BASE}: {hits}"

    def test_macos_app_bundle_and_dmg_names(self) -> None:
        script = _read("tauri/scripts/build_desktop.py")
        assert f'"{EXPECTED_NAME}.app"' in script, "build_desktop.py 未使用新的 .app 名"
        assert f'"{OLD_NAME}.app"' not in script, "build_desktop.py 仍引用旧 .app 名"
        assert f'f"{EXPECTED_NAME}_{{read_version()}}_{{arch}}.dmg"' in script, "dmg 命名未更新"

    def test_user_visible_strings(self) -> None:
        for rel in (
            "tauri/src-tauri/src/tray.rs",
            "tauri/src-tauri/src/lib.rs",
            "frontend/index.html",
            "frontend/splash.html",
        ):
            text = _read(rel)
            assert OLD_NAME not in text, f"{rel} 仍含旧品牌名 {OLD_NAME}"
            assert EXPECTED_NAME in text, f"{rel} 未出现新品牌名 {EXPECTED_NAME}"


class TestCompatibilityGuard:
    """改名前就绿；改名后必须仍然绿。变红 = 越界改了兼容面。"""

    def test_storage_root_unchanged(self) -> None:
        app_paths = _read("src/appPaths.py")
        assert '"~/.togospace"' in app_paths, "存储根被改了——现有实例会读不到数据"
        assert '"TOGOSPACE_DESKTOP"' in app_paths, "TOGOSPACE_DESKTOP 环境变量名被改了"
        assert '"~/.togobot"' not in app_paths, "存储根被改成了新目录"

    def test_container_label_unchanged(self) -> None:
        constants = _read("src/constants.py")
        assert '"com.togospace.container_record_id"' in constants, "容器 label 被改了——已有容器全部失配"

    def test_session_namespace_unchanged(self) -> None:
        session = _read("src/util/sessionUtil.py")
        assert '"togospace/agent-session"' in session, "session namespace 被改了——已有 session 指纹变化"

    def test_github_repo_name_unchanged(self) -> None:
        update = _read("src/service/updateService.py")
        assert "api.github.com/repos/alexazhou/TogoSpace/" in update, "GitHub 仓库名被改了——更新检查会 404"

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

import json
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
# 无后缀但必须扫描的文本文件（Dockerfile 里有 10 处 TOGOSPACE_/togospace）。
# 漏掉它会让「反向对账」少算 10 条，属于量具本身不准 —— 已实测校正。
_SCAN_EXACT_NAMES = {"Dockerfile"}


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def _repo_text_files() -> list[Path]:
    out: list[Path] = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        # 符号链接（本仓 AGENTS.md -> CLAUDE.md）会让同一个文件被数两遍，
        # 与 `git grep` 的口径不一致 ⇒ 跳过，保证量具可比。
        if path.is_symlink():
            continue
        if any(part in _SCAN_SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in _SCAN_SUFFIXES or path.name in _SCAN_EXACT_NAMES:
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

    def test_docker_image_name_still_togospace(self) -> None:
        """本轮决定 Docker 镜像名**不改**：Docker Hub 的 alexazhou/togobot 尚不存在（404），
        而 docker.yml 同时推 docker.io + ghcr.io，Hub 不会像 GHCR 那样 push 自动建库 ⇒
        改了就首推必红。等 Operator 建好 Hub 仓库后另开一批只改这一处。

        卷名 `togospace-storage` 同理保留，否则老用户的命名卷会脱挂。
        """
        docker_yml = _read(".github/workflows/docker.yml")
        images = re.findall(r"(?:^|\s)(?:ghcr\.io/\$\{\{\s*github\.repository_owner\s*\}\}|\$\{\{\s*secrets\.DOCKERHUB_USERNAME\s*\}\})/([A-Za-z0-9._-]+)", docker_yml)
        assert images, "docker.yml 里没提取到镜像名，锚点口径失效"
        assert set(images) == {"togospace"}, f"镜像名被改了（当前应保持 togospace）: {set(images)}"

        hub_script = _read("scripts/docker_hub_delete_platform_tags.sh")
        assert "/togospace/tags/" in hub_script, "Docker Hub 清理脚本的仓库名被改了"

        dockerfile = _read("Dockerfile")
        assert "togospace-storage:/storage" in dockerfile, "Docker 卷名被改了——老用户命名卷会脱挂"

    def test_unchanged_hit_counts_not_decreased(self) -> None:
        """反向对账（逐文件）：三类「改了就断」的载体，**每个文件**的命中数不得少于改名前的基线。

        为什么必须逐文件而不是只看总量：总量守恒会掩盖局部误伤 ——
        A 文件少一条、B 文件多一条（比如把某处 `~/.togospace` 顺手改成 `~/.togobot`，
        同一份文件里又新增了一行注释），总量仍是 0 变化。只有逐文件 `>=` 才咬得住。

        基线表：``tests/unit/backend/rename_togobot_baseline.json``
        （由 BASE `58214d05` 用**同一套扫描口径**生成，`_meta` 里写了量具定义）。
        少一条即判红；若将来**合法**删除某个使用点，必须同步下调 JSON 并在提交说明里写明原因。
        """
        baseline_path = REPO_ROOT / "tests/unit/backend/rename_togobot_baseline.json"
        assert baseline_path.is_file(), f"基线表缺失: {baseline_path}"
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        assert baseline["_meta"]["base_sha"] == "58214d05d3d1f9e67a5c5051e914cb6174514fee", (
            "基线表不是从声明的 BASE 生成的，反向对账失去意义"
        )

        self_rel = "tests/unit/backend/test_rename_togobot_contract.py"
        current: dict[str, list[str]] = {}
        for path in _repo_text_files():
            rel = path.relative_to(REPO_ROOT).as_posix()
            if rel == self_rel:  # 本文件自带的断言字符串会污染计数
                continue
            current[rel] = path.read_text(encoding="utf-8", errors="ignore").splitlines()

        violations: list[str] = []
        for group, spec in baseline.items():
            if group == "_meta":
                continue
            regex = re.compile(spec["pattern"])
            for rel, floor in spec["files"].items():
                lines = current.get(rel)
                if lines is None:
                    violations.append(f"{spec['pattern']} | {rel} 文件消失（基线 {floor} 行）")
                    continue
                hits = sum(1 for line in lines if regex.search(line))
                if hits < floor:
                    violations.append(f"{spec['pattern']} | {rel} {floor} → {hits} 行")
        assert not violations, (
            "不改项命中数下降，疑似误伤（运行实例会读不到数据 / 容器 label 失配 / env 失效）：\n  "
            + "\n  ".join(violations)
        )

    def test_readme_clone_dir_matches_clone_url(self) -> None:
        """README 的源码安装步骤：`git clone <url>/X.git` 之后必须 `cd X`。

        这是被人肉评审漏掉、又被反向对账抓出来的一处真实误伤：改名批次里把
        `cd TogoSpace` 顺手改成了 `cd Togobot`，但 clone URL 指向的仓库仍叫
        `TogoSpace`（仓库名本轮不改）⇒ 用户照抄命令会 `No such file or directory`。

        判据不写死目录名，只校验「两者一致」：仓库将来真改名时这条会自动跟着变绿。
        """
        pattern = re.compile(
            r"git clone\s+(\S+?)(?:\.git)?\s*\n\s*cd\s+(\S+)",
        )
        checked = 0
        for rel in ("README.md", "README_EN.md"):
            text = _read(rel)
            for url, directory in pattern.findall(text):
                expected = url.rstrip("/").rsplit("/", 1)[-1]
                if expected.endswith(".git"):
                    expected = expected[:-4]
                assert directory == expected, (
                    f"{rel}: `git clone {url}` 得到的目录是 `{expected}`，"
                    f"但下一行写的是 `cd {directory}` ⇒ 用户照抄会失败"
                )
                checked += 1
        assert checked >= 2, f"只匹配到 {checked} 处 clone 片段，锚点口径失效"

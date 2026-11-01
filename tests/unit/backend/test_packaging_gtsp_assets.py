"""第 8 步：打包必须带上容器功能依赖的 Linux gtsp 产物（§3.2 / §10）。

**为什么这条要有单测**：容器功能在开发态读的是仓库 `assets/`，`get_gtsp_linux_binary_path()`
一定能找到文件；但在打包态 `ASSETS_DIR` 指向 `_MEIPASS/assets`，产物是否在包里
完全取决于 `scripts/togo_desktop.spec` 的 datas 与后续过滤规则。换句话说——
**开发态全绿、打包态整个功能不可用**，这是最容易漏的一类差异，而且只能靠构建才发现。

历史：`56ddaede` 加 Tauri 桌面外壳时把 `gtsp-linux-*` 加进了排除表（那时还没有容器
功能，省 12.7M 合理）；容器功能落地后这条排除就成了功能缺陷，第 8 步移除。

这里**不复制一份过滤逻辑**，而是从真实 spec 源码里读出 `EXCLUDE_PATTERNS`
再套用同一套判定（`pattern in dest_path or fnmatch(basename(src_path), pattern)`）。
复制一份判定逻辑的话，spec 改了测试也不会红——那就白测了。
"""

from __future__ import annotations

import ast
import fnmatch
import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import appPaths

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SPEC_PATH = _REPO_ROOT / "scripts" / "togo_desktop.spec"

# 容器内注入所需的产物（§3.2 只列这两种架构）
_REQUIRED_LINUX_GTSPS = ("gtsp-linux-amd64", "gtsp-linux-arm64")


def _read_exclude_patterns() -> list[str]:
    """从 spec 源码里静态解析出 `EXCLUDE_PATTERNS`（不执行 spec：它 import litellm/PyInstaller）。

    用 AST 而不是正则：注释里出现 `gtsp-linux-*` 这类字面量（本文件恰好有）时，
    正则会把注释里的文字当成真实配置，测出来的结论就是假的。
    """
    source = _SPEC_PATH.read_text(encoding="utf-8")
    for node in ast.parse(source).body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "EXCLUDE_PATTERNS":
                return list(ast.literal_eval(node.value))
    raise AssertionError("togo_desktop.spec 里找不到 EXCLUDE_PATTERNS 赋值")


def _excluded(dest_path: str, src_path: str, patterns: list[str]) -> tuple[bool, str | None]:
    """复刻 spec 的判定顺序，返回 (是否排除, 命中的模式)。

    判定式与 spec 保持一致：`pattern in dest_path or fnmatch(basename(src_path), pattern)`。
    返回命中模式便于断言失败时直接看出是哪条规则干的。
    """
    for pattern in patterns:
        if pattern in dest_path or fnmatch.fnmatch(os.path.basename(src_path), pattern):
            return True, pattern
    return False, None


def test_spec_exists_and_declares_whole_assets_dir():
    """前置：spec 的 datas 必须是整棵 `assets/`。

    产物能被带进包里靠的就是这一条——如果哪天改成逐文件列举 `assets/`，
    新加的产物就不会自动跟进，本文件后面的断言也会失去意义。
    """
    source = _SPEC_PATH.read_text(encoding="utf-8")

    assert _SPEC_PATH.is_file()
    # datas 里出现 `"assets"` 作为打包目标目录
    assert re.search(r'"assets"\s*\)', source), "datas 未声明整棵 assets/ 目录"


@pytest.mark.parametrize("binary_name", _REQUIRED_LINUX_GTSPS)
def test_spec_does_not_exclude_gtsp_linux_binaries(binary_name: str):
    """**第 8 步的核心**：Linux gtsp 产物绝不能落在排除表里。

    一旦被排除，打包态 `_MEIPASS/assets/execute/gtsp/` 下就没有这个文件，
    `get_gtsp_linux_binary_path()` 抛 FileNotFoundError，容器功能整体不可用。
    命中时直接把命中的模式打出来，避免"只报一个 False"看不出原因。
    """
    patterns = _read_exclude_patterns()

    excluded, pattern = _excluded(
        dest_path=f"assets/execute/gtsp/{binary_name}",
        src_path=os.path.join(appPaths.ASSETS_DIR, "execute", "gtsp", binary_name),
        patterns=patterns,
    )

    assert not excluded, f"{binary_name} 被 spec 的 EXCLUDE_PATTERNS 排除了（命中模式 {pattern!r}）"


def test_gtsp_runtime_logs_are_excluded_from_bundle():
    """gtsp 的历史调试日志目录绝不能进包（包卫生 + 本机路径外泄）。

    `datas` 声明的是整棵 `assets/`，而 PyInstaller **不做 .gitignore 感知**：
    `assets/execute/gtsp/logs/` 虽然被 `.gitignore` 忽略、不入库，但开发机上物理
    存在，生产构建（`tauri/scripts/build_backend.py` 从工作区主目录打）会把它原样
    打进去。实测该目录日志含本机绝对路径与用户业务文件名（如 `wechat.JPG`），
    打进交付包等于把开发机信息带给终端用户。

    ⚠️ 措辞更正（勿再写回「base64 图片数据」）：日志里
    `"base64":"iVBORw0KGgoAAAANSUhEUgAA...[base64:100620 chars]"` 的
    `[base64:N chars]` 是 **gtsp 自己打的截断占位符**，不是数据本体。实测 26 个
    日志 `[A-Za-z0-9+/]{200,}` 0 命中、`data:image/...;base64` 0 命中、`iVBOR`
    后连续 base64 字符恒 24 个。真正敏感的是路径（暴露机器）与文件名（暴露用户
    业务内容）。

    注意与 `test_spec_does_not_exclude_gtsp_linux_binaries` 的区别：排的是
    `logs` 子目录，不是 gtsp 本体。两个断言方向相反、必须同时成立。
    """
    patterns = _read_exclude_patterns()

    # 日志文件（任意层级）必须被排除
    for dest_path in (
        "assets/execute/gtsp/logs/gt-agent.log",
        "assets/execute/gtsp/logs/gt-agent-20260816-230917.502.log",
        # 未来若出现子目录也要一起排掉
        "assets/execute/gtsp/logs/archive/old.log",
    ):
        excluded, pattern = _excluded(
            dest_path=dest_path, src_path="/tmp/assets/execute/gtsp/logs/gt-agent.log",
            patterns=patterns,
        )
        assert excluded, f"{dest_path} 本应被排除（会把本机路径打进包），但没命中任何模式"

    # 同目录下的**产物**不能被误伤：排除规则是按路径子串匹配的，
    # `execute/gtsp/logs` 不该命中 `execute/gtsp/gtsp-linux-arm64`
    for binary_name in _REQUIRED_LINUX_GTSPS:
        dest_path = f"assets/execute/gtsp/{binary_name}"
        excluded, pattern = _excluded(
            dest_path=dest_path,
            src_path=os.path.join(appPaths.ASSETS_DIR, "execute", "gtsp", binary_name),
            patterns=patterns,
        )
        assert not excluded, f"{binary_name} 被 logs 排除规则误伤（命中模式 {pattern!r}）"


def test_exclude_patterns_still_filters_litellm_bloat():
    """回归护栏：排除表本身的机制没被改坏（不是把列表清空来让上面的用例变绿）。

    litellm 那几个大目录（约 29M）与 tcl/tk 必须继续被排除，否则打包体积失控。
    """
    patterns = _read_exclude_patterns()

    for dest_path in (
        "litellm/proxy/_experimental/out/asset.js",
        "litellm/proxy/guardrails/x.py",
        "litellm/proxy/swagger/index.html",
        "litellm/integrations/foo.py",
        "litellm/llms/huggingface/bar.py",
        "_tcl_data/init.tcl",
        "_tk_data/tk.tcl",
        "tcl8/8.6/init.tcl",
    ):
        excluded, _ = _excluded(dest_path, f"/tmp/site-packages/{dest_path}", patterns)
        assert excluded, f"{dest_path} 本应被排除，但没命中任何模式"


def test_bytecode_caches_and_dsstore_are_excluded_from_bundle():
    """`__pycache__` 与 `.DS_Store` 绝不能进包（包卫生 + 构建机路径外泄）。

    这是包卫生的第二层，与 `logs` 同源：spec 的 `datas` 声明整棵 `assets/`，
    PyInstaller **不读 `.gitignore`**。这两类东西被 `.gitignore` 忽略、不入库，
    但开发机上物理存在：

    - `.pyc` 的 `co_filename` 记录**构建机绝对路径**（实测 1470 个 .pyc 100% 含
      `/Volumes/PDATA/GitDB`）；顺带省约 20M。
    - `assets/` 下当前有 5 个 `.DS_Store`，会被整棵 `assets/` 的 datas 原样带进包。

    判定靠 `fnmatch(basename)` 分支天然覆盖任意层级，所以这里同时覆盖
    「多级深目录」与「assets 根下」两种位置。
    """
    patterns = _read_exclude_patterns()

    for dest_path in (
        # litellm 体积大头：site-packages 里千余个 .pyc
        "litellm/x/__pycache__/a.cpython-311.pyc",
        "litellm/proxy/__pycache__/proxy_server.cpython-311.pyc",
        # 非 litellm 的 3 个（assets 里自带脚本的缓存）
        "assets/migrate/__pycache__/0016_team_workspace_layout.cpython-311.pyc",
        "assets/skills/skill-creator/scripts/__pycache__/quick_validate.cpython-311.pyc",
        # 多级深目录也要命中
        "assets/skills/skill-creator/scripts/__pycache__/__init__.cpython-311.pyc",
        # macOS Finder 元数据：assets 根下
        "assets/.DS_Store",
        # assets 子目录下（当前 assets/icons、assets/execute、assets/execute/gtsp 都有）
        "assets/icons/.DS_Store",
        "assets/execute/gtsp/.DS_Store",
    ):
        excluded, pattern = _excluded(
            dest_path=dest_path,
            src_path=f"/tmp/pyi_build/{dest_path}",
            patterns=patterns,
        )
        assert excluded, f"{dest_path} 本应被排除（会带进构建机脏文件），但没命中任何模式"

    # 命中的必须是这两条新规则本身，而不是被别的既有规则顺带扫掉
    for dest_path in ("litellm/x/__pycache__/a.cpython-311.pyc", "assets/.DS_Store"):
        _, pattern = _excluded(dest_path, f"/tmp/{dest_path}", patterns)
        assert pattern in {"__pycache__", ".DS_Store"}, (
            f"{dest_path} 被 {pattern!r} 排掉，不是预期的 __pycache__/.DS_Store 规则"
        )


def test_bytecode_rules_do_not_swallow_python_sources_or_binaries():
    """反向：`.py` 源码与 Linux gtsp 产物不能因新规则被误排。

    `__pycache__` 按 basename 精确匹配，绝不能变成「排掉所有 .pyc」或
    「排掉路径里带 pycache 字样的源码」；`gtsp-linux-*` 是功能必需物（见文件头）。
    """
    patterns = _read_exclude_patterns()

    for dest_path in (
        "litellm/x/a.py",
        "litellm/proxy/proxy_server.py",
        "assets/migrate/0016_team_workspace_layout.py",
        "assets/skills/skill-creator/scripts/quick_validate.py",
        # 反向：文件名里带 pycache 但不叫 `__pycache__`（fs 上不会是目录，
        # 这里防的是把规则写成 `"pycache" in path` 那种宽匹配）
        "assets/migrate/pycache_notes.py",
        # 产物本体
        "assets/execute/gtsp/gtsp-linux-arm64",
        "assets/execute/gtsp/gtsp-linux-amd64",
    ):
        excluded, pattern = _excluded(
            dest_path=dest_path,
            src_path=f"/tmp/pyi_build/{dest_path}",
            patterns=patterns,
        )
        assert not excluded, f"{dest_path} 被新规则误伤（命中模式 {pattern!r}）"


def test_repo_ships_both_linux_gtsp_binaries_executable():
    """仓库里必须真的存在两份产物且可执行（§3.2：权限由资源文件保证，不做容器内 chmod）。

    缺文件时 `get_gtsp_linux_binary_path` 会在创建容器时才报错；权限位丢了则
    `docker cp` 进容器后无法执行——两者都只会在真实容器里暴露，这里提前钉住。
    """
    for binary_name in _REQUIRED_LINUX_GTSPS:
        path = Path(appPaths.ASSETS_DIR) / "execute" / "gtsp" / binary_name

        assert path.is_file(), f"缺少产物 {binary_name}"
        assert os.access(path, os.X_OK), f"{binary_name} 没有可执行权限（docker cp 会原样保留）"


def test_app_paths_resolves_both_container_architectures():
    """`get_gtsp_linux_binary_path` 对 amd64 / arm64 都要解析到真实文件（含别名）。"""
    for arch, expected_suffix in (
        ("amd64", "gtsp-linux-amd64"),
        ("x86_64", "gtsp-linux-amd64"),
        ("arm64", "gtsp-linux-arm64"),
        ("aarch64", "gtsp-linux-arm64"),
    ):
        resolved = appPaths.get_gtsp_linux_binary_path(arch)

        assert Path(resolved).is_file()
        assert Path(resolved).name == expected_suffix


def test_bundled_assets_keep_the_gtsp_subdirectory_layout():
    """打包态按 `assets/execute/gtsp/<file>` 定位，少了中间目录解析就失败。

    spec 的 datas 把 `assets/` 整棵映射成包内 `assets/`，所以包内相对路径必须与
    仓库一致；这条断言的是仓库侧的布局契约（`get_gtsp_linux_binary_path` 硬编码了它）。
    """
    gtsp_dir = Path(appPaths.ASSETS_DIR) / "execute" / "gtsp"

    assert gtsp_dir.is_dir()
    assert gtsp_dir.parent.name == "execute"
    assert gtsp_dir.parent.parent == Path(appPaths.ASSETS_DIR)


# ---------------------------------------------------------------------------
# 打包态的资源目录解析（`appPaths._resolve_assets_dir`）
# ---------------------------------------------------------------------------


@pytest.fixture
def frozen_paths(monkeypatch, tmp_path):
    """把 appPaths 切到"打包态"，并给一个可控的假 exe 目录。"""
    exe_dir = tmp_path / "TogoSpace-backend"
    exe_dir.mkdir()
    exe_path = exe_dir / "TogoSpace-backend"
    exe_path.write_text("", encoding="utf-8")

    meipass = tmp_path / "_internal"
    meipass.mkdir()

    monkeypatch.setattr(appPaths, "_IS_FROZEN", True)
    monkeypatch.setattr(appPaths, "_MEIPASS", str(meipass))
    monkeypatch.setattr(appPaths.sys, "executable", str(exe_path))

    return SimpleNamespace(exe_dir=exe_dir, meipass=meipass)


def test_frozen_assets_prefers_meipass(frozen_paths):
    """首选 `_MEIPASS/assets`：PyInstaller 6 onedir 的 datas 就落在这里。"""
    target = frozen_paths.meipass / "assets"
    target.mkdir()

    assert appPaths._resolve_assets_dir() == str(target)


def test_frozen_assets_falls_back_to_exe_dir(frozen_paths):
    """部分打包形态把资源放在可执行文件同级：`_MEIPASS` 没有时要用它。"""
    exe_assets = frozen_paths.exe_dir / "assets"
    exe_assets.mkdir()

    assert appPaths._resolve_assets_dir() == str(exe_assets)


def test_frozen_assets_falls_back_to_underscore_internal(frozen_paths):
    """再退一步：`<exe>/_internal/assets`（`sys._MEIPASS` 取不到时的兜底）。"""
    internal_assets = frozen_paths.exe_dir / "_internal" / "assets"
    internal_assets.mkdir(parents=True)

    assert appPaths._resolve_assets_dir() == str(internal_assets)


def test_frozen_assets_prefers_meipass_over_exe_dir(frozen_paths):
    """两处都存在时以 `_MEIPASS` 为准（优先级不能乱：实测主形态走它）。"""
    meipass_assets = frozen_paths.meipass / "assets"
    meipass_assets.mkdir()
    (frozen_paths.exe_dir / "assets").mkdir()

    assert appPaths._resolve_assets_dir() == str(meipass_assets)


def test_frozen_assets_returns_first_candidate_when_none_exist(frozen_paths):
    """一个都没命中时返回首选项而不是抛错：让后续的"文件缺失"错误带上真实查找路径。"""
    expected = os.path.join(str(frozen_paths.meipass), "assets")

    assert appPaths._resolve_assets_dir() == expected


def test_dev_mode_assets_points_at_repo_assets():
    """开发态必须指向仓库 `assets/`（打包态与开发态的分歧就从这里开始）。"""
    if getattr(appPaths.sys, "frozen", False):  # pragma: no cover - 测试进程不会 frozen
        pytest.skip("测试进程处于 frozen 形态，无法验证开发态分支")

    assert appPaths._resolve_assets_dir() == os.path.abspath(
        os.path.join(appPaths._ROOT, "assets")
    )

#!/usr/bin/env python3
"""CI 内验证 Togobot Windows 产物（#243）。

Operator 的要求是「构建与验证都在 CI 上完成，不许 build 绿就交付」。本脚本就是那个验证层，
四个判据各自独立，任何一条不过即非零退出：

  A bundle          安装包存在 + 体积落在给定区间 + sha256（区间依据写在 workflow 注释/报告里）
  B backend-smoke   后端可执行文件在 windows runner 上真跑：
      B1 空跑（不带桌面环境变量）⇒ exit 2 且 stdout 含 `[TOGO] ERROR code=1`
         证明：PyInstaller 包在 Windows 上真能 boot（backend_main / configUtil 全链路导入成功），
         且入口是 exe 自身而不是 python 源码形态；这条同时排掉「进程挂住不动」的假绿（有超时）。
      B2 真启动 + 真 HTTP：
         读到 `[TOGO] READY url=…`（证明迁移完成、服务真监听）→
         GET /system/status.json = 200（免鉴权端点）→
         GET /agents/list.json 无 token = 401 auth_required → 带 Bearer = 200 →
         关闭 stdin ⇒ 进程 60s 内自行退出且 exit code 0（Windows 上 stdin EOF 停机机制）。
  C installed       静默安装后的安装树结构检查 + 在**安装后的**树上复跑 B1/B2
                    （只验构建目录等于没验用户真正拿到的东西）

设计约束：只用标准库、只读不写、任何一步失败都打印实收再退出。
产物里的 gtsp 资产是 LFS 文件，若 LFS 没拉下来会是约 130 字节的指针文件 —— 这里用体积下限
挡掉「指针冒充二进制」的假绿（指针文件也能"存在"）。
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

MARKER_ERROR_ENV = "[TOGO] ERROR code=1"
MARKER_READY = "[TOGO] READY "
MARKER_MIGRATION_END = "[TOGO] MIGRATION_END"
# 与 src/db.py 的 MIGRATION_NAME_PATTERN 逐字一致（迁移文件既可能是 .sql 也可能是 .py）
_MIGRATION_NAME_PATTERN = re.compile(r"^\d{4}.*\.(sql|py)$")

# 安装树里必须存在的相对路径（相对安装根目录）。依据：
#   backend/            ← tauri.conf.json bundle.resources {"../build/backend": "backend"}
#   backend/Togobot-backend.exe ← src/sys/windows.rs get_command() 拼的 sidecar 路径
#   _internal/assets/   ← PyInstaller onedir 的 datas（scripts/togo_desktop.spec）
#   _internal/assets/execute/gtsp/gtsp-windows-amd64.exe ← 容器沙箱功能依赖（assets 整棵打包）
#   _internal/assets/frontend/{index,splash}.html ← build_desktop.py 的 frontend 动作同步进来的前端产物
#
# 判据分两段：字节下限（挡空文件/截断/指针）+ 内容子串（挡「文件在、内容是错的」）。
# 内容子串取自仓库里可指认的源文件，不是猜的：
#   frontend/index.html 源文件 297 字节，引用 `/src/main.ts`；`npm run build` 后 vite 把它
#   重写成引用 `/assets/main-<hash>.js`（本地 mac 实测 370 字节；CI 实收 382 字节，hash 不同长度也不同）。
#   所以 index.html 的正确形态是「短 + 含 /assets/ + 不含 /src/main.ts」，
#   用「≥1000 字节」这种跟真实体量无关的下限只会假红（run 43622465816 就是这么红的）。
#   splash.html 源文件 3669 字节、构建不改写（直接拷贝，本地实测 3681 字节）。
REQUIRED_INSTALLED = [
    # (相对路径, 字节下限, 必须包含, 不得包含, 失败形态)
    ("Togobot.exe", 1_000_000, (), (), "Tauri 外壳；缺失/过小 ⇒ 主程序没打进 bundle"),
    ("backend/Togobot-backend.exe", 1_000_000, (), (), "sidecar 后端；缺失/过小 ⇒ resources 没打进去"),
    (
        "backend/_internal/assets/execute/gtsp/gtsp-windows-amd64.exe",
        1_000_000,
        (),
        (),
        "容器沙箱依赖的 gtsp；只剩百来字节 ⇒ git-lfs 指针没被 pull 下来",
    ),
    (
        "backend/_internal/assets/frontend/index.html",
        200,
        ("<script", "/assets/"),
        ("/src/main.ts",),
        "前端入口；缺 /assets/ 或含 /src/main.ts ⇒ 装进去的是未构建的源文件（本地能跑、装完白屏）",
    ),
    (
        "backend/_internal/assets/frontend/splash.html",
        1_000,
        ("<html",),
        (),
        "启动画面；过小 ⇒ 打包漏资产",
    ),
]

_failures: list[str] = []


def ok(message: str) -> None:
    print(f"  ✅ {message}")


def fail(message: str) -> None:
    print(f"  ❌ {message}")
    _failures.append(message)


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ── A: 安装包 ────────────────────────────────────────────────────────────────

def check_bundle(path: str, min_mb: float, max_mb: float) -> None:
    path = os.path.abspath(path)
    print(f"--- A bundle: {path}")
    if not os.path.isfile(path):
        fail(f"安装包不存在: {path}")
        return
    size = os.path.getsize(path)
    mb = size / (1024 * 1024)
    ok(f"存在，{size} 字节（{mb:.1f} MB），sha256={_sha256(path)}")
    if mb < min_mb or mb > max_mb:
        fail(f"体积 {mb:.1f} MB 不在区间 [{min_mb}, {max_mb}] MB 内（区间依据见 workflow 注释）")
    else:
        ok(f"体积在区间 [{min_mb}, {max_mb}] MB 内")


# ── B1: 后端空跑 ─────────────────────────────────────────────────────────────

def _spawn_and_capture(exe: str, env: dict[str, str]) -> tuple[int, str, str] | None:
    try:
        result = subprocess.run(
            [exe], capture_output=True, timeout=300, env=env, cwd=os.path.dirname(exe),
        )
    except subprocess.TimeoutExpired:
        fail("B1 超时：后端空跑 300s 未退出（说明它没走参数校验分支，可能挂住了）")
        return None
    except OSError as error:  # 起不来（缺 VC 运行时 / 权限 / 架构不符）也要算实收，不裸抛 traceback
        fail(f"B1 无法启动进程: {type(error).__name__}: {error}")
        return None
    out = (result.stdout or b"").decode("utf-8", "replace")
    err = (result.stderr or b"").decode("utf-8", "replace")
    return result.returncode, out, err


def judge_error_contract(returncode: int, out: str, err: str, *, expect_message: str = "必须为") -> list[str]:
    """B1 严格判据：错误路径的契约（#245 恢复的版本）。返回失败原因列表，空 = 通过。

    契约来源 src/desktopEntry.py 的 main()：
      `except DesktopParamError: emit_error(code, message); return 2`
    ⇒ 缺 `TOGOSPACE_DESKTOP` 时必须是：stdout 打出单行 `[TOGO] ERROR code=1 message=<原因>`，
      进程 exit **2**。

    为什么还要判「message 可读」而不只是「marker 出现」：编码兜底 `errors="backslashreplace"`
    会让 marker 以 `\\u5fc5\\u987b…` 的转义形态照样出现在 stdout —— 外壳拿到了行、用户看到的却是转义。
    PM 要的是「`message=…` 真打出来」，所以这里额外禁止转义形态；否则修没修都绿，等于没人守
    （#245 的变异自证就是钉这一条：把编码修复去掉，本判据必须变红）。
    """
    failures: list[str] = []
    stdout_lines = [line for line in out.splitlines() if line.strip()]

    marker_lines = [line for line in stdout_lines if line.startswith(MARKER_ERROR_ENV + " message=")]
    if not marker_lines:
        others = [line for line in stdout_lines if line.startswith(MARKER_ERROR_ENV)]
        if others:
            failures.append(
                f"stdout 有 ERROR marker 但缺 ` message=` 字段：{others[0][:200]!r}（外壳拿不到可读原因）"
            )
        else:
            failures.append(
                f"stdout 里没有 `{MARKER_ERROR_ENV} message=…`（stdout 实收 {out.strip()[:300]!r}）"
            )
    else:
        message = marker_lines[0].split("message=", 1)[1]
        if "\\u" in message or "\\x" in message:
            failures.append(
                f"message 是转义形态而非可读文本：{message[:160]!r} ⇒ 编码兜底在干活，用户看不懂真实原因"
            )
        elif expect_message and expect_message not in message:
            failures.append(f"message 里没有预期原因片段 {expect_message!r}：{message[:160]!r}")

    if returncode != 2:
        failures.append(
            f"退出码 = {returncode}，期望 2（src/desktopEntry.py 错误路径 `return 2`；"
            f"1 通常意味着异常处理器自己又崩了，例如 UnicodeEncodeError 顶掉了 return）"
        )

    if "UnicodeEncodeError" in err or "UnicodeEncodeError" in out:
        failures.append("输出里出现 UnicodeEncodeError ⇒ 编码兜底没生效")

    missing = [token for token in ("ModuleNotFoundError", "ImportError", "No module named") if token in out + err]
    if missing:
        failures.append(f"输出里出现 {missing} ⇒ 打包缺依赖，不是能交付的产物")

    return failures


def _run_no_desktop_env(exe: str) -> None:
    """B1（严格版，判据见 judge_error_contract）：空跑 ⇒ 必须 `exit 2` + 可读 `[TOGO] ERROR code=1 message=…`。

    历史：本判据在 #243 期间被临时放宽成「进到 DesktopParamError 分支 / 无缺依赖 / 非 0」，
    因为 Windows 上错误路径 100% 撞 cp1252（`UnicodeEncodeError` → exit 1）。#245 修好后恢复严格，
    并由 `marker-contract` 子命令做变异自证，防止下次坏回去没人红。
    """
    exe = os.path.abspath(exe)
    print(f"--- B1 backend 空跑（无桌面环境变量）: {exe}")
    if not os.path.isfile(exe):
        fail(f"后端可执行文件不存在: {exe}")
        return
    if not os.access(exe, os.X_OK):
        fail(f"后端可执行文件没有可执行权限: {exe}")
        return

    env = dict(os.environ)
    for key in ("TOGOSPACE_DESKTOP", "TOGOSPACE_DESKTOP_PORT", "TOGOSPACE_DESKTOP_TOKEN"):
        env.pop(key, None)

    captured = _spawn_and_capture(exe, env)
    if captured is None:
        return
    code, out, err = captured
    print(f"    真实环境（无 PYTHONIOENCODING）exit code = {code}")
    print(f"    stdout = {out.strip()[:500]!r}")
    print(f"    stderr = {err.strip()[:2000]!r}")

    failures = judge_error_contract(code, out, err)
    if failures:
        for reason in failures:
            fail(f"错误路径契约不成立：{reason}")
        return
    ok(f"收到 `{MARKER_ERROR_ENV} message=…`（可读、非转义）⇒ 外壳能拿到真实错误原因")
    ok(f"退出码 = {code} ⇒ 错误路径的本义（`return 2`），没被编码异常顶成 1")
    ok("未见 ModuleNotFoundError / ImportError / No module named ⇒ 无缺依赖")


# ── B2: 真启动 + 真 HTTP + stdin EOF 停机 ────────────────────────────────────

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _http(url: str, token: str | None = None, timeout: float = 15.0) -> tuple[int, str]:
    request = urllib.request.Request(url)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(400).decode("utf-8", "replace")
    except urllib.error.HTTPError as error:  # 401 之类：状态码有意义，不当异常
        return error.code, error.read(400).decode("utf-8", "replace")
    except Exception as error:  # noqa: BLE001 — 连接失败属于实收的一部分
        return -1, f"{type(error).__name__}: {error}"


# ── 迁移记账（MIGRATION_END 缺失时的正向对照） ───────────────────────────────
#
# `[TOGO] MIGRATION_END` 只在「确有未记账迁移」时才 emit：src/backend_main.py 的
# `_run_database_migrations` 里是 `if has_pending and ... on_migration_end` 才回调。
# 所以一个已经迁移过的库再启动一次，本来就不会有这条 marker —— 把它当硬判据必假红
# （run 43622465816 的 C 层：B 层刚在同一个 user profile 上把库建好并迁移完）。
# 但「没收到」也不能直接放过（迁移彻底坏掉也会是绿的），得给正向对照：
# 去 sqlite 里数 `_migrations` 表的记账条数，和 assets/migrate 下的迁移文件数对上。


def _db_candidates() -> list[str]:
    """按 src/appPaths.py + configTypes.py 的优先级给出库路径候选，去重保序。"""
    candidates: list[str] = []
    override = (os.environ.get("TEAMAGENT_DB_PATH") or "").strip()
    if override:
        candidates.append(override)
    home_store = os.path.expanduser("~/.togospace")
    storage_root = (os.environ.get("STORAGE_ROOT") or "").strip() or home_store
    candidates.append(os.path.join(storage_root, "data", "data.db"))
    # 兜底：冻结体的 STORAGE_ROOT 若被指到别处，路径猜错不该变成假红，
    # 在 ~/.togospace 下补两个常见位置（仍是同一个判据，只是先找到文件）。
    candidates.extend(sorted(glob.glob(os.path.join(home_store, "*", "data", "data.db"))))
    candidates.append(os.path.join(home_store, "data.db"))
    seen: set[str] = set()
    unique: list[str] = []
    for candidate in candidates:
        normalized = os.path.normcase(os.path.abspath(candidate))
        if normalized not in seen:
            seen.add(normalized)
            unique.append(candidate)
    return unique


def _migration_files(migrations_dir: str) -> list[str]:
    """复刻 src/db.py 的 `MIGRATION_NAME_PATTERN = ^\\d{4}.*\\.(sql|py)$`。

    只数 `*.sql` 会漏 —— 迁移里确实存在一个 `.py`（0016_team_workspace_layout.py），
    数出来 20 而库里有 21 条记账，就成了「记账不全」的假红（run 43624163220）。
    """
    if not os.path.isdir(migrations_dir):
        return []
    return sorted(
        name
        for name in os.listdir(migrations_dir)
        if os.path.isfile(os.path.join(migrations_dir, name))
        and _MIGRATION_NAME_PATTERN.match(name)
    )


def _migration_ledger(db_path: str, migrations_dir: str) -> tuple[int, int, str]:
    """返回 (已记账条数, 迁移文件数, 错误说明)；读失败时条数为 -1。"""
    if not os.path.isfile(db_path):
        return -1, -1, f"库文件不存在: {db_path}"
    files = len(_migration_files(migrations_dir))
    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            row = connection.execute("SELECT COUNT(*) FROM _migrations").fetchone()
        finally:
            connection.close()
    except sqlite3.Error as error:
        return -1, files, f"读 {db_path} 的 _migrations 失败: {error}"
    return int(row[0]), files, ""


def _report_migration_evidence(migrations_dir: str) -> None:
    """MIGRATION_END 缺失时的替代判据：迁移记账已满 ⇒ 迁移确实在 Windows 上跑过。"""
    candidates = _db_candidates()
    existing = [path for path in candidates if os.path.isfile(path)]
    print(f"    库候选: {candidates}")
    if not existing:
        fail(f"未收到 {MARKER_MIGRATION_END!r}，且{len(candidates)} 个候选路径下都没有库文件 ⇒ 无法证明迁移跑过")
        return
    db_path = existing[0]
    applied, files, error = _migration_ledger(db_path, migrations_dir)
    print(f"    迁移记账: 已记账={applied} 迁移文件={files} 库={db_path}")
    if error:
        fail(f"未收到 {MARKER_MIGRATION_END!r}，且查不到迁移记账（{error}）⇒ 无法证明迁移跑过")
    elif files == 0:
        fail(f"未收到 {MARKER_MIGRATION_END!r}，且 {migrations_dir} 下没有 .sql 文件 ⇒ 无对照依据")
    elif applied == files:
        ok(
            f"未收到 {MARKER_MIGRATION_END!r} 属预期（marker 只在有 pending 迁移时才打）："
            f"_migrations 记账 {applied}/{files} 条与迁移文件数一致 ⇒ 迁移确已执行过"
        )
    else:
        fail(f"未收到 {MARKER_MIGRATION_END!r} 且记账不全：{applied}/{files} ⇒ 迁移没跑完")


def _run_desktop_startup(
    exe: str,
    label: str,
    *,
    require_migration_end: bool = True,
    migrations_dir: str = "",
) -> None:
    exe = os.path.abspath(exe)
    print(f"--- B2 backend 真启动（{label}）: {exe}")
    if not os.path.isfile(exe):
        fail(f"B2 后端可执行文件不存在: {exe}")
        return
    port = _free_port()
    token = "ci-verify-token-243"
    env = dict(os.environ)
    env.update({
        "TOGOSPACE_DESKTOP": "1",
        "TOGOSPACE_DESKTOP_PORT": str(port),
        "TOGOSPACE_DESKTOP_TOKEN": token,
    })
    print(f"    端口={port}（127.0.0.1，来自 src/desktopEntry.py 的强制 bind）")
    process = subprocess.Popen(
        [exe], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        env=env, cwd=os.path.dirname(exe), bufsize=0,
    )
    lines: list[str] = []
    ready = threading.Event()
    stop = threading.Event()

    def reader() -> None:
        assert process.stdout is not None
        buffer = b""
        while not stop.is_set():
            chunk = process.stdout.read(1)
            if not chunk:
                break
            if chunk == b"\n":
                text = buffer.decode("utf-8", "replace").rstrip()
                buffer = b""
                if text:
                    lines.append(text)
                    print(f"    | {text[:300]}")
                    if text.startswith(MARKER_READY):
                        ready.set()
            else:
                buffer += chunk
                if len(buffer) > 8192:  # 超长无换行输出：截断防止内存膨胀
                    lines.append(buffer.decode("utf-8", "replace")[:8192])
                    buffer = b""

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    try:
        if not ready.wait(timeout=420):
            fail(f"B2 未在 420s 内收到 {MARKER_READY!r}（已收到 {len(lines)} 行输出，见上方实收）")
            return
        ok(f"收到 {MARKER_READY!r} ⇒ 迁移完成且服务已在 Windows 上监听")
        if any(line.startswith(MARKER_MIGRATION_END) for line in lines):
            ok(f"收到 {MARKER_MIGRATION_END!r} ⇒ sqlite 迁移在 Windows 上真跑过")
        elif require_migration_end:
            fail(f"未收到 {MARKER_MIGRATION_END!r}（首次启动、库是空的，这条必须有）")
        else:
            _report_migration_evidence(migrations_dir)

        base = f"http://127.0.0.1:{port}"
        status, body = _http(f"{base}/system/status.json")
        print(f"    GET /system/status.json          → {status} {body[:120]!r}")
        if status == 200:
            ok("免鉴权端点 200（服务真能应答 HTTP）")
        else:
            fail(f"/system/status.json 期望 200，实收 {status}")

        status, body = _http(f"{base}/agents/list.json")
        print(f"    GET /agents/list.json (no token) → {status} {body[:120]!r}")
        if status == 401 and "auth_required" in body:
            ok("无 token 401 auth_required（桌面模式强制鉴权生效）")
        else:
            fail(f"/agents/list.json 无 token 期望 401 auth_required，实收 {status} {body[:80]!r}")

        status, body = _http(f"{base}/agents/list.json", token=token)
        print(f"    GET /agents/list.json (bearer)   → {status} {body[:120]!r}")
        if status == 200:
            ok("带 Bearer token 200（token 与桌面参数一致）")
        else:
            fail(f"/agents/list.json 带 token 期望 200，实收 {status}")

        # stdin EOF 停机：关掉管道后后端应自行收尾退出，这是跨平台不留孤儿的机制
        process.stdin.close()
        try:
            code = process.wait(timeout=90)
        except subprocess.TimeoutExpired:
            fail("B2 关闭 stdin 后 90s 未退出（stdin EOF 停机机制在 Windows 上失效）")
            return
        print(f"    关闭 stdin 后 exit code = {code}")
        if code == 0:
            ok("关闭 stdin 后优雅退出（exit 0）")
        else:
            fail(f"关闭 stdin 后 exit code = {code}，期望 0")
    finally:
        stop.set()
        if process.poll() is None:
            print("    ⚠️ 进程仍在运行，强制结束（不影响上面已记录的判据）")
            process.kill()
            process.wait(timeout=30)


# ── C: 安装树 ────────────────────────────────────────────────────────────────

def check_installed(root: str, smoke_exe: str | None) -> None:
    root = os.path.abspath(root)
    print(f"--- C installed: {root}")
    if not os.path.isdir(root):
        fail(f"安装目录不存在: {root}")
        return
    for relative, min_bytes, must_contain, must_not_contain, failure_form in REQUIRED_INSTALLED:
        path = os.path.join(root, *relative.split("/"))
        if not os.path.isfile(path):
            fail(f"安装树缺少 {relative}（{failure_form}）")
            continue
        size = os.path.getsize(path)
        if size < min_bytes:
            fail(f"{relative} 只有 {size} 字节（下限 {min_bytes}）：{failure_form}")
            continue
        with open(path, "rb") as handle:
            text = handle.read(2 * 1024 * 1024).decode("utf-8", "replace")
        missing = [needle for needle in must_contain if needle not in text]
        forbidden = [needle for needle in must_not_contain if needle in text]
        if missing or forbidden:
            fail(
                f"{relative} 内容不符（{size} 字节）：缺 {missing}、含不应出现 {forbidden}；"
                f"{failure_form}"
            )
        else:
            ok(f"{relative} 存在，{size} 字节，内容判据通过")

    internal = os.path.join(root, "backend", "_internal")
    if os.path.isdir(internal):
        entries = sorted(os.listdir(internal))
        ok(f"backend/_internal 条目数 = {len(entries)}（前 8 个：{entries[:8]}）")
    else:
        fail("安装树缺少 backend/_internal（PyInstaller onedir 没打进去）")

    if smoke_exe:
        _run_no_desktop_env(smoke_exe)
        # 这里跑的是同一个 user profile 上的第二次启动：迁移已在 B 层做完，
        # MIGRATION_END 不会再打（只在有 pending 时打），改用迁移记账做正向对照。
        _run_desktop_startup(
            smoke_exe,
            "安装后的树",
            require_migration_end=False,
            migrations_dir=os.path.join(internal, "assets", "migrate"),
        )


# ── marker 契约变异自证（#245） ──────────────────────────────────────────────
#
# PM 要求「加一条断言钉住 marker 能输出且退出码为 2，并对该断言做变异自证：
# 把编码修复去掉 ⇒ 该断言必红」。做法：在源码形态上跑同一份判据（judge_error_contract），
# 一次用真源码，一次用「去掉修复调用」的变异体，且都用 PYTHONIOENCODING=cp1252
# 复刻 Windows 冻结体的 stdout 编码 —— 不依赖 Windows 特有 API，darwin / windows 都能跑。

FIX_CALL_SITE = "_configure_output_encoding()  # 输出编码：必须先于任何 marker"
MUTANT_CALL_SITE = "pass  # [mutant] 去掉了输出编码修复（变异自证用）"


def _run_entry_script(python_exe: str, script: str, repo_root: str) -> tuple[int, str, str]:
    """用 cp1252 的 stdout 跑 desktopEntry 源码，返回 (exit, stdout, stderr)。"""
    env = dict(os.environ)
    for key in ("TOGOSPACE_DESKTOP", "TOGOSPACE_DESKTOP_PORT", "TOGOSPACE_DESKTOP_TOKEN"):
        env.pop(key, None)
    env["PYTHONPATH"] = os.path.join(repo_root, "src")
    env["PYTHONUTF8"] = "0"
    env["PYTHONIOENCODING"] = "cp1252"
    try:
        result = subprocess.run(
            [python_exe, script], capture_output=True, timeout=300, env=env, cwd=repo_root,
        )
    except (subprocess.TimeoutExpired, OSError) as error:
        return -1, "", f"{type(error).__name__}: {error}"
    return (
        result.returncode,
        (result.stdout or b"").decode("utf-8", "replace"),
        (result.stderr or b"").decode("utf-8", "replace"),
    )


def check_marker_contract(repo_root: str, python_exe: str) -> None:
    """修复在场 ⇒ 判据绿；把修复变异掉 ⇒ 同一判据必须红（否则判据没牙）。"""
    script = os.path.join(repo_root, "src", "desktopEntry.py")
    print(f"--- marker 契约自证（PYTHONIOENCODING=cp1252 模拟 Windows stdout）: {script}")
    if not os.path.isfile(script):
        fail(f"找不到 {script}")
        return
    with open(script, encoding="utf-8") as handle:
        source = handle.read()
    if FIX_CALL_SITE not in source:
        fail(f"源码里找不到修复调用点（{FIX_CALL_SITE!r}）⇒ 修复不在场，自证无意义")
        return

    code, out, err = _run_entry_script(python_exe, script, repo_root)
    print(f"    修复在场: exit={code} stdout={out.strip()[:200]!r}")
    failures = judge_error_contract(code, out, err)
    if failures:
        for reason in failures:
            fail(f"修复在场但契约不成立：{reason}")
    else:
        ok("修复在场 ⇒ cp1252 下 marker 可读、exit 2")

    with tempfile.TemporaryDirectory() as tmp:
        mutant_path = os.path.join(tmp, "desktopEntry_mutant.py")
        with open(mutant_path, "w", encoding="utf-8") as handle:
            handle.write(source.replace(FIX_CALL_SITE, MUTANT_CALL_SITE, 1))
        code, out, err = _run_entry_script(python_exe, mutant_path, repo_root)
    print(f"    变异体（去掉修复）: exit={code} stdout={out.strip()[:200]!r}")
    mutant_failures = judge_error_contract(code, out, err)
    if mutant_failures:
        ok(f"变异体被同一判据判红（{len(mutant_failures)} 条）⇒ 判据有牙，下次坏回去会红")
        for reason in mutant_failures:
            print(f"       - {reason[:180]}")
    else:
        fail("变异体（去掉编码修复）竟然也过 ⇒ 判据没牙，修没修都绿")


def main() -> int:
    parser = argparse.ArgumentParser(description="验证 Togobot Windows 产物（#243）")
    sub = parser.add_subparsers(dest="command", required=True)

    bundle = sub.add_parser("bundle", help="校验安装包存在/体积/sha256")
    bundle.add_argument("--path", required=True)
    bundle.add_argument("--min-mb", type=float, default=120.0)
    bundle.add_argument("--max-mb", type=float, default=700.0)

    smoke = sub.add_parser("backend-smoke", help="后端可执行文件真跑（B1+B2）")
    smoke.add_argument("--exe", required=True)
    smoke.add_argument("--skip-startup", action="store_true", help="只跑 B1")

    contract = sub.add_parser("marker-contract", help="错误 marker 契约 + 变异自证（源码形态）")
    contract.add_argument("--repo-root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    contract.add_argument("--python", default=sys.executable)

    installed = sub.add_parser("installed", help="安装树结构检查（+ 可选在安装树上复跑后端）")
    installed.add_argument("--dir", required=True)
    installed.add_argument("--smoke-exe", default=None)

    args = parser.parse_args()
    if args.command == "bundle":
        check_bundle(args.path, args.min_mb, args.max_mb)
    elif args.command == "backend-smoke":
        _run_no_desktop_env(args.exe)
        if not args.skip_startup:
            _run_desktop_startup(args.exe, "构建目录")
    elif args.command == "marker-contract":
        check_marker_contract(os.path.abspath(args.repo_root), args.python)
    elif args.command == "installed":
        check_installed(args.dir, args.smoke_exe)

    print()
    if _failures:
        print(f"RESULT: FAIL（{len(_failures)} 条判据未通过）")
        for message in _failures:
            print(f"  - {message}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

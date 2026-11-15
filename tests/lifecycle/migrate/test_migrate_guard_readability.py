"""迁移守卫的**可读性**判据（#274：N-2 改名提示 / N-3 监听日志时序 / N-4 CLI 退出码）。

背景：#270 的守卫判定逻辑已 DONE 并被 38 验收，本文件只钉「错误信息与日志」三件事，
**不碰判定逻辑**（仍按迁移名比对，`applied - code` 差集）。

1. **N-2**：迁移被**改名**（非新增）时会被判「库超前」。信息里必须出现「改名」分支与
   可排查线索（代码侧清单 + 同版本号前缀的候选文件），否则人只会照着「请升级代码」
   白跑一趟。同时钉住「判定不变」：改名场景仍然抛 `DatabaseAheadOfCodeError`。
2. **N-3**：拒绝启动时输出里**不得**出现任何「监听 / 已监听 / 已就绪」字样，
   否则 `grep 监听地址` 的人会误判成「已经起来了」。
3. **N-4**：`python src/db.py migrate` 撞守卫时不得打裸 traceback，退出码与后端启动
   路径统一为 2，并输出同一份可读错误。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import db
from util import configUtil

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_DIR = REPO_ROOT / "src"
MIGRATIONS_DIR = REPO_ROOT / "assets/migrate"


# ── 现场构造 ────────────────────────────────────────────────────────────────


def _fully_migrated_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "data" / "data.db"
    db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR, verbose=False)
    return db_path


def _ledger_names(db_path: Path) -> list[str]:
    import sqlite3

    with sqlite3.connect(str(db_path)) as conn:
        return sorted(r[0] for r in conn.execute("SELECT name FROM _migrations"))


def _config_dir(tmp_path: Path, db_path: Path, port: int = 0) -> Path:
    """造一个指向指定库的 config 目录（setting.json 由模板生成后改 db_path）。"""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    configUtil.load(str(cfg_dir))  # 从模板生成 setting.json / setting.README.md
    setting_path = cfg_dir / "setting.json"
    setting = json.loads(setting_path.read_text(encoding="utf-8"))
    setting["db_path"] = str(db_path)
    setting["workspace_root"] = str(tmp_path / "workspace")
    setting["bind_port"] = port
    setting_path.write_text(json.dumps(setting, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg_dir


def _subprocess_env(tmp_path: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "TOGOSPACE_DESKTOP"}
    env["PYTHONPATH"] = os.pathsep.join([str(SRC_DIR), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    env["STORAGE_ROOT"] = str(tmp_path / "storage")
    return env


def _run(args: list[str], tmp_path: Path, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        cwd=str(REPO_ROOT),
        env=_subprocess_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _collect_until(
    proc: "subprocess.Popen[str]",
    needles: str | tuple[str, ...],
    timeout: float = 120.0,
) -> list[str]:
    """把进程输出逐行收下来，直到出现任一 `needles`、进程退出或超时（超时也返回已收行）。

    `needles` 给多个是为了**快速失败**：等待「已监听」的同时也等「阶段 4/4 完成」，
    后者是 bind 前最后一条日志，日志被删掉的假修会在这里就停下，不必空等超时。
    """
    targets = (needles,) if isinstance(needles, str) else needles
    import threading
    import time

    lines: list[str] = []

    def _reader() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.append(line)

    thread = threading.Thread(target=_reader, daemon=True)
    thread.start()
    deadline = time.time() + timeout
    while time.time() < deadline:
        if any(t in line for line in lines for t in targets):
            break
        if proc.poll() is not None:
            break
        time.sleep(0.2)
    return lines


def _stop(proc: "subprocess.Popen[str]") -> None:
    if proc.poll() is None:
        proc.terminate()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:  # pragma: no cover - 兜底
        proc.kill()
        proc.wait(timeout=10)


# ── N-2：改名误判的提示 ─────────────────────────────────────────────────────


def test_rename_hint_present_and_verdict_unchanged(tmp_path: Path) -> None:
    """迁移被改名：仍然拒绝（判定不变），且信息要指认「改名」这个分支。"""
    db_path = _fully_migrated_db(tmp_path)
    ledger_before = _ledger_names(db_path)

    renamed_dir = tmp_path / "renamed_migrations"
    renamed_dir.mkdir()
    for migration in MIGRATIONS_DIR.iterdir():
        if not migration.is_file():
            continue
        target = renamed_dir / migration.name
        if migration.name == "0022_add_agent_task_stages.sql":
            target = renamed_dir / "0022_add_agent_task_stages_v2.sql"  # 只改名，不新增
        shutil.copy(migration, target)

    with pytest.raises(db.DatabaseAheadOfCodeError) as excinfo:
        db.migrate_database(db_path, migrations_dir=renamed_dir)

    message = str(excinfo.value)
    # 判定没变：账本里的旧名字被判缺失，库未被改动
    assert excinfo.value.missing == ["0022_add_agent_task_stages.sql"]
    assert _ledger_names(db_path) == ledger_before

    # 信息层：① 要给「改名」这条分支，② 要给同版本号前缀的候选文件
    assert "改名" in message, f"错误信息缺少「改名」分支提示：{message}"
    assert "0022_add_agent_task_stages_v2.sql" in message, (
        f"错误信息没指认同版本号前缀的代码侧文件（改名排查的最关键线索）：{message}"
    )
    # ③ 原有的「升级代码」与 STORAGE_ROOT 自救提示不能丢
    assert "升级" in message and "STORAGE_ROOT" in message


def test_rename_to_unparsed_extension_is_flagged_as_rename(tmp_path: Path) -> None:
    """改名成**不合规后缀**（.txt/.bak）后不再算迁移，仍要能指认「疑似改名」。

    这是比 `_v2.sql` 更常见的形态（`.sql` → `.sql.bak`、误加 `.txt`）：文件还在目录里，
    但它已经不参与判据了。只看「已解析的迁移名」会误报成「代码侧没有这个版本号」。
    """
    db_path = _fully_migrated_db(tmp_path)
    renamed_dir = tmp_path / "renamed_migrations"
    renamed_dir.mkdir()
    for migration in MIGRATIONS_DIR.iterdir():
        if not migration.is_file():
            continue
        target = renamed_dir / migration.name
        if migration.name == "0022_add_agent_task_stages.sql":
            target = renamed_dir / "0022_add_agent_task_stages.sql.bak"
        shutil.copy(migration, target)

    with pytest.raises(db.DatabaseAheadOfCodeError) as excinfo:
        db.migrate_database(db_path, migrations_dir=renamed_dir)

    message = str(excinfo.value)
    assert "0022_add_agent_task_stages.sql.bak" in message, (
        f"没有把「同版本号前缀但不可解析」的文件列为改名嫌疑：{message}"
    )
    assert "高度疑似" in message


def test_genuine_ahead_message_does_not_blame_rename(tmp_path: Path) -> None:
    """真·库超前（代码里没有该版本号的文件）时，不应把锅甩给「改名」。"""
    db_path = _fully_migrated_db(tmp_path)
    import sqlite3

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO _migrations (name) VALUES ('9999_migration_from_newer_code.sql')"
        )

    with pytest.raises(db.DatabaseAheadOfCodeError) as excinfo:
        db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR)

    message = str(excinfo.value)
    assert "9999_migration_from_newer_code.sql" in message
    assert "高度疑似" not in message, f"没有同版本号候选时不应断言「疑似改名」：{message}"
    assert "更像 ①" in message, f"应提示更可能是「库被更新代码迁移过」：{message}"


# ── N-3：拒绝启动时不得出现「监听」字样 ─────────────────────────────────────


def test_backend_refusal_prints_no_listening_line(tmp_path: Path) -> None:
    """守卫拒绝启动：输出里不得出现「监听 / 已监听 / 已就绪」（会被误读成已起来）。"""
    db_path = _fully_migrated_db(tmp_path)
    import sqlite3

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO _migrations (name) VALUES ('9999_migration_from_newer_code.sql')"
        )
    cfg_dir = _config_dir(tmp_path, db_path)

    result = _run([str(SRC_DIR / "backend_main.py"), "--config-dir", str(cfg_dir)], tmp_path)
    combined = result.stdout + result.stderr

    assert result.returncode == 2, f"拒绝启动的退出码应为 2，实际 {result.returncode}：\n{combined}"
    assert "9999_migration_from_newer_code.sql" in combined, f"没点名缺失迁移：\n{combined}"
    for misleading in ("监听", "已就绪", "Uvicorn running", "Application startup complete"):
        assert misleading not in combined, f"拒绝路径不该出现「{misleading}」：\n{combined}"


def test_success_path_logs_listening_after_bootstrap_phase(tmp_path: Path) -> None:
    """成功启动必须**打出**「已监听…（HTTP 服务已就绪）」，且行序在 `阶段 1/4 完成` 之后。

    为什么需要这条（38 在 #274 打回时用「假修」抓出来的）：只钉「拒绝时不许出现」
    是**单向**判据 —— 把 `logger.info("[启动] 已监听…")` 整行删掉，那不是挪位置、
    而是日志没了，单向判据照样全绿，分不出「挪到 bind 之后」与「干脆不打印」。
    N-3 的要求是两半：该消失的消失 + 不该消失的还在，且顺序对。
    """
    db_path = _fully_migrated_db(tmp_path)
    cfg_dir = _config_dir(tmp_path, db_path, port=0)  # 端口 0 = 由内核分配，避免撞端口

    proc = subprocess.Popen(
        [sys.executable, str(SRC_DIR / "backend_main.py"), "--config-dir", str(cfg_dir)],
        cwd=str(REPO_ROOT),
        env=_subprocess_env(tmp_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        lines = _collect_until(proc, ("已监听", "阶段 4/4 完成"))
    finally:
        _stop(proc)

    tail = "".join(lines[-40:])
    phase_markers = [i for i, line in enumerate(lines) if "阶段 1/4 完成" in line]
    listen_markers = [i for i, line in enumerate(lines) if "已监听" in line]

    assert phase_markers, f"成功路径没有出现「阶段 1/4 完成」：\n{tail}"
    assert listen_markers, (
        f"成功路径没有出现「已监听」（可能被整行删掉了——这是「删日志冒充修好」的形态）：\n{tail}"
    )
    listen_line = lines[listen_markers[0]]
    assert "HTTP 服务已就绪" in listen_line, f"「已监听」那行缺少就绪语义：{listen_line}"
    assert listen_markers[0] > phase_markers[0], (
        "「已监听」必须出现在「阶段 1/4 完成」**之后**（之前打印=还没 bind 就宣称可服务）"
        f"：phase@{phase_markers[0]} listen@{listen_markers[0]}\n{tail}"
    )


# ── N-4：CLI 撞守卫不得打裸 traceback、退出码与后端统一 ─────────────────────


def test_cli_migrate_refuses_without_traceback(tmp_path: Path) -> None:
    """`db.py migrate` 撞守卫：exit=2 + 可读错误，无 traceback。"""
    db_path = _fully_migrated_db(tmp_path)
    import sqlite3

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO _migrations (name) VALUES ('9999_migration_from_newer_code.sql')"
        )

    result = _run([str(SRC_DIR / "db.py"), "migrate", "--db-path", str(db_path)], tmp_path)
    combined = result.stdout + result.stderr

    assert result.returncode == 2, f"CLI 退出码应为 2（与后端统一），实际 {result.returncode}：\n{combined}"
    assert "Traceback" not in combined, f"仍在打裸 traceback：\n{combined}"
    assert "9999_migration_from_newer_code.sql" in combined, f"没点名缺失迁移：\n{combined}"
    assert "升级" in combined, f"缺少处置建议：\n{combined}"


def test_cli_migrate_normal_path_still_exit_zero(tmp_path: Path) -> None:
    """正常路径不受影响：库落后于代码时 CLI 仍补执行并 exit=0（只改信息不改逻辑）。"""
    db_path = tmp_path / "data" / "data.db"
    db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR, verbose=False)
    import sqlite3

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("DELETE FROM _migrations WHERE name = '0022_add_agent_task_stages.sql'")

    result = _run([str(SRC_DIR / "db.py"), "migrate", "--db-path", str(db_path)], tmp_path)
    combined = result.stdout + result.stderr

    assert result.returncode == 0, f"库落后时 CLI 应成功，实际 {result.returncode}：\n{combined}"
    assert "Traceback" not in combined
    assert "Applied 1 migration(s)." in combined, f"没有补执行：\n{combined}"

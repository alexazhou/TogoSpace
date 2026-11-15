"""#277 F1：裸跑形态暴露面告警（Operator 20:30 决策 A；任务单评论 #1693–#1695）。

判据（与 38 / PM 定稿一致）：
- 2×2 四格穷举：`0.0.0.0`+无鉴权 ⇒ 告警 ≥1；其余三组合 ⇒ =0（只验「该响时响了」
  会漏「条件写成 OR」型错——那样三种组合全会响）；
- **每格独立日志文件**：日志是追加的，共用文件会把上一格的告警残留带成假红（#1695）；
- 固定串 `[安全告警]`（ASCII 方括号；`str.count` 等价 `grep -F`，不涉正则转义）；
- 告警文案不得含 token 或凭证值（#1694）；
- **不阻断启动**：helper 只打日志，main() 在 bind 成功后调用后继续走。
"""

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import backend_main

_ALERT = "[安全告警]"
_LOGBACK_LOGGER = "backend_main"


def _run_cell(tmp_path: Path, cell: int, bind_host: str, auth_enabled: bool) -> str:
    """跑一格，返回该格**独立**日志文件的内容（追加写 ⇒ 每格必须新文件）。"""
    log_file = tmp_path / f"cell-{cell}.log"
    handler = logging.FileHandler(log_file, mode="w", encoding="utf-8")
    target = logging.getLogger(_LOGBACK_LOGGER)
    old_level, old_propagate = target.level, target.propagate
    target.addHandler(handler)
    target.setLevel(logging.WARNING)
    try:
        backend_main._warn_exposure_if_unsafe(bind_host, auth_enabled)
    finally:
        target.removeHandler(handler)
        target.setLevel(old_level)
        target.propagate = old_propagate
        handler.close()
    return log_file.read_text(encoding="utf-8")


def test_exposure_warning_four_cells_exhaustive(tmp_path: Path) -> None:
    """2×2 四格在同一循环里穷举（#1694/#1695）。"""
    cells = [
        ("0.0.0.0", False, True),  # 局域网 + 无鉴权 ⇒ 必须响
        ("127.0.0.1", False, False),  # 回环 ⇒ 不响
        ("0.0.0.0", True, False),  # 开了鉴权 ⇒ 不响
        ("127.0.0.1", True, False),  # 双安全 ⇒ 不响
    ]
    for cell, (bind_host, auth_enabled, should_alert) in enumerate(cells):
        text = _run_cell(tmp_path, cell, bind_host, auth_enabled)
        hits = text.count(_ALERT)
        expected = 1 if should_alert else 0
        assert hits == expected, f"cell#{cell} bind={bind_host} auth={auth_enabled}: hits={hits}"
        assert "token" not in text and "sk-" not in text  # 告警不带 token/凭证
        if should_alert:
            assert bind_host in text  # 告警里点出实际 bind 的地址


def test_exposure_warning_more_loopback_forms_silent(tmp_path: Path) -> None:
    """`::1` / `localhost` 同属回环，也不响（补格，仍同一循环断言）。"""
    for cell, host in enumerate(("::1", "localhost")):
        text = _run_cell(tmp_path, cell, host, False)
        assert text.count(_ALERT) == 0, host


def test_is_loopback_bind() -> None:
    assert backend_main._is_loopback_bind("127.0.0.1")
    assert backend_main._is_loopback_bind("127.9.9.9")
    assert backend_main._is_loopback_bind("::1")
    assert backend_main._is_loopback_bind("localhost")
    assert not backend_main._is_loopback_bind("0.0.0.0")
    assert not backend_main._is_loopback_bind("192.168.1.5")
    # 解析失败按非回环（宁多勿漏的告警形态）
    assert not backend_main._is_loopback_bind("not-an-ip")


# ---------------------------------------------------------------------------
# wiring：helper 存在但没人调等于没做 —— 走一遍真 main()（服务全 mock），验
# bind 成功后真的打/不打告警。两组合同一循环。
# ---------------------------------------------------------------------------


def _patch_main(monkeypatch, fake_app_config, fake_server) -> None:
    """与 test_backend_main_shutdown 同款 mock 集，只为了真跑 main() 一次。"""
    monkeypatch.setattr(backend_main, "_setup_logger", lambda: None)
    monkeypatch.setattr(backend_main, "_remove_pid", lambda: None)
    monkeypatch.setattr(backend_main.llmApiUtil, "init", lambda: None)
    monkeypatch.setattr(backend_main.configUtil, "load", lambda config_dir=None: fake_app_config)
    monkeypatch.setattr(backend_main.configUtil, "get_app_config", lambda: fake_app_config)
    monkeypatch.setattr(backend_main.configUtil, "is_initialized", lambda: True)
    monkeypatch.setattr(backend_main.gtTeamManager, "get_all_teams", AsyncMock(return_value=[]))
    monkeypatch.setattr(backend_main.tornado.httpserver, "HTTPServer", MagicMock(return_value=fake_server))
    monkeypatch.setattr(backend_main, "has_pending_migrations", lambda _path: False)
    monkeypatch.setattr(backend_main, "migrate_database", lambda *_a, **_k: None)
    monkeypatch.setattr(backend_main.skillService, "startup", lambda: None)

    async def _noop_async(*_a, **_k):
        return None

    for name in (
        "messageBus", "llmService", "funcToolService", "ormService",
        "persistenceService", "agentService", "roomService",
        "schedulerService", "presetService",
    ):
        module = getattr(backend_main, name)
        monkeypatch.setattr(module, "startup", _noop_async)
    monkeypatch.setattr(backend_main.presetService, "import_from_app_config", AsyncMock())
    monkeypatch.setattr(backend_main.teamService, "restore_team", AsyncMock())
    monkeypatch.setattr(backend_main.schedulerService, "start_schedule", _noop_async)
    monkeypatch.setattr(backend_main.schedulerService, "stop_schedule", lambda *_a, **_k: None)
    monkeypatch.setattr(backend_main.schedulerService, "start_schedule", _noop_async)
    monkeypatch.setattr(backend_main.schedulerService, "stop_schedule", lambda *_a, **_k: None)
    # shutdown：scheduler/funcTool/skill/room/llm 是同步调用，agent/persistence/orm/preset/messageBus 是 await
    monkeypatch.setattr(backend_main.schedulerService, "shutdown", lambda *_a, **_k: None)
    monkeypatch.setattr(backend_main.skillService, "shutdown", lambda *_a, **_k: None)
    monkeypatch.setattr(backend_main.funcToolService, "shutdown", lambda *_a, **_k: None)
    monkeypatch.setattr(backend_main.roomService, "shutdown", lambda *_a, **_k: None)
    monkeypatch.setattr(backend_main.llmService, "shutdown", lambda *_a, **_k: None)
    for name in ("agentService", "persistenceService", "ormService", "presetService", "messageBus"):
        module = getattr(backend_main, name)
        monkeypatch.setattr(module, "shutdown", _noop_async)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bind_host,auth_enabled,should_alert",
    [("0.0.0.0", False, True), ("127.0.0.1", False, False), ("0.0.0.0", True, False)],
)
async def test_main_wires_exposure_warning(monkeypatch, caplog, bind_host, auth_enabled, should_alert) -> None:
    """main() 真的调用 `_warn_exposure_if_unsafe`：helper 存在但没接线等于没做。"""
    fake_setting = SimpleNamespace(
        bind_host=bind_host,
        bind_port=9091,
        db_path="/tmp/exposure-test.db",
        workspace_root="/tmp/exposure-ws",
        demo_mode=SimpleNamespace(read_only=True),
        auth=SimpleNamespace(enabled=auth_enabled),
    )
    fake_app_config = SimpleNamespace(setting=fake_setting)
    _patch_main(monkeypatch, fake_app_config, MagicMock())

    with caplog.at_level(logging.WARNING, logger=_LOGBACK_LOGGER):
        task = asyncio.create_task(backend_main.main(config_dir="/tmp/exposure-cfg", port=9091))
        await asyncio.sleep(0)
        backend_main.request_shutdown()
        await task

    text = "\n".join(rec.getMessage() for rec in caplog.records)
    hits = text.count(_ALERT)
    assert hits == (1 if should_alert else 0), (bind_host, auth_enabled, hits, text)
    assert "token" not in text and "sk-" not in text

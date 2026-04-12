import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import backend_main


def test_request_shutdown_noop_when_main_loop_not_initialized(monkeypatch):
    monkeypatch.setattr(backend_main, "_main_loop", None)
    monkeypatch.setattr(backend_main, "_shutdown_event", None)

    backend_main.request_shutdown()


def test_request_shutdown_uses_threadsafe_signal_when_loop_is_running(monkeypatch):
    fake_loop = MagicMock()
    fake_loop.is_running.return_value = True
    fake_event = MagicMock()

    monkeypatch.setattr(backend_main, "_main_loop", fake_loop)
    monkeypatch.setattr(backend_main, "_shutdown_event", fake_event)

    backend_main.request_shutdown()

    fake_loop.call_soon_threadsafe.assert_called_once_with(fake_event.set)
    fake_event.set.assert_not_called()


def test_request_shutdown_sets_event_directly_when_loop_not_running(monkeypatch):
    fake_loop = MagicMock()
    fake_loop.is_running.return_value = False
    fake_event = MagicMock()

    monkeypatch.setattr(backend_main, "_main_loop", fake_loop)
    monkeypatch.setattr(backend_main, "_shutdown_event", fake_event)

    backend_main.request_shutdown()

    fake_event.set.assert_called_once_with()
    fake_loop.call_soon_threadsafe.assert_not_called()


@pytest.mark.asyncio
async def test_main_waits_for_shutdown_event_and_runs_cleanup(monkeypatch):
    fake_setting = SimpleNamespace(
        bind_host="127.0.0.1",
        bind_port=8080,
        persistence=SimpleNamespace(db_path="/tmp/test.db"),
        workspace_root="/tmp/workspace",
    )
    fake_app_config = SimpleNamespace(setting=fake_setting)

    fake_server = MagicMock()

    startup_calls: list[str] = []
    cleanup_calls: list[str] = []

    async def _record_startup(name: str) -> None:
        startup_calls.append(name)

    async def _record_shutdown(name: str) -> None:
        cleanup_calls.append(name)

    monkeypatch.setattr(backend_main, "_setup_logger", lambda: None)
    monkeypatch.setattr(backend_main, "_remove_pid", lambda: cleanup_calls.append("remove_pid"))
    monkeypatch.setattr(backend_main.llmApiUtil, "init", lambda: None)
    monkeypatch.setattr(backend_main.configUtil, "load", lambda config_dir=None: fake_app_config)
    monkeypatch.setattr(backend_main.gtTeamManager, "get_all_teams", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        backend_main.tornado.httpserver,
        "HTTPServer",
        MagicMock(return_value=fake_server),
    )

    async def _message_bus_startup():
        await _record_startup("messageBus")

    async def _llm_service_startup():
        await _record_startup("llmService")

    async def _func_tool_service_startup():
        await _record_startup("funcToolService")

    async def _orm_service_startup(_db_path: str):
        await _record_startup("ormService")

    async def _persistence_service_startup():
        await _record_startup("persistenceService")

    async def _agent_service_startup():
        await _record_startup("agentService")

    async def _room_service_startup():
        await _record_startup("roomService")

    async def _scheduler_service_startup():
        await _record_startup("schedulerService")

    async def _preset_service_startup():
        await _record_startup("presetService")

    async def _agent_service_shutdown():
        await _record_shutdown("agentService")

    async def _persistence_service_shutdown():
        await _record_shutdown("persistenceService")

    async def _orm_service_shutdown():
        await _record_shutdown("ormService")

    async def _message_bus_shutdown():
        await _record_shutdown("messageBus")

    monkeypatch.setattr(backend_main.messageBus, "startup", _message_bus_startup)
    monkeypatch.setattr(backend_main.llmService, "startup", _llm_service_startup)
    monkeypatch.setattr(backend_main.funcToolService, "startup", _func_tool_service_startup)
    monkeypatch.setattr(backend_main.ormService, "startup", _orm_service_startup)
    monkeypatch.setattr(backend_main.persistenceService, "startup", _persistence_service_startup)
    monkeypatch.setattr(backend_main.agentService, "startup", _agent_service_startup)
    monkeypatch.setattr(backend_main.roomService, "startup", _room_service_startup)
    monkeypatch.setattr(backend_main.schedulerService, "startup", _scheduler_service_startup)
    monkeypatch.setattr(backend_main.presetService, "startup", _preset_service_startup)
    monkeypatch.setattr(backend_main.presetService, "import_from_app_config", AsyncMock())
    monkeypatch.setattr(backend_main.teamService, "restore_team", AsyncMock())

    monkeypatch.setattr(backend_main.schedulerService, "shutdown", lambda: cleanup_calls.append("schedulerService"))
    monkeypatch.setattr(backend_main.agentService, "shutdown", _agent_service_shutdown)
    monkeypatch.setattr(backend_main.persistenceService, "shutdown", _persistence_service_shutdown)
    monkeypatch.setattr(backend_main.ormService, "shutdown", _orm_service_shutdown)
    monkeypatch.setattr(backend_main.funcToolService, "shutdown", lambda: cleanup_calls.append("funcToolService"))
    monkeypatch.setattr(backend_main.roomService, "shutdown", lambda: cleanup_calls.append("roomService"))
    monkeypatch.setattr(backend_main.llmService, "shutdown", lambda: cleanup_calls.append("llmService"))
    monkeypatch.setattr(backend_main.messageBus, "shutdown", _message_bus_shutdown)

    task = asyncio.create_task(backend_main.main(config_dir="/tmp/config", port=9090))
    await asyncio.sleep(0)
    backend_main.request_shutdown()
    await task

    fake_server.listen.assert_called_once_with(9090, "127.0.0.1")
    fake_server.stop.assert_called_once_with()
    assert startup_calls == [
        "messageBus",
        "llmService",
        "funcToolService",
        "ormService",
        "persistenceService",
        "agentService",
        "roomService",
        "schedulerService",
        "presetService",
    ]
    assert cleanup_calls == [
        "schedulerService",
        "agentService",
        "persistenceService",
        "ormService",
        "funcToolService",
        "roomService",
        "llmService",
        "messageBus",
        "remove_pid",
    ]
    assert backend_main._main_loop is None
    assert backend_main._shutdown_event is None

import json
import os
import sys

import pytest
from util import configUtil
from util.configTypes import AppConfig, LlmServiceConfig, PersistenceConfig, resolve_team_workdir

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


def test_runtime_configs_load_from_config_dir(tmp_path):
    (tmp_path / "setting.json").write_text(json.dumps({
        "default_llm_server": "mock",
        "llm_services": [
            {
                "name": "mock",
                "enable": True,
                "base_url": "http://127.0.0.1:9999/v1/chat/completions",
                "api_key": "test-key",
                "type": "openai-compatible",
            }
        ],
        "persistence": {
            "enabled": True,
            "db_path": "./runtime/test.db",
        },
        "workspace_root": "/tmp/workspaces",
    }), encoding="utf-8")

    app_config = configUtil.load(str(tmp_path))

    assert isinstance(app_config, AppConfig)
    assert app_config.llm_service.name == "mock"
    assert app_config.llm_service.base_url == "http://127.0.0.1:9999/v1/chat/completions"
    assert app_config.persistence.enabled is True
    assert app_config.persistence.db_path == "./runtime/test.db"
    assert app_config.workspace_root == "/tmp/workspaces"


def test_runtime_configs_skip_disabled_llm_service(tmp_path):
    (tmp_path / "setting.json").write_text(json.dumps({
        "default_llm_server": "mock_disabled",
        "llm_services": [
            {
                "name": "mock_disabled",
                "enable": False,
                "base_url": "http://127.0.0.1:1111/v1/chat/completions",
                "api_key": "disabled-key",
                "type": "openai-compatible",
            },
            {
                "name": "mock",
                "enable": True,
                "base_url": "http://127.0.0.1:8888/v1/chat/completions",
                "api_key": "app-key",
                "type": "openai-compatible",
            }
        ],
    }), encoding="utf-8")

    try:
        configUtil.load(str(tmp_path))
        assert False, "expected ValueError for disabled default llm server"
    except ValueError as exc:
        assert "已禁用" in str(exc) or "未在 llm_services 中定义或已禁用" in str(exc)


def test_runtime_configs_allow_llm_only_setting(tmp_path):
    (tmp_path / "setting.json").write_text(json.dumps({
        "default_llm_server": "mock",
        "llm_services": [
            {
                "name": "mock",
                "enable": True,
                "base_url": "http://127.0.0.1:7777/v1/chat/completions",
                "api_key": "llm-only-key",
                "type": "openai-compatible",
            }
        ],
    }), encoding="utf-8")

    app_config = configUtil.load(str(tmp_path))

    assert app_config.llm_service.name == "mock"
    assert app_config.llm_service.base_url == "http://127.0.0.1:7777/v1/chat/completions"
    assert app_config.persistence.enabled is False
    assert app_config.persistence.db_path == "../test_data/data.db"


def test_persistence_default_db_path_in_non_test_env(tmp_path, monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("TEAMAGENT_ENV", "prod")

    cfg = configUtil.load_persistence_config(str(tmp_path))
    assert cfg == {
        "enabled": False,
        "db_path": "../data/data.db",
    }


def test_persistence_default_db_path_in_test_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TEAMAGENT_ENV", "test")

    cfg = configUtil.load_persistence_config(str(tmp_path))
    assert cfg == {
        "enabled": False,
        "db_path": "../test_data/data.db",
    }


def test_load_returns_appconfig_with_typed_fields(tmp_path):
    (tmp_path / "setting.json").write_text(json.dumps({
        "default_llm_server": "svc",
        "llm_services": [
            {
                "name": "svc",
                "enable": True,
                "base_url": "http://localhost/v1",
                "api_key": "key-123",
                "type": "openai-compatible",
                "model": "gpt-4",
            }
        ],
        "persistence": {
            "enabled": False,
            "db_path": "./data/db.sqlite",
        },
    }), encoding="utf-8")

    app_config = configUtil.load(str(tmp_path))

    assert isinstance(app_config, AppConfig)
    assert isinstance(app_config.llm_service, LlmServiceConfig)
    assert isinstance(app_config.persistence, PersistenceConfig)
    assert app_config.llm_service.model == "gpt-4"
    assert app_config.llm_service.api_key == "key-123"
    assert app_config.agents == []
    assert app_config.teams == []
    assert app_config.workspace_root


def test_workspace_root_defaults_to_repo_root_when_missing(tmp_path):
    (tmp_path / "setting.json").write_text(json.dumps({
        "default_llm_server": "svc",
        "llm_services": [
            {
                "name": "svc",
                "enable": True,
                "base_url": "http://localhost/v1",
                "api_key": "key-123",
                "type": "openai-compatible",
            }
        ],
    }), encoding="utf-8")

    app_config = configUtil.load(str(tmp_path))
    assert os.path.isabs(app_config.workspace_root)


def test_resolve_team_workdir_prefers_explicit_working_directory():
    resolved = resolve_team_workdir(
        team_name="default",
        working_directory="/tmp/custom-team-dir",
        workspace_root="/tmp/workspaces",
    )
    assert resolved == "/tmp/custom-team-dir"


def test_resolve_team_workdir_falls_back_to_workspace_root_and_team_name():
    resolved = resolve_team_workdir(
        team_name="default",
        working_directory="",
        workspace_root="/tmp/workspaces",
    )
    assert resolved == "/tmp/workspaces/default"


def test_load_json_objects_from_dir_returns_sorted_objects(tmp_path):
    (tmp_path / "b.json").write_text(json.dumps({"name": "b"}), encoding="utf-8")
    (tmp_path / "a.json").write_text(json.dumps({"name": "a"}), encoding="utf-8")

    items = configUtil.load_json_objects_from_dir(str(tmp_path))

    assert [item["name"] for item in items] == ["a", "b"]


def test_load_json_objects_from_dir_raises_for_non_object(tmp_path):
    (tmp_path / "invalid.json").write_text(json.dumps(["not", "object"]), encoding="utf-8")

    with pytest.raises(ValueError):
        configUtil.load_json_objects_from_dir(str(tmp_path))

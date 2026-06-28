import json
import os
import sys
import builtins

import pytest
from util import configUtil
from constants import LlmServiceType
from util.configTypes import (
    AppConfig,
    LlmProviderConfig,
    SettingConfig,
)

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


def test_default_db_path_in_non_test_env(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("TEAMAGENT_ENV", "prod")

    assert configUtil.get_db_path().endswith("data/data.db")



def test_default_db_path_in_test_env(monkeypatch):
    monkeypatch.setenv("TEAMAGENT_ENV", "test")

    assert configUtil.get_db_path() == "../test_data/data.db"



def test_demo_mode_flags_load_from_setting(tmp_path):
    (tmp_path / "setting.json").write_text(json.dumps({
            "version": "v2",
        "demo_mode": {
            "enabled": True,
            "freeze_data": True,
            "hide_sensitive_info": False,
        },
        "default_models": {"primary": "mock-model@svc"},
        "llm_providers": [
            {
                "name": "svc",
                "urls": {"openai": "http://localhost/v1"},
                "api_key": "key-123",
                "type": "openai",
                "models": [{"name": "mock-model", "protocol": "openai"}]
            }
        ],
    }), encoding="utf-8")

    app_config = configUtil.load(str(tmp_path), force_reload=True)

    assert app_config.setting.demo_mode.enabled is True
    assert app_config.setting.demo_mode.freeze_data is True
    assert app_config.setting.demo_mode.hide_sensitive_info is False
    assert configUtil.is_demo_mode() is True
    assert app_config.setting.demo_mode.read_only is True
    assert app_config.setting.demo_mode.hide_sensitive is False



def test_development_mode_loads_from_setting(tmp_path):
    (tmp_path / "setting.json").write_text(json.dumps({
            "version": "v2",
        "development_mode": True,
        "default_models": {"primary": "mock-model@svc"},
        "llm_providers": [
            {
                "name": "svc",
                "urls": {"openai": "http://localhost/v1"},
                "api_key": "key-123",
                "type": "openai",
                "models": [{"name": "mock-model", "protocol": "openai"}]
            }
        ],
    }), encoding="utf-8")

    app_config = configUtil.load(str(tmp_path), force_reload=True)

    assert app_config.setting.development_mode is True



def test_workspace_root_defaults_to_repo_root_when_missing(tmp_path):
    (tmp_path / "setting.json").write_text(json.dumps({
            "version": "v2",
        "default_models": {"primary": "mock-model@svc"},
        "llm_providers": [
            {
                "name": "svc",
                "urls": {"openai": "http://localhost/v1"},
                "api_key": "key-123",
                "type": "openai",
                "models": [{"name": "mock-model", "protocol": "openai"}]
            }
        ],
    }), encoding="utf-8")

    app_config = configUtil.load(str(tmp_path))
    assert os.path.isabs(app_config.setting.workspace_root)



def test_workspace_root_defaults_to_repo_root_when_null(tmp_path):
    (tmp_path / "setting.json").write_text(json.dumps({
            "version": "v2",
        "default_models": {"primary": "mock-model@svc"},
        "llm_providers": [
            {
                "name": "svc",
                "urls": {"openai": "http://localhost/v1"},
                "api_key": "key-123",
                "type": "openai",
                "models": [{"name": "mock-model", "protocol": "openai"}]
            }
        ],
        "workspace_root": None,
    }), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        configUtil.load(str(tmp_path))
    assert "workspace_root 不允许为 null" in str(exc_info.value)



def test_workspace_root_keeps_blank_when_provided(tmp_path):
    (tmp_path / "setting.json").write_text(json.dumps({
            "version": "v2",
        "default_models": {"primary": "mock-model@svc"},
        "llm_providers": [
            {
                "name": "svc",
                "urls": {"openai": "http://localhost/v1"},
                "api_key": "key-123",
                "type": "openai",
                "models": [{"name": "mock-model", "protocol": "openai"}]
            }
        ],
        "workspace_root": "   ",
    }), encoding="utf-8")

    app_config = configUtil.load(str(tmp_path))
    assert app_config.setting.workspace_root == "   "



def test_db_path_defaults_when_blank(tmp_path):
    os.environ.pop("TEAMAGENT_DB_PATH", None)
    (tmp_path / "setting.json").write_text(json.dumps({
            "version": "v2",
        "default_models": {"primary": "mock-model@svc"},
        "llm_providers": [
            {
                "name": "svc",
                "urls": {"openai": "http://localhost/v1"},
                "api_key": "key-123",
                "type": "openai",
                "models": [{"name": "mock-model", "protocol": "openai"}]
            }
        ],
        "db_path": "   ",
    }), encoding="utf-8")

    app_config = configUtil.load(str(tmp_path))
    assert app_config.setting.db_path == configUtil.get_db_path()



def test_get_default_team_workdir_uses_workspace_root():
    setting = SettingConfig(
        workspace_root="/tmp/workspaces",
        default_models={"primary": "mock-model@mock"},
        llm_providers=[
            {
                "name": "mock",
                "urls": {"openai": "http://localhost/v1"},
                "api_key": "key-123",
                "type": "openai",
                "models": [{"name": "mock-model", "protocol": "openai"}]
            }
        ],
    )
    resolved = setting.get_default_team_workdir("default")
    assert resolved == "/tmp/workspaces/default"



def test_get_default_team_workdir_joins_team_name():
    setting = SettingConfig(
        workspace_root="/tmp/workspaces",
        default_models={"primary": "mock-model@mock"},
        llm_providers=[
            {
                "name": "mock",
                "urls": {"openai": "http://localhost/v1"},
                "api_key": "key-123",
                "type": "openai",
                "models": [{"name": "mock-model", "protocol": "openai"}]
            }
        ],
    )
    resolved = setting.get_default_team_workdir("research")
    assert resolved == "/tmp/workspaces/research"



def test_team_workdir_prefers_explicit_working_directory():
    team_workdir = "/tmp/custom-team-dir"
    setting = SettingConfig(
        workspace_root="/tmp/workspaces",
        default_models={"primary": "mock-model@mock"},
        llm_providers=[
            {
                "name": "mock",
                "urls": {"openai": "http://localhost/v1"},
                "api_key": "key-123",
                "type": "openai",
                "models": [{"name": "mock-model", "protocol": "openai"}]
            }
        ],
    )
    resolved = team_workdir or setting.get_default_team_workdir("default")
    assert resolved == "/tmp/custom-team-dir"



def test_team_workdir_falls_back_to_default_when_empty():
    team_workdir = ""
    setting = SettingConfig(
        workspace_root="/tmp/workspaces",
        default_models={"primary": "mock-model@mock"},
        llm_providers=[
            {
                "name": "mock",
                "urls": {"openai": "http://localhost/v1"},
                "api_key": "key-123",
                "type": "openai",
                "models": [{"name": "mock-model", "protocol": "openai"}]
            }
        ],
    )
    resolved = team_workdir or setting.get_default_team_workdir("default")
    assert resolved == "/tmp/workspaces/default"




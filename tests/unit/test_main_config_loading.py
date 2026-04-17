import json
import os
import sys
import builtins

import pytest
from util import configUtil
from util.configTypes import (
    AppConfig,
    LlmServiceConfig,
    LlmServiceType,
    PersistenceConfig,
    SettingConfig,
)

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


def test_runtime_configs_load_from_config_dir(tmp_path):
    os.environ.pop("TEAMAGENT_DB_PATH", None)
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
    llm_cfg = app_config.setting.current_llm_service

    assert isinstance(app_config, AppConfig)
    assert llm_cfg.name == "mock"
    assert llm_cfg.base_url == "http://127.0.0.1:9999/v1/chat/completions"
    assert app_config.setting.persistence.enabled is True
    assert app_config.setting.persistence.db_path == "./runtime/test.db"
    assert app_config.setting.workspace_root == "/tmp/workspaces"


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

    # V13: default 指向已禁用服务时，自动回退到首个可用服务。
    app_config = configUtil.load(str(tmp_path))
    assert app_config.setting.current_llm_service is not None
    assert app_config.setting.current_llm_service.name == "mock"


def test_runtime_configs_allow_llm_only_setting(tmp_path):
    os.environ.pop("TEAMAGENT_DB_PATH", None)
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
    llm_cfg = app_config.setting.current_llm_service

    assert llm_cfg.name == "mock"
    assert llm_cfg.base_url == "http://127.0.0.1:7777/v1/chat/completions"
    assert app_config.setting.persistence.enabled is False
    assert app_config.setting.persistence.db_path == "../test_data/data.db"


def test_default_db_path_in_non_test_env(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("TEAMAGENT_ENV", "prod")

    assert configUtil.get_db_path().endswith("data/data.db")


def test_default_db_path_in_test_env(monkeypatch):
    monkeypatch.setenv("TEAMAGENT_ENV", "test")

    assert configUtil.get_db_path() == "../test_data/data.db"


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
    llm_cfg = app_config.setting.current_llm_service

    assert isinstance(app_config, AppConfig)
    assert isinstance(llm_cfg, LlmServiceConfig)
    assert llm_cfg.type == LlmServiceType.OPENAI_COMPATIBLE
    assert isinstance(app_config.setting, SettingConfig)
    assert isinstance(app_config.setting.persistence, PersistenceConfig)
    assert llm_cfg.model == "gpt-4"
    assert llm_cfg.api_key == "key-123"
    assert isinstance(app_config.role_templates, list)
    assert isinstance(app_config.teams, list)
    assert app_config.setting.workspace_root


def test_llm_service_extra_headers_defaults_to_opencode(tmp_path):
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

    app_config = configUtil.load(str(tmp_path), force_reload=True)

    assert app_config.setting.current_llm_service.extra_headers == {"User-Agent": "opencode"}


def test_llm_service_extra_headers_use_json_value_when_provided(tmp_path):
    (tmp_path / "setting.json").write_text(json.dumps({
        "default_llm_server": "svc",
        "llm_services": [
            {
                "name": "svc",
                "enable": True,
                "base_url": "http://localhost/v1",
                "api_key": "key-123",
                "type": "openai-compatible",
                "extra_headers": {
                    "X-Client-Name": "openclaw",
                },
            }
        ],
    }), encoding="utf-8")

    app_config = configUtil.load(str(tmp_path), force_reload=True)

    assert app_config.setting.current_llm_service.extra_headers == {"X-Client-Name": "openclaw"}


def test_llm_service_provider_params_use_json_value_when_provided(tmp_path):
    (tmp_path / "setting.json").write_text(json.dumps({
        "default_llm_server": "svc",
        "llm_services": [
            {
                "name": "svc",
                "enable": True,
                "base_url": "http://localhost/v1",
                "api_key": "key-123",
                "type": "openai-compatible",
                "provider_params": {
                    "reasoning_effort": "high",
                },
            }
        ],
    }), encoding="utf-8")

    app_config = configUtil.load(str(tmp_path), force_reload=True)

    assert app_config.setting.current_llm_service.provider_params == {"reasoning_effort": "high"}


def test_llm_service_provider_params_reject_reserved_keys(tmp_path):
    (tmp_path / "setting.json").write_text(json.dumps({
        "default_llm_server": "svc",
        "llm_services": [
            {
                "name": "svc",
                "enable": True,
                "base_url": "http://localhost/v1",
                "api_key": "key-123",
                "type": "openai-compatible",
                "provider_params": {
                    "model": "other-model",
                },
            }
        ],
    }), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        configUtil.load(str(tmp_path), force_reload=True)

    assert "provider_params 包含保留字段" in str(exc_info.value)


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
    assert os.path.isabs(app_config.setting.workspace_root)


def test_workspace_root_defaults_to_repo_root_when_null(tmp_path):
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
        "workspace_root": None,
    }), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        configUtil.load(str(tmp_path))
    assert "workspace_root 不允许为 null" in str(exc_info.value)


def test_workspace_root_keeps_blank_when_provided(tmp_path):
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
        "workspace_root": "   ",
    }), encoding="utf-8")

    app_config = configUtil.load(str(tmp_path))
    assert app_config.setting.workspace_root == "   "


def test_persistence_defaults_when_null(tmp_path):
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
        "persistence": None,
    }), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        configUtil.load(str(tmp_path))
    assert "persistence 不允许为 null" in str(exc_info.value)


def test_persistence_db_path_defaults_when_blank(tmp_path):
    os.environ.pop("TEAMAGENT_DB_PATH", None)
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
        "persistence": {
            "enabled": True,
            "db_path": "   ",
        },
    }), encoding="utf-8")

    app_config = configUtil.load(str(tmp_path))
    assert app_config.setting.persistence.enabled is True
    assert app_config.setting.persistence.db_path == configUtil.get_db_path()


def test_get_default_team_workdir_uses_workspace_root():
    setting = SettingConfig(
        workspace_root="/tmp/workspaces",
        llm_services=[
            {
                "name": "mock",
                "enable": True,
                "base_url": "http://localhost/v1",
                "api_key": "key-123",
                "type": "openai-compatible",
            }
        ],
    )
    resolved = setting.get_default_team_workdir("default")
    assert resolved == "/tmp/workspaces/default"


def test_get_default_team_workdir_joins_team_name():
    setting = SettingConfig(
        workspace_root="/tmp/workspaces",
        llm_services=[
            {
                "name": "mock",
                "enable": True,
                "base_url": "http://localhost/v1",
                "api_key": "key-123",
                "type": "openai-compatible",
            }
        ],
    )
    resolved = setting.get_default_team_workdir("research")
    assert resolved == "/tmp/workspaces/research"


def test_team_workdir_prefers_explicit_working_directory():
    team_workdir = "/tmp/custom-team-dir"
    setting = SettingConfig(
        workspace_root="/tmp/workspaces",
        llm_services=[
            {
                "name": "mock",
                "enable": True,
                "base_url": "http://localhost/v1",
                "api_key": "key-123",
                "type": "openai-compatible",
            }
        ],
    )
    resolved = team_workdir or setting.get_default_team_workdir("default")
    assert resolved == "/tmp/custom-team-dir"


def test_team_workdir_falls_back_to_default_when_empty():
    team_workdir = ""
    setting = SettingConfig(
        workspace_root="/tmp/workspaces",
        llm_services=[
            {
                "name": "mock",
                "enable": True,
                "base_url": "http://localhost/v1",
                "api_key": "key-123",
                "type": "openai-compatible",
            }
        ],
    )
    resolved = team_workdir or setting.get_default_team_workdir("default")
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


def test_load_reads_setting_json_once(tmp_path, monkeypatch):
    setting_file = tmp_path / "setting.json"
    setting_file.write_text(json.dumps({
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
        "persistence": {
            "enabled": True,
            "db_path": "./runtime/test.db",
        },
        "workspace_root": "/tmp/workspaces",
    }), encoding="utf-8")

    target_path = os.path.abspath(setting_file)
    open_count = {"setting_json": 0}
    real_open = builtins.open

    def _counting_open(path, *args, **kwargs):
        if os.path.abspath(path) == target_path:
            open_count["setting_json"] += 1
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _counting_open)

    configUtil.load(str(tmp_path))

    assert open_count["setting_json"] == 1


def test_load_creates_setting_json_and_readme_when_missing(tmp_path):
    configUtil.load(str(tmp_path), force_reload=True)

    setting_file = tmp_path / "setting.json"
    readme_file = tmp_path / "setting.README.md"

    assert setting_file.is_file()
    assert readme_file.is_file()

    setting_data = json.loads(setting_file.read_text(encoding="utf-8"))
    assert setting_data["default_llm_server"] == "qwen"
    assert "llm_services" in setting_data

    readme_text = readme_file.read_text(encoding="utf-8")
    assert "setting.json 说明" in readme_text
    assert "default_llm_server" in readme_text


def test_load_setting_ignores_extra_keys(tmp_path):
    (tmp_path / "setting.json").write_text(json.dumps({
        "default_llm_server": "mock",
        "llm_services": [
            {
                "name": "mock",
                "enable": True,
                "base_url": "http://localhost/v1",
                "api_key": "test-key",
                "type": "openai-compatible",
            }
        ],
        "workspace_root": "/tmp/ws",
        "unknown_key": {"keep": False},
    }), encoding="utf-8")

    setting = configUtil.load(str(tmp_path)).setting

    assert setting.default_llm_server == "mock"
    assert setting.workspace_root == "/tmp/ws"


def test_get_app_config_raises_when_cache_is_empty(monkeypatch):
    monkeypatch.setattr(configUtil, "_cached_app_config", None)
    monkeypatch.setattr(configUtil, "_cached_config_dir", None)
    monkeypatch.setattr(configUtil, "_cached_preset_dir", None)

    with pytest.raises(RuntimeError) as exc_info:
        configUtil.get_app_config()

    assert "请先调用 configUtil.load" in str(exc_info.value)


def test_empty_llm_services_config_loads_successfully(tmp_path):
    """V13: llm_services 为空时配置可以正常加载，不抛异常。"""
    (tmp_path / "setting.json").write_text(json.dumps({
        "llm_services": [],
        "default_llm_server": None,
    }), encoding="utf-8")

    app_config = configUtil.load(str(tmp_path), force_reload=True)
    assert app_config.setting.llm_services == []
    assert app_config.setting.is_llm_configured is False


def test_empty_llm_services_current_llm_service_returns_none(tmp_path):
    """V13: llm_services 为空时 current_llm_service 返回 None。"""
    (tmp_path / "setting.json").write_text(json.dumps({
        "llm_services": [],
        "default_llm_server": None,
    }), encoding="utf-8")

    app_config = configUtil.load(str(tmp_path), force_reload=True)
    assert app_config.setting.current_llm_service is None


def test_all_disabled_llm_services_loads_successfully(tmp_path):
    """V13: 所有 LLM 服务禁用时配置可以正常加载。"""
    (tmp_path / "setting.json").write_text(json.dumps({
        "llm_services": [
            {
                "name": "disabled",
                "enable": False,
                "base_url": "http://localhost/v1",
                "api_key": "key",
                "type": "openai-compatible",
            }
        ],
        "default_llm_server": "disabled",
    }), encoding="utf-8")

    app_config = configUtil.load(str(tmp_path), force_reload=True)
    assert app_config.setting.is_llm_configured is False
    assert app_config.setting.current_llm_service is None


def test_is_llm_configured_true_with_enabled_service(tmp_path):
    """V13: 至少一个 enable=True 的服务时 is_llm_configured 为 True。"""
    (tmp_path / "setting.json").write_text(json.dumps({
        "llm_services": [
            {
                "name": "mock",
                "enable": True,
                "base_url": "http://localhost/v1",
                "api_key": "key",
                "type": "openai-compatible",
            }
        ],
        "default_llm_server": "mock",
    }), encoding="utf-8")

    app_config = configUtil.load(str(tmp_path), force_reload=True)
    assert app_config.setting.is_llm_configured is True

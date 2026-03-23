import json
import os
import sys

from backend_main import _load_runtime_configs
from util import configUtil

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
    }), encoding="utf-8")

    llm_cfg, persistence_cfg = _load_runtime_configs(str(tmp_path))

    assert llm_cfg["name"] == "mock"
    assert llm_cfg["base_url"] == "http://127.0.0.1:9999/v1/chat/completions"
    assert persistence_cfg == {
        "enabled": True,
        "db_path": "./runtime/test.db",
    }


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
        _load_runtime_configs(str(tmp_path))
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

    llm_cfg, persistence_cfg = _load_runtime_configs(str(tmp_path))

    assert llm_cfg["name"] == "mock"
    assert llm_cfg["base_url"] == "http://127.0.0.1:7777/v1/chat/completions"
    assert persistence_cfg == {
        "enabled": False,
        "db_path": "../test_data/data.db",
    }


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

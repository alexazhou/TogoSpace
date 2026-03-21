import json
import os
import sys

import pytest

from main import _load_runtime_configs

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

pytestmark = pytest.mark.forked


def test_runtime_configs_load_from_config_dir(tmp_path):
    (tmp_path / "llm.json").write_text(json.dumps({
        "active_llm_service": "mock",
        "llm_services": [
            {
                "name": "mock",
                "base_url": "http://127.0.0.1:9999/v1/chat/completions",
                "api_key": "test-key",
                "type": "openai-compatible",
            }
        ],
    }), encoding="utf-8")
    (tmp_path / "config.json").write_text(json.dumps({
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


def test_runtime_configs_fall_back_to_single_config_file_in_dir(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({
        "active_llm_service": "mock",
        "llm_services": [
            {
                "name": "mock",
                "base_url": "http://127.0.0.1:8888/v1/chat/completions",
                "api_key": "app-key",
                "type": "openai-compatible",
            }
        ],
        "persistence": {
            "enabled": True,
            "db_path": "./runtime/from-app.db",
        },
    }), encoding="utf-8")

    llm_cfg, persistence_cfg = _load_runtime_configs(str(tmp_path))

    assert llm_cfg["name"] == "mock"
    assert llm_cfg["base_url"] == "http://127.0.0.1:8888/v1/chat/completions"
    assert persistence_cfg == {
        "enabled": True,
        "db_path": "./runtime/from-app.db",
    }


def test_runtime_configs_allow_llm_only_config_dir(tmp_path):
    (tmp_path / "llm.json").write_text(json.dumps({
        "active_llm_service": "mock",
        "llm_services": [
            {
                "name": "mock",
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
        "db_path": "./runtime/state/teamagent.db",
    }

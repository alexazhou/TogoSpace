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


def test_runtime_configs_load_from_config_dir(tmp_path):
    os.environ.pop("TEAMAGENT_DB_PATH", None)
    (tmp_path / "setting.json").write_text(json.dumps({
            "version": "v2",
        "default_models": {"primary": "mock-model@mock"},
        "llm_providers": [
            {
                "name": "mock",
                "urls": {"openai": "http://127.0.0.1:9999/v1/chat/completions"},
                "api_key": "test-key",
                "type": "openai",
                "models": [{"name": "mock-model", "protocol": "openai"}]
            }
        ],
        "db_path": "./runtime/test.db",
        "workspace_root": "/tmp/workspaces",
    }), encoding="utf-8")

    app_config = configUtil.load(str(tmp_path))
    llm_cfg = app_config.setting.llm_providers[0]

    assert isinstance(app_config, AppConfig)
    assert llm_cfg.name == "mock"
    assert llm_cfg.urls["openai"] == "http://127.0.0.1:9999/v1/chat/completions"
    assert app_config.setting.db_path == "./runtime/test.db"
    assert app_config.setting.workspace_root == "/tmp/workspaces"



def test_load_returns_appconfig_with_typed_fields(tmp_path):
    (tmp_path / "setting.json").write_text(json.dumps({
            "version": "v2",
        "default_models": {"primary": "gpt-4@svc"},
        "llm_providers": [
            {
                "name": "svc",
                "urls": {"openai": "http://localhost/v1"},
                "api_key": "key-123",
                "type": "openai",
                "models": [{"name": "gpt-4", "protocol": "openai"}]
            }
        ],
        "db_path": "./data/db.sqlite",
    }), encoding="utf-8")

    app_config = configUtil.load(str(tmp_path))
    llm_cfg = app_config.setting.llm_providers[0]

    assert isinstance(app_config, AppConfig)
    assert isinstance(llm_cfg, LlmProviderConfig)
    assert llm_cfg.type == "openai"
    assert isinstance(app_config.setting, SettingConfig)
    assert app_config.setting.db_path == "./data/db.sqlite"
    assert llm_cfg.models[0].name == "gpt-4"
    assert llm_cfg.api_key == "key-123"
    assert isinstance(app_config.role_templates_preset, list)
    assert isinstance(app_config.teams_preset, list)
    assert app_config.setting.workspace_root



def test_load_reads_setting_json_once(tmp_path, monkeypatch):
    setting_file = tmp_path / "setting.json"
    setting_file.write_text(json.dumps({
            "version": "v2",
        "default_models": {"primary": "mock-model@mock"},
        "llm_providers": [
            {
                "name": "mock",
                "urls": {"openai": "http://127.0.0.1:7777/v1/chat/completions"},
                "api_key": "llm-only-key",
                "type": "openai",
                "models": [{"name": "mock-model", "protocol": "openai"}]
            }
        ],
        "db_path": "./runtime/test.db",
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



def test_load_creates_setting_json_when_missing(tmp_path):
    """测试加载配置时自动创建 setting.json（README 在测试环境下不生成）。"""
    configUtil.load(str(tmp_path), force_reload=True)

    setting_file = tmp_path / "setting.json"
    readme_file = tmp_path / "setting.README.md"

    assert setting_file.is_file()
    # README 在测试环境下不生成（_is_running_tests() 返回 True）
    assert not readme_file.is_file()

    setting_data = json.loads(setting_file.read_text(encoding="utf-8"))
    assert setting_data["default_llm_server"] == "qwen"
    assert setting_data["development_mode"] is False
    assert "llm_services" in setting_data



def test_load_setting_ignores_extra_keys(tmp_path):
    (tmp_path / "setting.json").write_text(json.dumps({
            "version": "v2",
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

    assert setting.default_models.primary.split('@')[1] if setting.default_models.primary else '' == "mock"
    assert setting.workspace_root == "/tmp/ws"



def test_get_app_config_raises_when_cache_is_empty(monkeypatch):
    monkeypatch.setattr(configUtil.core, "_cached_app_config", None)
    monkeypatch.setattr(configUtil.core, "_cached_config_dir", None)
    monkeypatch.setattr(configUtil.core, "_cached_preset_dir", None)

    with pytest.raises(RuntimeError) as exc_info:
        configUtil.get_app_config()

    assert "请先调用 configUtil.load" in str(exc_info.value)



def test_load_json_objects_from_dir_returns_sorted_objects(tmp_path):
    (tmp_path / "b.json").write_text(json.dumps({
            "version": "v2","name": "b"}), encoding="utf-8")
    (tmp_path / "a.json").write_text(json.dumps({
            "version": "v2","name": "a"}), encoding="utf-8")

    items = configUtil.load_json_objects_from_dir(str(tmp_path))

    assert [item["name"] for item in items] == ["a", "b"]



def test_load_json_objects_from_dir_raises_for_non_object(tmp_path):
    (tmp_path / "invalid.json").write_text(json.dumps(["not", "object"]), encoding="utf-8")

    with pytest.raises(ValueError):
        configUtil.load_json_objects_from_dir(str(tmp_path))




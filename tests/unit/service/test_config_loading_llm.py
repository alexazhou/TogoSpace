import json
import os
import sys
import builtins

import pytest
from util import configUtil
from util.configUtil.migrations import LlmServiceType
from util.configTypes import (
    AppConfig,
    LlmProviderConfig,
    SettingConfig,
)

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


def test_runtime_configs_skip_disabled_llm_service(tmp_path):
    (tmp_path / "setting.json").write_text(json.dumps({
            "version": "v2",
        "default_models": {"primary": "mock-model@mock"},
        "llm_providers": [
            {
                "name": "mock",
                "urls": {"openai": "http://127.0.0.1:8888/v1/chat/completions"},
                "api_key": "app-key",
                "type": "openai",
                "models": [{"name": "mock-model", "protocol": "openai"}]
            }
        ],
    }), encoding="utf-8")

    # V13: default 指向已禁用服务时，自动回退到首个可用服务。
    app_config = configUtil.load(str(tmp_path))
    assert app_config.setting.llm_providers[0] is not None
    assert app_config.setting.llm_providers[0].name == "mock"



def test_runtime_configs_allow_llm_only_setting(tmp_path):
    os.environ.pop("TEAMAGENT_DB_PATH", None)
    (tmp_path / "setting.json").write_text(json.dumps({
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
    }), encoding="utf-8")

    app_config = configUtil.load(str(tmp_path))
    llm_cfg = app_config.setting.llm_providers[0]

    assert llm_cfg.name == "mock"
    assert llm_cfg.urls["openai"] == "http://127.0.0.1:7777/v1/chat/completions"
    assert app_config.setting.db_path == "../test_data/data.db"



def test_llm_service_extra_headers_defaults_to_opencode(tmp_path):
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

    app_config = configUtil.load(str(tmp_path), force_reload=True)

    assert app_config.setting.llm_providers[0].extra_headers == {"User-Agent": "opencode"}



def test_llm_service_extra_headers_use_json_value_when_provided(tmp_path):
    (tmp_path / "setting.json").write_text(json.dumps({
            "version": "v2",
        "default_models": {"primary": "mock-model@svc"},
        "llm_providers": [
            {
                "name": "svc",
                "urls": {"openai": "http://localhost/v1"},
                "api_key": "key-123",
                "type": "openai",
                "extra_headers": {
                    "X-Client-Name": "openclaw",
                },
                "models": [{"name": "mock-model", "protocol": "openai"}]
            }
        ],
    }), encoding="utf-8")

    app_config = configUtil.load(str(tmp_path), force_reload=True)

    assert app_config.setting.llm_providers[0].extra_headers == {"X-Client-Name": "openclaw"}



def test_llm_service_provider_params_use_json_value_when_provided(tmp_path):
    (tmp_path / "setting.json").write_text(json.dumps({
            "version": "v2",
        "default_models": {"primary": "mock-model@svc"},
        "llm_providers": [
            {
                "name": "svc",
                "urls": {"openai": "http://localhost/v1"},
                "api_key": "key-123",
                "type": "openai",
                "provider_params": {
                    "reasoning_effort": "high",
                },
                "models": [{"name": "mock-model", "protocol": "openai"}]
            }
        ],
    }), encoding="utf-8")

    app_config = configUtil.load(str(tmp_path), force_reload=True)

    assert app_config.setting.llm_providers[0].provider_params == {"reasoning_effort": "high"}



def test_llm_service_provider_params_reject_reserved_keys(tmp_path):
    (tmp_path / "setting.json").write_text(json.dumps({
            "version": "v2",
        "default_models": {"primary": "mock-model@svc"},
        "llm_providers": [
            {
                "name": "svc",
                "urls": {"openai": "http://localhost/v1"},
                "api_key": "key-123",
                "type": "openai",
                "provider_params": {
                    "model": "other-model",
                },
                "models": [{"name": "mock-model", "protocol": "openai"}]
            }
        ],
    }), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        configUtil.load(str(tmp_path), force_reload=True)

    assert "provider_params 包含保留字段" in str(exc_info.value)



def test_empty_llm_services_config_loads_successfully(tmp_path):
    """V13: llm_services 为空时配置可以正常加载，不抛异常。"""
    (tmp_path / "setting.json").write_text(json.dumps({
            "version": "v2",
        "llm_providers": [],
        "default_models": {"primary": ""},
    }), encoding="utf-8")

    app_config = configUtil.load(str(tmp_path), force_reload=True)
    assert app_config.setting.llm_providers == []
    assert app_config.setting.is_llm_configured is False



def test_empty_llm_services_current_llm_service_returns_none(tmp_path):
    """V13: llm_services 为空时 current_llm_service 返回 None。"""
    (tmp_path / "setting.json").write_text(json.dumps({
            "version": "v2",
        "llm_providers": [],
        "default_models": {"primary": ""},
    }), encoding="utf-8")

    app_config = configUtil.load(str(tmp_path), force_reload=True)
    assert len(app_config.setting.llm_providers) == 0



def test_all_disabled_llm_services_loads_successfully(tmp_path):
    """V13: 所有 LLM 服务禁用时配置可以正常加载。"""
    (tmp_path / "setting.json").write_text(json.dumps({
            "version": "v2",
        "llm_providers": [],
        "default_models": {"primary": ""},
    }), encoding="utf-8")

    app_config = configUtil.load(str(tmp_path), force_reload=True)
    assert app_config.setting.is_llm_configured is False
    assert len(app_config.setting.llm_providers) == 0



def test_is_llm_configured_true_with_enabled_service(tmp_path):
    """V13: 至少一个 enable=True 的服务时 is_llm_configured 为 True。"""
    (tmp_path / "setting.json").write_text(json.dumps({
            "version": "v2",
        "llm_providers": [
            {
                "name": "mock",
                "urls": {"openai": "http://localhost/v1"},
                "api_key": "key",
                "type": "openai",
                "models": [{"name": "mock-model", "protocol": "openai"}]
            }
        ],
        "default_models": {"primary": "mock-model@mock"},
    }), encoding="utf-8")

    app_config = configUtil.load(str(tmp_path), force_reload=True)
    print(app_config.setting.llm_providers)

    assert app_config.setting.is_llm_configured is True


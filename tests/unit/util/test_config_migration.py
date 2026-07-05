import json
import os
from unittest import mock
import pytest
from constants import LlmProviderType
from util.configTypes import SettingConfig
from util.configUtil.migrations.v1_to_v2 import migrate_v1_to_v2


def _setup_preset_file(tmp_path):
    """在 tmp_path 下创建 providerDefaultUrls.json 供迁移逻辑使用。"""
    preset_dir = tmp_path / "preset"
    preset_dir.mkdir(exist_ok=True)
    preset_file = preset_dir / "providerDefaultUrls.json"
    preset_data = {
        "openai": {
            "label": "OpenAI",
            "openai": "https://api.openai.com/v1/chat/completions"
        },
        "deepseek": {
            "label": "DeepSeek",
            "openai": "https://api.deepseek.com/v1/chat/completions"
        },
        "aliyun": {
            "label": "Aliyun (通义千问)",
            "openai": "https://dashscope.aliyuncs.com/compatible-mode/v1"
        },
        "other": {
            "label": "Other"
        }
    }
    with open(preset_file, "w", encoding="utf-8") as f:
        json.dump(preset_data, f)
    return str(tmp_path)


def test_v1_to_v2_migration(tmp_path):
    assets_dir = _setup_preset_file(tmp_path)

    # Construct a V1 setting.json
    v1_json = {
        "language": "zh-CN",
        "default_llm_server": "TestOpenAI",
        "llm_services": [
            {
                "name": "TestOpenAI",
                "base_url": "https://api.openai.com/v1/chat/completions",
                "api_key": "sk-xxx",
                "type": "openai",
                "model": "gpt-4o",
                "enable": True,
                "reserve_output_tokens": 8192,
                "context_window_tokens": 128000
            },
            {
                "name": "TestOllama",
                "base_url": "http://localhost:11434/v1",
                "api_key": "test",
                "type": "openai-compatible",
                "model": "llama3",
                "enable": False
            }
        ]
    }

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    setting_path = config_dir / "setting.json"
    with open(setting_path, "w", encoding="utf-8") as f:
        json.dump(v1_json, f)

    with mock.patch("appPaths.ASSETS_DIR", assets_dir):
        from util import configUtil
        setting: SettingConfig = configUtil._load_setting(str(config_dir))

        # Verify migration
        assert setting.version == "v2"
        assert not hasattr(setting, "llm_services")
        assert not hasattr(setting, "default_llm_server")

        assert len(setting.llm_providers) == 2

        # OpenAI URL 匹配预设 -> name 保留原始名, type="openai"
        p1 = setting.llm_providers[0]
        assert p1.name == "TestOpenAI"
        assert p1.type == LlmProviderType.OPENAI
        assert p1.urls["openai"] == "https://api.openai.com/v1/chat/completions"
        assert len(p1.models) == 1
        m1 = p1.models[0]
        assert m1.name == "gpt-4o"
        # reserve output tokens upgraded from 8192 to 16384
        assert m1.context_config.reserve_output_tokens == 16384

        # 本地 Ollama URL 无法匹配任何预设 -> name 保留原始名, type="other"
        p2 = setting.llm_providers[1]
        assert p2.name == "TestOllama"
        assert p2.type == LlmProviderType.OTHER
        assert p2.urls["openai"] == "http://localhost:11434/v1"
        assert len(p2.models) == 1
        m2 = p2.models[0]
        assert m2.name == "llama3"
        assert m2.enabled is False

        # Default model: 通过 default_llm_server name 匹配 -> "gpt-4o@TestOpenAI"
        assert setting.default_models.primary == "gpt-4o@TestOpenAI"


def test_v1_migration_temperature_and_params():
    """迁移时 temperature、provider_params、extra_headers 正确传递。"""

    cfg = {
        "llm_services": [
            {
                "name": "SvcA",
                "base_url": "https://api.openai.com/v1/chat/completions",
                "api_key": "sk-a",
                "type": "openai",
                "model": "gpt-4o",
                "enable": True,
                "temperature": 0.7,
                "provider_params": {"max_retries": 3},
                "extra_headers": {"X-Custom": "val"},
            }
        ]
    }
    migrate_v1_to_v2(cfg)

    p = cfg["llm_providers"][0]
    m = p["models"][0]
    assert m["temperature"] == 0.7
    assert m["provider_params"] == {"max_retries": 3}
    assert p["extra_headers"] == {"X-Custom": "val"}


def test_v1_migration_context_config_defaults():
    """context_config 各字段使用 V1 顶级值或默认值。"""

    cfg = {
        "llm_services": [
            {
                "name": "Svc",
                "base_url": "https://api.deepseek.com/v1/chat/completions",
                "api_key": "sk-ds",
                "type": "deepseek",
                "model": "deepseek-chat",
                "enable": True,
                "compact_trigger_ratio": 0.9,
                "compact_summary_max_tokens": 8192,
            }
        ]
    }
    migrate_v1_to_v2(cfg)

    m = cfg["llm_providers"][0]["models"][0]
    cc = m["context_config"]
    assert cc["compact_trigger_ratio"] == 0.9
    assert cc["compact_summary_max_tokens"] == 8192
    assert cc["context_window_tokens"] == 131072
    assert cc["reserve_output_tokens"] == 16384


def test_v1_migration_openai_compatible_type():
    """openai-compatible 类型转为 openai。"""

    cfg = {
        "llm_services": [
            {
                "name": "Ollama",
                "base_url": "http://localhost:11434/v1",
                "api_key": "",
                "type": "openai-compatible",
                "model": "llama3",
                "enable": True,
            }
        ]
    }
    migrate_v1_to_v2(cfg)

    p = cfg["llm_providers"][0]
    m = p["models"][0]
    assert m["protocol"] == "openai"
    assert p["type"] == "other"  # URL 不匹配预设


def test_v1_migration_empty_services():
    """空 llm_services 迁移后得到空 providers 列表。"""

    cfg = {"llm_services": []}
    migrate_v1_to_v2(cfg)

    assert cfg["llm_providers"] == []
    assert cfg["version"] == "v2"
    assert "llm_services" not in cfg


def test_v1_migration_default_server_no_match():
    """default_llm_server 名称无法匹配任何 provider 时，default_models 不设置。"""

    cfg = {
        "default_llm_server": "NonExistent",
        "llm_services": [
            {
                "name": "RealProvider",
                "base_url": "https://api.openai.com/v1/chat/completions",
                "api_key": "sk-x",
                "type": "openai",
                "model": "gpt-4o",
                "enable": True,
            }
        ]
    }
    migrate_v1_to_v2(cfg)

    assert "default_models" not in cfg


def test_v1_migration_disabled_model():
    """enable=False 的 provider 和 model 正确迁移。"""

    cfg = {
        "llm_services": [
            {
                "name": "Disabled",
                "base_url": "https://api.openai.com/v1/chat/completions",
                "api_key": "sk-x",
                "type": "openai",
                "model": "gpt-4o",
                "enable": False,
            }
        ]
    }
    migrate_v1_to_v2(cfg)

    p = cfg["llm_providers"][0]
    assert p["enable"] is False
    m = p["models"][0]
    assert m["enabled"] is False


def test_v1_migration_url_in_urls_dict():
    """base_url 存入 urls 字典，key 为 provider type。"""

    cfg = {
        "llm_services": [
            {
                "name": "Aliyun",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "api_key": "sk-ali",
                "type": "openai",
                "model": "qwen-plus",
                "enable": True,
            }
        ]
    }
    migrate_v1_to_v2(cfg)

    p = cfg["llm_providers"][0]
    assert p["type"] == "aliyun"  # 匹配预设
    assert "openai" in p["urls"]
    assert p["urls"]["openai"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"

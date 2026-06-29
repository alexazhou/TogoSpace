import json
import os
from unittest import mock
import pytest
from constants import LlmProviderType
from util.configTypes import SettingConfig


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

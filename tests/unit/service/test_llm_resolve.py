import pytest
from unittest import mock
from constants import LlmProtocol
from util.configTypes import AppConfig, SettingConfig, LlmProviderConfig, LlmModelConfig, LlmContextConfig, DefaultModelSlots

def test_resolve_model():
    # Construct a V2 config
    mock_app_config = AppConfig(
        setting=SettingConfig(
            version="v2",
            default_models=DefaultModelSlots(
                primary="gpt-4o@openai",
                lightweight="gpt-4o-mini@openai",
                vision="gpt-4o@openai"
            ),
            llm_providers=[
                LlmProviderConfig(
                    name="openai",
                    type="openai",
                    api_key="sk-xxx",
                    urls={"openai": "https://api.openai.com/v1"},
                    models=[
                        LlmModelConfig(
                            name="gpt-4o",
                            protocol="openai",
                            context_config=LlmContextConfig(reserve_output_tokens=2048)
                        ),
                        LlmModelConfig(
                            name="gpt-4o-mini",
                            protocol="openai"
                        )
                    ]
                )
            ]
        )
    )

    with mock.patch("util.configUtil.get_app_config", return_value=mock_app_config):
        from service.llmService.core import resolve_model, get_provider_url

        # Test 1: Resolve primary slot (agent_model=None)
        provider, model = resolve_model(None)
        assert provider.name == "openai"
        assert model.name == "gpt-4o"
        assert model.protocol == LlmProtocol.OPENAI
        assert model.context_config.reserve_output_tokens == 2048
        assert get_provider_url(provider, model.protocol) == "https://api.openai.com/v1"

        # Test 2: Resolve specific slot
        provider, model = resolve_model("lightweight")
        assert model.name == "gpt-4o-mini"

        # Test 3: Resolve direct model@provider
        provider, model = resolve_model("gpt-4o@openai")
        assert model.name == "gpt-4o"

        # Test 4: Invalid slot
        with pytest.raises(ValueError):
            resolve_model("unknown_slot")

        # Test 5: Invalid provider
        with pytest.raises(ValueError):
            resolve_model("gpt-4o@unknown")


def test_resolve_model_merges_provider_params():
    """resolve_model 应合并 provider 和 model 级别的 provider_params 和 extra_headers。"""
    mock_app_config = AppConfig(
        setting=SettingConfig(
            version="v2",
            default_models=DefaultModelSlots(primary="gpt-4o@openai"),
            llm_providers=[
                LlmProviderConfig(
                    name="openai",
                    type="openai",
                    api_key="sk-xxx",
                    urls={"openai": "https://api.openai.com/v1"},
                    provider_params={"top_p": 0.9, "frequency_penalty": 0.5},
                    extra_headers={"X-Custom": "provider-value"},
                    models=[
                        LlmModelConfig(
                            name="gpt-4o",
                            protocol="openai",
                            provider_params={"top_p": 0.8},  # 应覆盖 provider 级
                            extra_headers={"X-Model": "model-value"},
                        )
                    ]
                )
            ]
        )
    )

    with mock.patch("util.configUtil.get_app_config", return_value=mock_app_config):
        from service.llmService.core import resolve_model

        _, model = resolve_model("gpt-4o@openai")

        # provider_params: model 级 top_p 覆盖 provider 级，frequency_penalty 来自 provider
        assert model.provider_params == {"top_p": 0.8, "frequency_penalty": 0.5}

        # extra_headers: 合并两者
        assert model.extra_headers == {"X-Custom": "provider-value", "X-Model": "model-value"}

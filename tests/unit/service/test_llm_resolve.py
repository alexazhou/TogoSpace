import pytest
from unittest import mock
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
        provider, model, protocol, resolved_model = resolve_model(None)
        assert provider.name == "openai"
        assert model.name == "gpt-4o"
        assert protocol == "openai"
        assert resolved_model == "gpt-4o@openai"
        assert model.context_config.reserve_output_tokens == 2048
        assert get_provider_url(provider, protocol) == "https://api.openai.com/v1"

        # Test 2: Resolve specific slot
        provider, model, protocol, resolved_model = resolve_model("lightweight")
        assert model.name == "gpt-4o-mini"
        assert resolved_model == "gpt-4o-mini@openai"
        
        # Test 3: Resolve direct model@provider
        provider, model, protocol, resolved_model = resolve_model("gpt-4o@openai")
        assert model.name == "gpt-4o"
        assert resolved_model == "gpt-4o@openai"

        # Test 4: Invalid slot
        with pytest.raises(ValueError):
            resolve_model("unknown_slot")
            
        # Test 5: Invalid provider
        with pytest.raises(ValueError):
            resolve_model("gpt-4o@unknown")

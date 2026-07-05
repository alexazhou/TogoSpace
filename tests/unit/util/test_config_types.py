"""tests for SettingConfig / LlmProviderConfig helper methods."""
from util.configTypes import (
    SettingConfig,
    LlmProviderConfig,
    LlmModelConfig,
    DefaultModelSlots,
    LlmContextConfig,
    LlmProtocol,
    LlmProviderType,
)


def _make_provider(name: str, models: list[str], enabled: bool = True) -> LlmProviderConfig:
    return LlmProviderConfig(
        name=name,
        type=LlmProviderType.OPENAI,
        api_key="sk-test",
        enable=enabled,
        models=[LlmModelConfig(name=m, protocol=LlmProtocol.OPENAI) for m in models],
    )


def _make_setting(**kwargs) -> SettingConfig:
    defaults = {
        "version": "v2",
        "default_models": DefaultModelSlots(primary="gpt-4o@openai"),
        "llm_providers": [],
    }
    defaults.update(kwargs)
    return SettingConfig(**defaults)


# ===== LlmProviderConfig.find_model =====


class TestFindModel:
    def test_found(self):
        provider = _make_provider("openai", ["gpt-4o", "gpt-4o-mini"])
        result = provider.find_model("gpt-4o")
        assert result is not None
        assert result.name == "gpt-4o"

    def test_not_found(self):
        provider = _make_provider("openai", ["gpt-4o"])
        result = provider.find_model("unknown")
        assert result is None

    def test_empty_models(self):
        provider = _make_provider("openai", [])
        result = provider.find_model("gpt-4o")
        assert result is None

    def test_disabled_model_still_found(self):
        """find_model 不过滤 disabled，只按名称查找。"""
        provider = LlmProviderConfig(
            name="openai",
            type=LlmProviderType.OPENAI,
            api_key="sk-test",
            models=[LlmModelConfig(name="gpt-4o", protocol=LlmProtocol.OPENAI, enabled=False)],
        )
        result = provider.find_model("gpt-4o")
        assert result is not None
        assert result.enabled is False


# ===== SettingConfig.find_provider =====


class TestFindProvider:
    def test_found(self):
        setting = _make_setting(llm_providers=[
            _make_provider("openai", ["gpt-4o"]),
            _make_provider("deepseek", ["deepseek-chat"]),
        ])
        result = setting.find_provider("deepseek")
        assert result is not None
        assert result.name == "deepseek"

    def test_not_found(self):
        setting = _make_setting(llm_providers=[_make_provider("openai", ["gpt-4o"])])
        result = setting.find_provider("unknown")
        assert result is None

    def test_empty_providers(self):
        setting = _make_setting(llm_providers=[])
        result = setting.find_provider("openai")
        assert result is None

    def test_disabled_provider_still_found(self):
        """find_provider 不过滤 disabled，只按名称查找。"""
        setting = _make_setting(llm_providers=[
            _make_provider("openai", ["gpt-4o"], enabled=False),
        ])
        result = setting.find_provider("openai")
        assert result is not None
        assert result.enable is False


# ===== SettingConfig.get_slot_model_name =====


class TestGetSlotModelName:
    def test_primary(self):
        setting = _make_setting(
            default_models=DefaultModelSlots(primary="gpt-4o@openai", lite="mini@openai"),
        )
        assert setting.get_slot_model_name("primary") == "gpt-4o@openai"

    def test_lite(self):
        setting = _make_setting(
            default_models=DefaultModelSlots(lite="mini@openai"),
        )
        assert setting.get_slot_model_name("lite") == "mini@openai"

    def test_vision(self):
        setting = _make_setting(
            default_models=DefaultModelSlots(vision="vision-model@openai"),
        )
        assert setting.get_slot_model_name("vision") == "vision-model@openai"

    def test_advanced(self):
        setting = _make_setting(
            default_models=DefaultModelSlots(advanced="pro@openai"),
        )
        assert setting.get_slot_model_name("advanced") == "pro@openai"

    def test_empty_slot(self):
        setting = _make_setting(default_models=DefaultModelSlots())
        assert setting.get_slot_model_name("primary") == ""

    def test_unknown_slot(self):
        setting = _make_setting(default_models=DefaultModelSlots(primary="gpt-4o@openai"))
        assert setting.get_slot_model_name("unknown") == ""

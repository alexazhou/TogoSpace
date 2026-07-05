import pytest
from controller.settingController import validate_model_slot_refs
from util.configTypes import LlmProviderConfig, LlmModelConfig, DefaultModelSlots, LlmProtocol


def _make_provider(name: str, models: list[str]) -> LlmProviderConfig:
    return LlmProviderConfig(
        name=name,
        type="openai",
        api_key="test-key",
        models=[LlmModelConfig(name=m, protocol=LlmProtocol.OPENAI) for m in models],
    )


class TestValidateModelSlotRefs:
    """validate_model_slot_refs 单元测试。"""

    def test_all_empty_slots_pass(self):
        """所有槽位为空时通过。"""
        providers = [_make_provider("p1", ["gpt-4o"])]
        slots = DefaultModelSlots()
        assert validate_model_slot_refs(providers, slots) is None

    def test_valid_primary_slot(self):
        """主模型引用有效时通过。"""
        providers = [_make_provider("default", ["gpt-4o"])]
        slots = DefaultModelSlots(primary="gpt-4o@default")
        assert validate_model_slot_refs(providers, slots) is None

    def test_valid_all_slots(self):
        """所有槽位引用有效时通过。"""
        providers = [
            _make_provider("prov_a", ["gpt-4o", "gpt-4o-mini"]),
            _make_provider("prov_b", ["claude-3"]),
        ]
        slots = DefaultModelSlots(
            primary="gpt-4o@prov_a",
            lite="gpt-4o-mini@prov_a",
            advanced="claude-3@prov_b",
            vision="gpt-4o@prov_a",
        )
        assert validate_model_slot_refs(providers, slots) is None

    def test_invalid_primary_slot(self):
        """主模型引用不存在的模型时报错。"""
        providers = [_make_provider("default", ["gpt-4o"])]
        slots = DefaultModelSlots(primary="nonexistent@default")
        result = validate_model_slot_refs(providers, slots)
        assert result is not None
        assert result["error_code"] == "invalid_model_ref"
        assert "主模型" in result["error_desc"]
        assert "nonexistent@default" in result["error_desc"]

    def test_invalid_lite_slot(self):
        """轻量模型引用不存在时报错。"""
        providers = [_make_provider("default", ["gpt-4o"])]
        slots = DefaultModelSlots(lite="mini@default")
        result = validate_model_slot_refs(providers, slots)
        assert result is not None
        assert "轻量模型" in result["error_desc"]

    def test_invalid_advanced_slot(self):
        """高级模型引用不存在时报错。"""
        providers = [_make_provider("default", ["gpt-4o"])]
        slots = DefaultModelSlots(advanced="unknown@default")
        result = validate_model_slot_refs(providers, slots)
        assert result is not None
        assert "高级模型" in result["error_desc"]

    def test_invalid_vision_slot(self):
        """视觉模型引用不存在时报错。"""
        providers = [_make_provider("default", ["gpt-4o"])]
        slots = DefaultModelSlots(vision="vision-model@default")
        result = validate_model_slot_refs(providers, slots)
        assert result is not None
        assert "视觉模型" in result["error_desc"]

    def test_wrong_provider_name(self):
        """模型名正确但服务商名错误时报错。"""
        providers = [_make_provider("real_provider", ["gpt-4o"])]
        slots = DefaultModelSlots(primary="gpt-4o@wrong_provider")
        result = validate_model_slot_refs(providers, slots)
        assert result is not None
        assert "invalid_model_ref" in result["error_code"]

    def test_empty_providers_all_slots_filled(self):
        """无服务商但槽位有引用时报错。"""
        slots = DefaultModelSlots(primary="gpt-4o@default")
        result = validate_model_slot_refs([], slots)
        assert result is not None
        assert "主模型" in result["error_desc"]

    def test_provider_without_models(self):
        """服务商无模型时，槽位引用报错。"""
        providers = [LlmProviderConfig(
            name="empty_provider",
            type="openai",
            api_key="test",
            models=[],
        )]
        slots = DefaultModelSlots(primary="gpt-4o@empty_provider")
        result = validate_model_slot_refs(providers, slots)
        assert result is not None

    def test_multiple_invalid_slots_reports_first(self):
        """多个槽位无效时，返回第一个错误。"""
        providers = [_make_provider("default", ["gpt-4o"])]
        slots = DefaultModelSlots(
            primary="a@default",
            lite="b@default",
        )
        result = validate_model_slot_refs(providers, slots)
        assert result is not None
        assert "主模型" in result["error_desc"]

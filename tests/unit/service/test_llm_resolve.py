"""tests for resolve_model."""
import pytest
from unittest import mock
from constants import LlmProtocol
from util.configTypes import (
    AppConfig, SettingConfig, LlmProviderConfig, LlmModelConfig,
    LlmContextConfig, DefaultModelSlots,
)


def _make_app_config(
    global_ctx: LlmContextConfig | None = None,
    model_ctx: LlmContextConfig | None = None,
    model_enabled: bool = True,
    provider_enabled: bool = True,
) -> AppConfig:
    """构造用于 resolve_model 测试的 AppConfig。"""
    return AppConfig(
        setting=SettingConfig(
            version="v2",
            context_config=global_ctx or LlmContextConfig(),
            default_models=DefaultModelSlots(primary="gpt-4o@openai"),
            llm_providers=[
                LlmProviderConfig(
                    name="openai",
                    type="openai",
                    api_key="sk-test",
                    enable=provider_enabled,
                    models=[
                        LlmModelConfig(
                            name="gpt-4o",
                            protocol="openai",
                            enabled=model_enabled,
                            context_config=model_ctx,
                        ),
                    ],
                ),
            ],
        )
    )


# ===== 基础解析 =====


class TestResolveModelBasic:
    def test_resolve_none_uses_primary_slot(self):
        cfg = _make_app_config()
        with mock.patch("util.configUtil.get_app_config", return_value=cfg):
            from service.llmService.core import resolve_model
            provider, model = resolve_model(None)
            assert provider.name == "openai"
            assert model.name == "gpt-4o"

    def test_resolve_system_slot(self):
        cfg = _make_app_config()
        cfg.setting.default_models.lite = "gpt-4o@openai"
        with mock.patch("util.configUtil.get_app_config", return_value=cfg):
            from service.llmService.core import resolve_model
            provider, model = resolve_model("lite@system")
            assert model.name == "gpt-4o"

    def test_resolve_direct_model_at_provider(self):
        cfg = _make_app_config()
        with mock.patch("util.configUtil.get_app_config", return_value=cfg):
            from service.llmService.core import resolve_model
            provider, model = resolve_model("gpt-4o@openai")
            assert model.name == "gpt-4o"
            assert provider.name == "openai"

    def test_invalid_format_no_at(self):
        cfg = _make_app_config()
        with mock.patch("util.configUtil.get_app_config", return_value=cfg):
            from service.llmService.core import resolve_model
            with pytest.raises(ValueError, match="格式错误"):
                resolve_model("gpt-4o")

    def test_invalid_system_slot(self):
        cfg = _make_app_config()
        with mock.patch("util.configUtil.get_app_config", return_value=cfg):
            from service.llmService.core import resolve_model
            with pytest.raises(ValueError, match="未配置有效的系统槽位"):
                resolve_model("unknown@system")

    def test_provider_not_found(self):
        cfg = _make_app_config()
        with mock.patch("util.configUtil.get_app_config", return_value=cfg):
            from service.llmService.core import resolve_model
            with pytest.raises(ValueError, match="找不到提供商"):
                resolve_model("gpt-4o@unknown")

    def test_provider_disabled(self):
        cfg = _make_app_config(provider_enabled=False)
        with mock.patch("util.configUtil.get_app_config", return_value=cfg):
            from service.llmService.core import resolve_model
            with pytest.raises(ValueError, match="已禁用"):
                resolve_model("gpt-4o@openai")

    def test_model_not_found(self):
        cfg = _make_app_config()
        with mock.patch("util.configUtil.get_app_config", return_value=cfg):
            from service.llmService.core import resolve_model
            with pytest.raises(ValueError, match="找不到模型"):
                resolve_model("unknown@openai")

    def test_model_disabled(self):
        cfg = _make_app_config(model_enabled=False)
        with mock.patch("util.configUtil.get_app_config", return_value=cfg):
            from service.llmService.core import resolve_model
            with pytest.raises(ValueError, match="已禁用"):
                resolve_model("gpt-4o@openai")


# ===== context_config 合并逻辑 =====


class TestContextConfigMerge:
    """context_config 合并优先级：模型 > 全局 > 默认值。"""

    def test_model_ctx_overrides_global(self):
        """模型配了 reserve_output_tokens=4096，全局配了 16384 → 用模型的 4096。"""
        cfg = _make_app_config(
            global_ctx=LlmContextConfig(reserve_output_tokens=16384),
            model_ctx=LlmContextConfig(reserve_output_tokens=4096),
        )
        with mock.patch("util.configUtil.get_app_config", return_value=cfg):
            from service.llmService.core import resolve_model
            _, model = resolve_model("gpt-4o@openai")
            assert model.context_config.reserve_output_tokens == 4096

    def test_global_ctx_used_when_model_none(self):
        """模型没配 context_config，全局配了 reserve_output_tokens=8192 → 用全局的。"""
        cfg = _make_app_config(
            global_ctx=LlmContextConfig(reserve_output_tokens=8192),
            model_ctx=None,
        )
        with mock.patch("util.configUtil.get_app_config", return_value=cfg):
            from service.llmService.core import resolve_model
            _, model = resolve_model("gpt-4o@openai")
            assert model.context_config.reserve_output_tokens == 8192

    def test_default_used_when_neither_set(self):
        """模型和全局都没配 → 用默认值 16384。"""
        cfg = _make_app_config(
            global_ctx=LlmContextConfig(),  # 全部默认
            model_ctx=None,
        )
        with mock.patch("util.configUtil.get_app_config", return_value=cfg):
            from service.llmService.core import resolve_model
            _, model = resolve_model("gpt-4o@openai")
            assert model.context_config.reserve_output_tokens == 16384
            assert model.context_config.compact_trigger_ratio == 0.85

    def test_field_level_merge(self):
        """逐字段合并：模型设了 A，全局设了 B，各自取各自的值。"""
        cfg = _make_app_config(
            global_ctx=LlmContextConfig(
                context_window_tokens=65536,
                compact_trigger_ratio=0.9,
            ),
            model_ctx=LlmContextConfig(
                reserve_output_tokens=4096,
            ),
        )
        with mock.patch("util.configUtil.get_app_config", return_value=cfg):
            from service.llmService.core import resolve_model
            _, model = resolve_model("gpt-4o@openai")
            # 模型设了 reserve_output_tokens → 4096
            assert model.context_config.reserve_output_tokens == 4096
            # 全局设了 context_window_tokens → 65536
            assert model.context_config.context_window_tokens == 65536
            # 全局设了 compact_trigger_ratio → 0.9
            assert model.context_config.compact_trigger_ratio == 0.9
            # 都没设 compact_summary_max_tokens → 默认 6144
            assert model.context_config.compact_summary_max_tokens == 6144


# ===== extra_params / extra_headers 保留 =====


class TestModelParams:
    def test_preserves_extra_params(self):
        cfg = _make_app_config()
        cfg.setting.llm_providers[0].models[0].extra_params = {
            "top_p": 0.8,
            "reasoning_effort": "high",
        }
        with mock.patch("util.configUtil.get_app_config", return_value=cfg):
            from service.llmService.core import resolve_model
            _, model = resolve_model("gpt-4o@openai")
            assert model.extra_params == {"top_p": 0.8, "reasoning_effort": "high"}

    def test_preserves_extra_headers(self):
        cfg = _make_app_config()
        cfg.setting.llm_providers[0].models[0].extra_headers = {
            "X-Custom": "value",
        }
        with mock.patch("util.configUtil.get_app_config", return_value=cfg):
            from service.llmService.core import resolve_model
            _, model = resolve_model("gpt-4o@openai")
            assert model.extra_headers == {"X-Custom": "value"}

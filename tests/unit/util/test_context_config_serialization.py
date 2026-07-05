"""测试 context_config 序列化：默认值应被排除，自定义值应保留。"""
import pytest
from constants import LlmProtocol
from util.configTypes import LlmModelConfig, LlmContextConfig


class TestLlmModelConfigContextConfigSerialization:
    """LlmModelConfig 序列化时 context_config 的处理。"""

    def test_no_context_config_excluded(self):
        """未配置 context_config 时，序列化结果不应包含该字段。"""
        m = LlmModelConfig(name="test", protocol=LlmProtocol.OPENAI)
        data = m.model_dump(mode="json")
        assert "context_config" not in data

    def test_default_context_config_excluded(self):
        """context_config 全为默认值时，序列化结果不应包含该字段。"""
        m = LlmModelConfig(
            name="test",
            protocol=LlmProtocol.OPENAI,
            context_config=LlmContextConfig(),
        )
        data = m.model_dump(mode="json")
        assert "context_config" not in data

    def test_custom_context_config_preserved(self):
        """context_config 有自定义值时，序列化结果应包含该字段。"""
        m = LlmModelConfig(
            name="test",
            protocol=LlmProtocol.OPENAI,
            context_config=LlmContextConfig(context_window_tokens=32000),
        )
        data = m.model_dump(mode="json")
        assert "context_config" in data
        assert data["context_config"]["context_window_tokens"] == 32000

    def test_partial_custom_context_config_preserved(self):
        """context_config 部分自定义时，序列化结果应包含该字段。"""
        m = LlmModelConfig(
            name="test",
            protocol=LlmProtocol.OPENAI,
            context_config=LlmContextConfig(compact_trigger_ratio=0.9),
        )
        data = m.model_dump(mode="json")
        assert "context_config" in data
        assert data["context_config"]["compact_trigger_ratio"] == 0.9


class TestLlmContextConfigExcludeDefaults:
    """LlmContextConfig 使用 exclude_defaults 的效果。"""

    def test_all_defaults_returns_empty(self):
        """全默认值 + exclude_defaults → 空字典。"""
        cc = LlmContextConfig()
        data = cc.model_dump(exclude_defaults=True, mode="json")
        assert data == {}

    def test_custom_value_retained(self):
        """自定义值 + exclude_defaults → 只包含非默认字段。"""
        cc = LlmContextConfig(context_window_tokens=32000)
        data = cc.model_dump(exclude_defaults=True, mode="json")
        assert data == {"context_window_tokens": 32000}

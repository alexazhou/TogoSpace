"""单元测试：鉴权配置和 BaseHandler 鉴权检查逻辑。"""
import unittest.mock as mock

from util.configTypes import AuthConfig, SettingConfig
from util import configUtil


class TestAuthConfig:
    """测试 AuthConfig 配置模型。"""

    def test_default_disabled(self):
        """默认应禁用鉴权。"""
        config = AuthConfig()
        assert config.enabled is False
        assert config.token == ""

    def test_enabled_with_token(self):
        """启用时应有 token。"""
        config = AuthConfig(enabled=True, token="my_token")
        assert config.enabled is True
        assert config.token == "my_token"


class TestSettingConfigAuth:
    """测试 SettingConfig 中的 auth 字段。"""

    def test_default_auth_disabled(self):
        """SettingConfig 默认应包含禁用的 auth。"""
        config = SettingConfig()
        assert config.auth.enabled is False
        assert config.auth.token == ""

    def test_auth_from_dict(self):
        """从字典加载时应正确解析 auth。"""
        data = {
            "auth": {
                "enabled": True,
                "token": "test_token",
            }
        }
        config = SettingConfig.model_validate(data)
        assert config.auth.enabled is True
        assert config.auth.token == "test_token"
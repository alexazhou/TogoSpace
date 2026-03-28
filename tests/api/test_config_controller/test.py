import os
import sys

import aiohttp

from ...base import ServiceTestCase

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


class _ApiServiceCase(ServiceTestCase):
    """API 测试基类"""


class TestConfigController(_ApiServiceCase):
    requires_backend = True
    requires_mock_llm = True

    async def test_get_config(self):
        """验证 GET /config/frontend.json 返回前端所需配置。"""
        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/config/frontend.json") as resp:
                assert resp.status == 200
                data = await resp.json()

        assert "models" in data
        assert "driver_types" in data
        assert "default_model" in data

        # 验证 driver_types 结构
        assert len(data["driver_types"]) >= 1
        driver = data["driver_types"][0]
        assert "name" in driver
        assert "description" in driver

        # 验证 models 结构
        assert len(data["models"]) >= 1
        model = data["models"][0]
        assert "name" in model
        assert "model" in model
        assert "enabled" in model
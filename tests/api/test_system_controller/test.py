import os
import sys

import aiohttp

from ...base import ServiceTestCase

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

_CONFIG_DIR = os.path.join(os.path.dirname(__file__), "config")
_SETTING_PATH = os.path.join(_CONFIG_DIR, "setting.json")


class _ApiServiceCase(ServiceTestCase):
    """V13 System Status API 测试基类"""
    use_custom_config = True
    _original_setting: str = None

    @classmethod
    def setup_class(cls) -> None:
        with open(_SETTING_PATH, "r", encoding="utf-8") as f:
            cls._original_setting = f.read()
        super().setup_class()

    @classmethod
    def teardown_class(cls) -> None:
        super().teardown_class()
        if cls._original_setting is not None:
            with open(_SETTING_PATH, "w", encoding="utf-8") as f:
                f.write(cls._original_setting)


class TestSystemStatus(_ApiServiceCase):
    """系统状态接口测试 — 使用已配置的 setting.json（一个已启用的 mock 服务）。"""
    requires_backend = True

    # ──────── helpers ────────

    async def _status(self, client: aiohttp.ClientSession) -> dict:
        async with client.get(f"{self.backend_base_url}/system/status.json") as resp:
            assert resp.status == 200
            return await resp.json()

    # ──────── tests ────────

    async def test_status_initialized_true(self):
        """有已启用服务时返回 initialized: true。"""
        async with aiohttp.ClientSession() as client:
            data = await self._status(client)

        assert data["initialized"] is True
        assert "default_llm_server" in data
        assert data["default_llm_server"] == "mock"

    async def test_status_returns_default_llm_server(self):
        """已初始化时返回 default_llm_server 字段。"""
        async with aiohttp.ClientSession() as client:
            data = await self._status(client)

        assert data["initialized"] is True
        assert data["default_llm_server"] == "mock"
        # 未初始化时的 message 字段不应出现
        assert "message" not in data

    async def test_status_no_message_when_initialized(self):
        """已初始化时不返回 message 字段。"""
        async with aiohttp.ClientSession() as client:
            data = await self._status(client)

        assert data["initialized"] is True
        assert "message" not in data

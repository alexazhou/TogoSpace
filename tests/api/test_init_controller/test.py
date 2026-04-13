import os
import sys

import aiohttp

from ...base import ServiceTestCase

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

_CONFIG_DIR = os.path.join(os.path.dirname(__file__), "config")
_SETTING_PATH = os.path.join(_CONFIG_DIR, "setting.json")


class _ApiServiceCase(ServiceTestCase):
    """V13 Quick Init API 测试基类"""
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


class TestQuickInit(_ApiServiceCase):
    """快速初始化接口测试 — 使用空 setting.json 起步。"""
    requires_backend = True

    # ──────── helpers ────────

    async def _status(self, client: aiohttp.ClientSession) -> dict:
        async with client.get(f"{self.backend_base_url}/system/status.json") as resp:
            assert resp.status == 200
            return await resp.json()

    async def _quick_init(self, client: aiohttp.ClientSession, payload: dict, expect_ok: bool = True):
        async with client.post(
            f"{self.backend_base_url}/config/quick_init.json",
            json=payload,
        ) as resp:
            data = await resp.json()
            if expect_ok:
                assert resp.status == 200, f"quick_init failed: {data}"
            return resp.status, data

    async def _list_services(self, client: aiohttp.ClientSession) -> dict:
        async with client.get(f"{self.backend_base_url}/config/llm_services/list.json") as resp:
            assert resp.status == 200
            return await resp.json()

    # ──────── tests ────────

    async def test_status_initialized_false_when_empty(self):
        """空 llm_services 时系统状态应返回 initialized: false 和 schedule_state。"""
        async with aiohttp.ClientSession() as client:
            data = await self._status(client)

        assert data["initialized"] is False
        assert "message" in data
        assert data["message"] == "当前未配置大模型服务"
        assert "schedule_state" in data

    async def test_quick_init_success(self):
        """快速初始化成功保存配置。"""
        async with aiohttp.ClientSession() as client:
            status_code, data = await self._quick_init(client, {
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-test-key-123",
                "model": "test-model",
            })

        assert status_code == 200
        assert data["status"] == "ok"
        assert data["message"] == "配置保存成功"
        assert data["detail"]["name"] == "default"
        assert data["detail"]["model"] == "test-model"

    async def test_quick_init_adds_service(self):
        """快速初始化后 llm_services 包含新服务。"""
        async with aiohttp.ClientSession() as client:
            # 执行初始化
            await self._quick_init(client, {
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-test-key-456",
                "model": "test-model-2",
            })

            # 验证服务列表
            list_data = await self._list_services(client)

        services = list_data["llm_services"]
        names = [s["name"] for s in services]
        assert "default" in names

        default_svc = next(s for s in services if s["name"] == "default")
        assert default_svc["base_url"] == "https://api.example.com/v1"
        assert default_svc["model"] == "test-model-2"
        assert default_svc["type"] == "openai-compatible"
        assert default_svc["enable"] is True

    async def test_quick_init_sets_default(self):
        """快速初始化后 default_llm_server 设为 'default'。"""
        async with aiohttp.ClientSession() as client:
            await self._quick_init(client, {
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-test-key-789",
                "model": "test-model-3",
            })

            list_data = await self._list_services(client)

        assert list_data["default_llm_server"] == "default"

    async def test_quick_init_updates_initialized(self):
        """快速初始化后系统状态变为 initialized: true，调度状态变为 RUNNING。"""
        async with aiohttp.ClientSession() as client:
            # 执行初始化
            await self._quick_init(client, {
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-init-test",
                "model": "init-model",
            })

            # 初始化后应为 true
            after = await self._status(client)

        assert after["initialized"] is True
        assert after["default_llm_server"] == "default"
        assert after["schedule_state"] == "RUNNING"

    async def test_quick_init_invalid_url(self):
        """URL 格式错误返回 400。"""
        async with aiohttp.ClientSession() as client:
            status_code, data = await self._quick_init(client, {
                "base_url": "not-a-url",
                "api_key": "sk-test",
                "model": "model",
            }, expect_ok=False)

        assert status_code == 400
        assert data["error_code"] == "validation_error"

    async def test_quick_init_empty_url(self):
        """空 URL 返回 400。"""
        async with aiohttp.ClientSession() as client:
            status_code, data = await self._quick_init(client, {
                "base_url": "",
                "api_key": "sk-test",
                "model": "model",
            }, expect_ok=False)

        assert status_code == 400
        assert data["error_code"] == "validation_error"

    async def test_quick_init_empty_api_key(self):
        """空 API Key 返回 400。"""
        async with aiohttp.ClientSession() as client:
            status_code, data = await self._quick_init(client, {
                "base_url": "https://api.example.com/v1",
                "api_key": "",
                "model": "model",
            }, expect_ok=False)

        assert status_code == 400
        assert data["error_code"] == "validation_error"

    async def test_quick_init_empty_model(self):
        """空模型名称返回 400。"""
        async with aiohttp.ClientSession() as client:
            status_code, data = await self._quick_init(client, {
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-test",
                "model": "",
            }, expect_ok=False)

        assert status_code == 400
        assert data["error_code"] == "validation_error"

    async def test_quick_init_missing_fields(self):
        """缺少必填字段返回 400。"""
        async with aiohttp.ClientSession() as client:
            # 缺少 model
            status_code, data = await self._quick_init(client, {
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-test",
            }, expect_ok=False)

        assert status_code == 400
        assert data["error_code"] == "validation_error"

    async def test_quick_init_replaces_existing_default(self):
        """重复初始化时替换已有的 default 服务。"""
        async with aiohttp.ClientSession() as client:
            # 第一次初始化
            await self._quick_init(client, {
                "base_url": "https://api.first.com/v1",
                "api_key": "sk-first",
                "model": "first-model",
            })

            # 第二次初始化（覆盖）
            await self._quick_init(client, {
                "base_url": "https://api.second.com/v1",
                "api_key": "sk-second",
                "model": "second-model",
            })

            list_data = await self._list_services(client)

        # 应只有一个 default 服务
        defaults = [s for s in list_data["llm_services"] if s["name"] == "default"]
        assert len(defaults) == 1
        assert defaults[0]["base_url"] == "https://api.second.com/v1"
        assert defaults[0]["model"] == "second-model"

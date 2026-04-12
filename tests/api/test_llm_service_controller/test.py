import json
import os
import sys

import aiohttp

from ...base import ServiceTestCase
from ...mock_llm_server import get_mock_llm_api_url

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

_CONFIG_DIR = os.path.join(os.path.dirname(__file__), "config")
_SETTING_PATH = os.path.join(_CONFIG_DIR, "setting.json")


class _ApiServiceCase(ServiceTestCase):
    """V12 LLM Service Config API 测试基类"""
    use_custom_config = True
    _original_setting: str = None

    @classmethod
    def setup_class(cls) -> None:
        # 备份 setting.json 原始内容（在 super 启动后端子进程之前）
        with open(_SETTING_PATH, "r", encoding="utf-8") as f:
            cls._original_setting = f.read()
        super().setup_class()

    @classmethod
    def teardown_class(cls) -> None:
        super().teardown_class()
        # 恢复 setting.json 原始内容
        if cls._original_setting is not None:
            with open(_SETTING_PATH, "w", encoding="utf-8") as f:
                f.write(cls._original_setting)


class TestLlmServiceController(_ApiServiceCase):
    requires_backend = True
    requires_mock_llm = True

    # ──────── helpers ────────

    def _mock_api_url(self) -> str:
        assert self.mock_llm_server is not None
        return get_mock_llm_api_url(port=self.mock_llm_server.port)

    async def _list(self, client: aiohttp.ClientSession) -> dict:
        async with client.get(f"{self.backend_base_url}/config/llm_services/list.json") as resp:
            assert resp.status == 200
            return await resp.json()

    async def _create(self, client: aiohttp.ClientSession, payload: dict, expect_ok: bool = True):
        async with client.post(
            f"{self.backend_base_url}/config/llm_services/create.json",
            json=payload,
        ) as resp:
            data = await resp.json()
            if expect_ok:
                assert resp.status == 200, f"create failed: {data}"
            return resp.status, data

    async def _modify(self, client: aiohttp.ClientSession, index: int, payload: dict, expect_ok: bool = True):
        async with client.post(
            f"{self.backend_base_url}/config/llm_services/{index}/modify.json",
            json=payload,
        ) as resp:
            data = await resp.json()
            if expect_ok:
                assert resp.status == 200, f"modify failed: {data}"
            return resp.status, data

    async def _delete(self, client: aiohttp.ClientSession, index: int, expect_ok: bool = True):
        async with client.post(
            f"{self.backend_base_url}/config/llm_services/{index}/delete.json",
            json={},
        ) as resp:
            data = await resp.json()
            if expect_ok:
                assert resp.status == 200, f"delete failed: {data}"
            return resp.status, data

    async def _set_default(self, client: aiohttp.ClientSession, index: int, expect_ok: bool = True):
        async with client.post(
            f"{self.backend_base_url}/config/llm_services/{index}/set_default.json",
            json={},
        ) as resp:
            data = await resp.json()
            if expect_ok:
                assert resp.status == 200, f"set_default failed: {data}"
            return resp.status, data

    async def _test_connectivity(self, client: aiohttp.ClientSession, payload: dict):
        async with client.post(
            f"{self.backend_base_url}/config/llm_services/test.json",
            json=payload,
        ) as resp:
            assert resp.status == 200
            return await resp.json()

    # ──────── test cases ────────

    async def test_list_llm_services(self):
        """列表接口返回初始配置（含 mock 服务）"""
        async with aiohttp.ClientSession() as client:
            data = await self._list(client)

        assert "llm_services" in data
        assert "default_llm_server" in data
        assert len(data["llm_services"]) >= 1
        assert data["default_llm_server"] == "mock"

        mock_svc = data["llm_services"][0]
        assert mock_svc["name"] == "mock"

    async def test_create_llm_service(self):
        """新增服务并验证列表更新"""
        async with aiohttp.ClientSession() as client:
            before = await self._list(client)
            before_count = len(before["llm_services"])

            status, data = await self._create(client, {
                "name": "test-new",
                "base_url": "http://127.0.0.1:19876/v1",
                "api_key": "test-key",
                "type": "openai-compatible",
                "model": "test-model",
            })
            assert status == 200
            assert data["status"] == "ok"

            after = await self._list(client)
            assert len(after["llm_services"]) == before_count + 1
            new_svc = after["llm_services"][-1]
            assert new_svc["name"] == "test-new"
            assert new_svc["model"] == "test-model"

    async def test_create_duplicate_name(self):
        """重复名称创建返回 400"""
        async with aiohttp.ClientSession() as client:
            status, data = await self._create(client, {
                "name": "mock",
                "base_url": "http://127.0.0.1:19876/v1",
                "api_key": "test-key",
                "type": "openai-compatible",
                "model": "test-model",
            }, expect_ok=False)
            assert status == 400
            assert "name_duplicate" == data.get("error_code") or "已存在" in data.get("error_desc", "")

    async def test_create_invalid_fields(self):
        """缺少必填字段 / URL 格式错误返回 400"""
        async with aiohttp.ClientSession() as client:
            # 缺少 base_url
            status, data = await self._create(client, {
                "name": "bad-service",
                "api_key": "test-key",
                "type": "openai-compatible",
                "model": "test-model",
            }, expect_ok=False)
            assert status == 400

            # URL 格式错误
            status, data = await self._create(client, {
                "name": "bad-url",
                "base_url": "ftp://invalid",
                "api_key": "test-key",
                "type": "openai-compatible",
                "model": "test-model",
            }, expect_ok=False)
            assert status == 400

    async def test_modify_llm_service(self):
        """通过数组序号修改服务字段并验证生效"""
        async with aiohttp.ClientSession() as client:
            # 先创建一个可修改的服务
            await self._create(client, {
                "name": "to-modify",
                "base_url": "http://127.0.0.1:19876/v1",
                "api_key": "old-key",
                "type": "openai-compatible",
                "model": "old-model",
            })

            listing = await self._list(client)
            idx = len(listing["llm_services"]) - 1

            status, _ = await self._modify(client, idx, {"model": "new-model"})
            assert status == 200

            after = await self._list(client)
            assert after["llm_services"][idx]["model"] == "new-model"

    async def test_modify_invalid_index(self):
        """序号越界返回 400"""
        async with aiohttp.ClientSession() as client:
            status, data = await self._modify(client, 9999, {"model": "x"}, expect_ok=False)
            assert status == 400
            assert "index_out_of_range" == data.get("error_code") or "越界" in data.get("error_desc", "")

    async def test_delete_llm_service(self):
        """通过数组序号删除非默认服务并验证列表更新"""
        async with aiohttp.ClientSession() as client:
            await self._create(client, {
                "name": "to-delete",
                "base_url": "http://127.0.0.1:19876/v1",
                "api_key": "key",
                "type": "openai-compatible",
                "model": "model",
            })

            listing = await self._list(client)
            before_count = len(listing["llm_services"])
            idx = before_count - 1

            status, data = await self._delete(client, idx)
            assert status == 200
            assert data["deleted_name"] == "to-delete"

            after = await self._list(client)
            assert len(after["llm_services"]) == before_count - 1

    async def test_delete_default_service(self):
        """删除默认服务返回 400"""
        async with aiohttp.ClientSession() as client:
            listing = await self._list(client)
            default_name = listing["default_llm_server"]
            default_idx = next(
                i for i, s in enumerate(listing["llm_services"]) if s["name"] == default_name
            )

            status, data = await self._delete(client, default_idx, expect_ok=False)
            assert status == 400

    async def test_set_default(self):
        """切换默认服务并验证"""
        async with aiohttp.ClientSession() as client:
            await self._create(client, {
                "name": "new-default",
                "base_url": "http://127.0.0.1:19876/v1",
                "api_key": "key",
                "type": "openai-compatible",
                "model": "model",
                "enable": True,
            })

            listing = await self._list(client)
            idx = len(listing["llm_services"]) - 1

            status, data = await self._set_default(client, idx)
            assert status == 200
            assert data["default_llm_server"] == "new-default"

            after = await self._list(client)
            assert after["default_llm_server"] == "new-default"

    async def test_set_default_disabled(self):
        """将禁用的服务设为默认返回 400"""
        async with aiohttp.ClientSession() as client:
            await self._create(client, {
                "name": "disabled-svc",
                "base_url": "http://127.0.0.1:19876/v1",
                "api_key": "key",
                "type": "openai-compatible",
                "model": "model",
                "enable": False,
            })

            listing = await self._list(client)
            idx = len(listing["llm_services"]) - 1

            status, data = await self._set_default(client, idx, expect_ok=False)
            assert status == 400

    async def test_disable_default_service(self):
        """通过 modify 禁用默认服务返回 400"""
        async with aiohttp.ClientSession() as client:
            listing = await self._list(client)
            default_name = listing["default_llm_server"]
            default_idx = next(
                i for i, s in enumerate(listing["llm_services"]) if s["name"] == default_name
            )

            status, data = await self._modify(
                client, default_idx, {"enable": False}, expect_ok=False
            )
            assert status == 400

    async def test_set_enabled_via_modify(self):
        """通过 modify 启用/禁用非默认服务"""
        async with aiohttp.ClientSession() as client:
            await self._create(client, {
                "name": "toggle-svc",
                "base_url": "http://127.0.0.1:19876/v1",
                "api_key": "key",
                "type": "openai-compatible",
                "model": "model",
                "enable": True,
            })

            listing = await self._list(client)
            idx = len(listing["llm_services"]) - 1

            # 禁用
            status, _ = await self._modify(client, idx, {"enable": False})
            assert status == 200
            after = await self._list(client)
            assert after["llm_services"][idx]["enable"] is False

            # 重新启用
            status, _ = await self._modify(client, idx, {"enable": True})
            assert status == 200
            after = await self._list(client)
            assert after["llm_services"][idx]["enable"] is True

    async def test_connectivity_by_index(self):
        """按序号测试已保存服务（使用 mock LLM）"""
        async with aiohttp.ClientSession() as client:
            data = await self._test_connectivity(client, {
                "mode": "saved",
                "index": 0,
            })
            assert data["status"] == "ok"
            assert "detail" in data
            assert "duration_ms" in data["detail"]

    async def test_connectivity_by_config(self):
        """按临时配置测试（使用 mock LLM）"""
        async with aiohttp.ClientSession() as client:
            data = await self._test_connectivity(client, {
                "mode": "temp",
                "base_url": self._mock_api_url(),
                "api_key": "mock-api-key",
                "type": "openai-compatible",
                "model": "mock-model",
            })
            assert data["status"] == "ok"
            assert "detail" in data

    async def test_connectivity_failure(self):
        """测试不可达服务返回错误详情"""
        async with aiohttp.ClientSession() as client:
            data = await self._test_connectivity(client, {
                "mode": "temp",
                "base_url": "http://127.0.0.1:1/v1",
                "api_key": "bad-key",
                "type": "openai-compatible",
                "model": "nonexistent-model",
            })
            assert data["status"] == "error"
            assert "detail" in data
            assert "error_type" in data["detail"]

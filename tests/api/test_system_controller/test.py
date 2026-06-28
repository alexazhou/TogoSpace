import os
import re
import contextlib
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
        """有已启用服务时返回 initialized: true 和 schedule_state。"""
        async with aiohttp.ClientSession() as client:
            data = await self._status(client)

        assert data["initialized"] is True
        assert "default_llm_server" in data
        assert data["default_llm_server"] == "mock"
        assert "schedule_state" in data
        assert "not_running_reason" in data
        assert data["demo_mode"] is False
        assert data["freeze_data"] is False
        assert data["read_only"] is False
        assert data["hide_sensitive_info"] is False
        assert data["development_mode"] is False

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

    async def test_status_has_schedule_state_field(self):
        """状态响应必须包含 schedule_state 字段。"""
        async with aiohttp.ClientSession() as client:
            data = await self._status(client)

        assert "schedule_state" in data
        assert data["schedule_state"] in ("STOPPED", "BLOCKED", "RUNNING")
        assert "not_running_reason" in data

    async def test_resume_schedule_returns_current_schedule_state(self):
        async with aiohttp.ClientSession() as client:
            async with client.post(f"{self.backend_base_url}/system/schedule/resume.json") as resp:
                assert resp.status == 200
                data = await resp.json()

        assert data["status"] == "ok"
        assert data["schedule_state"] in ("STOPPED", "BLOCKED", "RUNNING")
        assert "not_running_reason" in data

    async def test_backup_database_returns_created_backup(self):
        async with aiohttp.ClientSession() as client:
            async with client.post(f"{self.backend_base_url}/system/database/backup.json") as resp:
                assert resp.status == 200
                data = await resp.json()

        backup_path = data["backup_path"]
        try:
            assert data["status"] == "ok"
            assert os.path.isfile(backup_path)
            assert data["backup_file_name"] == os.path.basename(backup_path)
            assert re.fullmatch(r".+_\d{8}_\d{6}_\d{6}\.db", data["backup_file_name"])
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.remove(backup_path)

    async def test_status_returns_auto_check_update_field(self):
        """系统状态接口应返回 auto_check_update 字段。"""
        async with aiohttp.ClientSession() as client:
            data = await self._status(client)

        assert "auto_check_update" in data
        assert isinstance(data["auto_check_update"], bool)

    # ──────── check_update API ────────

    async def test_check_update_returns_expected_fields(self):
        """GET /system/check_update.json 返回所有必要字段。"""
        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/system/check_update.json?force=true") as resp:
                assert resp.status == 200
                data = await resp.json()

        assert "has_update" in data
        assert "current_version" in data
        assert "latest_version" in data
        assert "release_url" in data
        assert "release_notes" in data
        assert isinstance(data["has_update"], bool)

    async def test_check_update_current_version_matches(self):
        """check_update 返回的 current_version 应与 version.py 一致。"""
        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/system/check_update.json?force=true") as resp:
                data = await resp.json()

        # current_version 应为 0.x.y 格式
        assert re.fullmatch(r"\d+\.\d+\.\d+", data["current_version"])

    async def test_check_update_without_force_uses_cache(self):
        """不带 force 参数时第二次请求应使用缓存。"""
        async with aiohttp.ClientSession() as client:
            # 第一次请求（可能命中缓存也可能不命中）
            async with client.get(f"{self.backend_base_url}/system/check_update.json") as resp:
                assert resp.status == 200
                data1 = await resp.json()

            # 第二次请求，应该命中缓存
            async with client.get(f"{self.backend_base_url}/system/check_update.json") as resp:
                assert resp.status == 200
                data2 = await resp.json()

        # 两次结果应一致
        assert data1["latest_version"] == data2["latest_version"]

    # ──────── update_config API ────────

    async def test_update_config_toggle_auto_check(self):
        """POST /system/update_config.json 可以切换 auto_check_update。"""
        import json
        async with aiohttp.ClientSession() as client:
            # 获取当前值
            async with client.get(f"{self.backend_base_url}/system/status.json") as resp:
                status = await resp.json()
            original = status["auto_check_update"]

            # 切换值
            new_value = not original
            async with client.post(
                f"{self.backend_base_url}/system/update_config.json",
                json={"auto_check_update": new_value},
            ) as resp:
                assert resp.status == 200
                data = await resp.json()

            assert data["status"] == "ok"
            assert data["auto_check_update"] == new_value

            # 验证生效
            async with client.get(f"{self.backend_base_url}/system/status.json") as resp:
                status2 = await resp.json()
            assert status2["auto_check_update"] == new_value

            # 恢复原值
            async with client.post(
                f"{self.backend_base_url}/system/update_config.json",
                json={"auto_check_update": original},
            ) as resp:
                assert resp.status == 200

    async def test_update_config_empty_body_succeeds(self):
        """POST /system/update_config.json 空 body 不报错。"""
        import json
        async with aiohttp.ClientSession() as client:
            async with client.post(
                f"{self.backend_base_url}/system/update_config.json",
                json={},
            ) as resp:
                assert resp.status == 200
                data = await resp.json()
            assert data["status"] == "ok"

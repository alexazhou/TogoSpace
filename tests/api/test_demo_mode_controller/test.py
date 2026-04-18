import os
import sys

import aiohttp

from ...base import ServiceTestCase

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


class _ApiServiceCase(ServiceTestCase):
    use_custom_config = True


class TestDemoModeController(_ApiServiceCase):
    requires_backend = True

    async def test_frontend_config_reports_demo_flags(self):
        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/config/frontend.json") as resp:
                assert resp.status == 200
                data = await resp.json()

        assert data["demo_mode"] == {
            "enabled": True,
            "freeze_data": True,
            "hide_sensitive_info": True,
        }

    async def test_system_status_reports_demo_readonly(self):
        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/system/status.json") as resp:
                assert resp.status == 200
                data = await resp.json()

        assert data["initialized"] is True
        assert data["demo_mode"] is True
        assert data["freeze_data"] is True
        assert data["read_only"] is True
        assert data["hide_sensitive_info"] is True
        assert data["development_mode"] is True
        assert data["schedule_state"] == "BLOCKED"
        assert data["not_running_reason"] == "演示模式已冻结数据"

    async def test_directories_hidden_when_sensitive_info_is_masked(self):
        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/config/directories.json") as resp:
                assert resp.status == 200
                data = await resp.json()

        assert data["storage_root"] == ""
        assert data["config_dir"] == ""
        assert data["workspace_dir"] == ""
        assert data["data_dir"] == ""
        assert data["log_dir"] == ""
        assert data["demo_mode"] == {
            "enabled": True,
            "freeze_data": True,
            "hide_sensitive_info": True,
        }

    async def test_llm_service_list_masks_sensitive_fields(self):
        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/config/llm_services/list.json") as resp:
                assert resp.status == 200
                data = await resp.json()

        assert data["default_llm_server"] == "demo"
        assert len(data["llm_services"]) == 1
        service = data["llm_services"][0]
        assert service["name"] == "demo"
        assert service["api_key"] == ""
        assert service["base_url"] == ""
        assert service["extra_headers"] == {}
        assert service["has_api_key"] is True

    async def test_write_endpoint_returns_400_in_demo_mode(self):
        async with aiohttp.ClientSession() as client:
            async with client.post(
                f"{self.backend_base_url}/config/llm_services/create.json",
                json={
                    "name": "blocked",
                    "base_url": "http://127.0.0.1:9999/v1",
                    "api_key": "key",
                    "type": "openai-compatible",
                    "model": "blocked-model",
                },
            ) as resp:
                assert resp.status == 400
                data = await resp.json()

        assert data["error_code"] == "demo_mode_data_frozen"

    async def test_resume_schedule_returns_400_in_demo_mode(self):
        async with aiohttp.ClientSession() as client:
            async with client.post(f"{self.backend_base_url}/system/schedule/resume.json") as resp:
                assert resp.status == 400
                data = await resp.json()

        assert data["error_code"] == "demo_mode_data_frozen"

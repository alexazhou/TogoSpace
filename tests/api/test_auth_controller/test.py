import os
import sys

import aiohttp

from ...base import ServiceTestCase

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


class _AuthServiceCase(ServiceTestCase):
    use_custom_config = True


class TestAuthController(_AuthServiceCase):
    requires_backend = True

    async def test_system_status_reports_auth_enabled(self):
        """system/status.json 应返回 auth_enabled=true"""
        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/system/status.json") as resp:
                assert resp.status == 200
                data = await resp.json()

        assert data["auth_enabled"] is True

    async def test_api_without_token_returns_401(self):
        """未携带 token 时应返回 401 auth_required"""
        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/teams/list.json") as resp:
                assert resp.status == 401
                data = await resp.json()

        assert data["error_code"] == "auth_required"

    async def test_api_with_wrong_token_returns_401(self):
        """错误 token 时应返回 401 auth_invalid"""
        async with aiohttp.ClientSession() as client:
            async with client.get(
                f"{self.backend_base_url}/teams/list.json",
                headers={"Authorization": "Bearer wrong_token"},
            ) as resp:
                assert resp.status == 401
                data = await resp.json()

        assert data["error_code"] == "auth_invalid"

    async def test_api_with_correct_token_returns_200(self):
        """正确 token 时应正常返回"""
        async with aiohttp.ClientSession() as client:
            async with client.get(
                f"{self.backend_base_url}/teams/list.json",
                headers={"Authorization": "Bearer test_access_token"},
            ) as resp:
                assert resp.status == 200
                data = await resp.json()

        assert "teams" in data

    async def test_system_status_exempt_from_auth(self):
        """system/status.json 应豁免鉴权，无需 token"""
        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/system/status.json") as resp:
                assert resp.status == 200
                data = await resp.json()

        assert data["auth_enabled"] is True
import os
import sys

import aiohttp

from ...base import ServiceTestCase

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


class _ApiServiceCase(ServiceTestCase):
    """API 测试基类"""


class TestRoleTemplateController(_ApiServiceCase):
    requires_backend = True
    requires_mock_llm = True

    async def test_list_role_templates(self):
        """验证 GET /role_templates/list.json 返回角色模板列表。"""
        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/role_templates/list.json") as resp:
                assert resp.status == 200
                data = await resp.json()

        assert "role_templates" in data
        assert len(data["role_templates"]) > 0

        template = data["role_templates"][0]
        assert "name" in template
        assert "model" in template

    async def test_get_role_template_detail(self):
        """验证 GET /role_templates/<name>.json 返回模板详情。"""
        # 先获取列表确定有模板
        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/role_templates/list.json") as resp:
                data = await resp.json()

        template_name = data["role_templates"][0]["name"]

        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/role_templates/{template_name}.json") as resp:
                assert resp.status == 200
                detail = await resp.json()

        assert detail["name"] == template_name
        assert "model" in detail
        assert "prompt" in detail
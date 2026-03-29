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
        assert "id" in template
        assert "name" in template
        assert "model" in template
        assert "type" in template
        assert "driver" in template

    async def test_get_role_template_detail(self):
        """验证 GET /role_templates/<id>.json 返回模板详情。"""
        # 先获取列表确定有模板
        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/role_templates/list.json") as resp:
                data = await resp.json()

        template = data["role_templates"][0]
        template_id = template["id"]

        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/role_templates/{template_id}.json") as resp:
                assert resp.status == 200
                detail = await resp.json()

        assert detail["id"] == template_id
        assert detail["name"] == template["name"]
        assert "model" in detail
        assert "prompt" in detail
        assert "type" in detail
        assert "driver" in detail
        assert "allowed_tools" in detail

    async def test_create_role_template(self):
        """验证 POST /role_templates/create.json 创建用户模板。"""
        payload = {
            "name": "custom_writer",
            "soul": "你是一个用户创建的模板",
            "model": "gpt-4o-mini",
        }

        async with aiohttp.ClientSession() as client:
            async with client.post(f"{self.backend_base_url}/role_templates/create.json", json=payload) as resp:
                assert resp.status == 200
                created = await resp.json()

            async with client.get(f"{self.backend_base_url}/role_templates/{created['id']}.json") as resp:
                assert resp.status == 200
                detail = await resp.json()

        assert isinstance(created["id"], int)
        assert created["name"] == "custom_writer"
        assert created["type"] == "user"
        assert detail["type"] == "user"

    async def test_modify_role_template(self):
        """验证 POST /role_templates/<id>/modify.json 修改用户模板。"""
        create_payload = {
            "name": "custom_editor",
            "soul": "初始 Soul",
            "model": "gpt-4o-mini",
        }
        modify_payload = {
            "name": "custom_editor_renamed",
            "soul": "更新后的 Soul",
            "model": "gpt-4.1-mini",
            "driver": "native",
            "allowed_tools": ["Read", "Edit"],
        }

        async with aiohttp.ClientSession() as client:
            async with client.post(f"{self.backend_base_url}/role_templates/create.json", json=create_payload) as resp:
                assert resp.status == 200
                created = await resp.json()

            async with client.post(
                f"{self.backend_base_url}/role_templates/{created['id']}/modify.json",
                json=modify_payload,
            ) as resp:
                assert resp.status == 200
                updated = await resp.json()

            async with client.get(f"{self.backend_base_url}/role_templates/{created['id']}.json") as resp:
                assert resp.status == 200
                detail = await resp.json()

        assert updated["id"] == created["id"]
        assert updated["name"] == "custom_editor_renamed"
        assert updated["prompt"] == "更新后的 Soul"
        assert updated["model"] == "gpt-4.1-mini"
        assert updated["driver"] == "native"
        assert updated["allowed_tools"] == ["Read", "Edit"]
        assert detail["name"] == "custom_editor_renamed"
        assert detail["prompt"] == "更新后的 Soul"
        assert detail["model"] == "gpt-4.1-mini"
        assert detail["driver"] == "native"
        assert detail["allowed_tools"] == ["Read", "Edit"]

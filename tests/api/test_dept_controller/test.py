import os
import sys

import aiohttp

from ...base import ServiceTestCase

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


class _ApiServiceCase(ServiceTestCase):
    """API 测试基类"""


class TestDeptController(_ApiServiceCase):
    requires_backend = True
    requires_mock_llm = True

    async def _get_team_id(self, team_name: str) -> int:
        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/teams/list.json") as resp:
                data = await resp.json()
        team = next(team for team in data["teams"] if team["name"] == team_name)
        return team["id"]

    async def _get_agent_id(self, team_id: int, agent_name: str) -> int:
        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/teams/{team_id}/agents/{agent_name}.json") as resp:
                assert resp.status == 200
                data = await resp.json()
        return data["id"]

    async def test_get_dept_tree_empty(self):
        """验证 GET /teams/<id>/dept_tree.json 无部门树时返回 null。"""
        team_id = await self._get_team_id("e2e")

        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/teams/{team_id}/dept_tree.json") as resp:
                assert resp.status == 200
                data = await resp.json()

        # e2e team 没有配置 dept_tree
        assert data["dept_tree"] is None

    async def test_set_and_get_dept_tree(self):
        """验证 PUT/GET /teams/<id>/dept_tree/update.json 设置和获取部门树。"""
        team_id = await self._get_team_id("e2e")
        alice_id = await self._get_agent_id(team_id, "alice")
        bob_id = await self._get_agent_id(team_id, "bob")

        # 设置部门树（至少需要 2 个成员）
        dept_tree = {
            "dept_name": "技术部",
            "responsibility": "负责技术研发",
            "manager_id": alice_id,
            "member_ids": [alice_id, bob_id],
            "children": [],
        }

        async with aiohttp.ClientSession() as client:
            async with client.put(
                f"{self.backend_base_url}/teams/{team_id}/dept_tree/update.json",
                json={"dept_tree": dept_tree},
            ) as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["status"] == "ok"

            # 获取部门树
            async with client.get(f"{self.backend_base_url}/teams/{team_id}/dept_tree.json") as resp:
                assert resp.status == 200
                data = await resp.json()

        assert data["dept_tree"] is not None
        assert data["dept_tree"]["dept_name"] == "技术部"
        assert data["dept_tree"]["manager_id"] == alice_id
        assert alice_id in data["dept_tree"]["member_ids"]
        assert bob_id in data["dept_tree"]["member_ids"]

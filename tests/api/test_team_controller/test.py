import os
import sys

import aiohttp

from ...base import ServiceTestCase

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


class _ApiServiceCase(ServiceTestCase):
    requires_backend = True
    requires_mock_llm = True


class TestTeamController(_ApiServiceCase):
    async def _get_team_id(self, team_name: str) -> int:
        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/teams/list.json") as resp:
                assert resp.status == 200
                data = await resp.json()
        team = next(team for team in data["teams"] if team["name"] == team_name)
        return team["id"]

    async def _get_role_template_id(self, template_name: str) -> int:
        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/role_templates/list.json") as resp:
                assert resp.status == 200
                data = await resp.json()
        template = next(item for item in data["role_templates"] if item["name"] == template_name)
        return template["id"]

    async def test_team_detail_includes_members_and_rooms(self):
        team_id = await self._get_team_id("e2e")
        alice_template_id = await self._get_role_template_id("alice")
        bob_template_id = await self._get_role_template_id("bob")

        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/teams/{team_id}.json") as resp:
                assert resp.status == 200
                data = await resp.json()

        assert data["name"] == "e2e"
        assert data["config"] == {}
        assert len(data["members"]) == 2
        member_names = {m["name"] for m in data["members"]}
        assert member_names == {"alice", "bob"}
        assert len(data["rooms"]) == 1
        room = data["rooms"][0]
        assert room["name"] == "general"
        assert set(room["members"]) == {"Operator", "alice", "bob"}
        assert room["max_turns"] == 50

    async def test_create_team_and_fetch_detail(self):
        payload = {
            "name": "new_team",
            "config": {
                "slogan": "使命必达",
                "rules": "先沟通后执行",
            },
        }

        async with aiohttp.ClientSession() as client:
            async with client.post(f"{self.backend_base_url}/teams/create.json", json=payload) as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["status"] == "created"
                assert isinstance(data["id"], int)
                created_team_id = data["id"]

            async with client.get(f"{self.backend_base_url}/teams/list.json") as resp:
                assert resp.status == 200
                teams_data = await resp.json()

        assert any(team["name"] == "new_team" for team in teams_data["teams"])

        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/teams/{created_team_id}.json") as resp:
                assert resp.status == 200
                detail = await resp.json()

        assert detail["members"] == []
        assert detail["config"] == {
            "slogan": "使命必达",
            "rules": "先沟通后执行",
        }
        assert detail["rooms"] == []

    async def test_team_agents_by_team_id(self):
        """验证 GET /agents/list.json?team_id=<id> 返回团队成员。"""
        team_id = await self._get_team_id("e2e")

        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/agents/list.json?team_id={team_id}") as resp:
                assert resp.status == 200
                agents_data = await resp.json()

        assert len(agents_data["agents"]) == 2
        names = {a["name"] for a in agents_data["agents"]}
        assert names == {"alice", "bob"}
        agent = agents_data["agents"][0]
        assert isinstance(agent["role_template_id"], int)

    async def test_agent_detail(self):
        """验证 GET /teams/<id>/agents/<name>.json 返回成员详情。"""
        team_id = await self._get_team_id("e2e")

        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/teams/{team_id}/agents/alice.json") as resp:
                assert resp.status == 200
                data = await resp.json()

        assert data["name"] == "alice"
        assert isinstance(data["role_template_id"], int)
        assert "employ_status" in data
        assert "model" in data
        assert "driver" in data

    async def test_team_set_enabled(self):
        """验证 POST /teams/{id}/set_enabled.json 设置团队启用状态。"""
        team_id = await self._get_team_id("e2e")

        async with aiohttp.ClientSession() as client:
            # 先停用
            async with client.post(
                f"{self.backend_base_url}/teams/{team_id}/set_enabled.json",
                json={"enabled": False},
            ) as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["status"] == "ok"
                assert data["enabled"] is False

            # 验证停用后不在启用列表中（使用 enabled=true 参数过滤）
            async with client.get(f"{self.backend_base_url}/teams/list.json?enabled=true") as resp:
                teams_data = await resp.json()
            team_names = [t["name"] for t in teams_data["teams"]]
            assert "e2e" not in team_names

            # 验证停用的团队在停用列表中
            async with client.get(f"{self.backend_base_url}/teams/list.json?enabled=false") as resp:
                teams_data = await resp.json()
            team_names = [t["name"] for t in teams_data["teams"]]
            assert "e2e" in team_names

            # 再启用
            async with client.post(
                f"{self.backend_base_url}/teams/{team_id}/set_enabled.json",
                json={"enabled": True},
            ) as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["status"] == "ok"
                assert data["enabled"] is True

            # 验证启用后重新出现在启用列表中
            async with client.get(f"{self.backend_base_url}/teams/list.json?enabled=true") as resp:
                teams_data = await resp.json()
            team_names = [t["name"] for t in teams_data["teams"]]
            assert "e2e" in team_names

    async def test_team_set_enabled_invalid_id(self):
        """验证设置不存在的团队启用状态返回错误。"""
        async with aiohttp.ClientSession() as client:
            async with client.post(
                f"{self.backend_base_url}/teams/99999/set_enabled.json",
                json={"enabled": True},
            ) as resp:
                assert resp.status != 200

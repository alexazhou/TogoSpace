import os
import sys
import time

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

    async def test_team_detail_includes_agents_and_rooms(self):
        team_id = await self._get_team_id("e2e")
        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/teams/{team_id}.json") as resp:
                assert resp.status == 200
                data = await resp.json()

        assert data["name"] == "e2e"
        assert data["config"] == {}
        assert len(data["agents"]) == 2
        agent_names = {a["name"] for a in data["agents"]}
        assert agent_names == {"alice", "bob"}
        assert len(data["rooms"]) == 2
        rooms_by_name = {room["name"]: room for room in data["rooms"]}
        assert set(rooms_by_name.keys()) == {"general", "测试组"}
        assert len(rooms_by_name["general"]["agent_ids"]) == 3
        assert len(rooms_by_name["general"]["agents"]) == 3
        assert rooms_by_name["general"]["max_turns"] == 50
        assert len(rooms_by_name["测试组"]["agent_ids"]) == 2
        assert len(rooms_by_name["测试组"]["agents"]) == 2

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

        assert detail["agents"] == []
        assert detail["config"] == {
            "slogan": "使命必达",
            "rules": "先沟通后执行",
        }
        assert detail["rooms"] == []

    async def test_team_modify_agents_with_role_template_id(self):
        template_id = await self._get_role_template_id("alice")
        temp_team_name = f"team_modify_members_{int(time.time() * 1000)}"

        async with aiohttp.ClientSession() as client:
            async with client.post(
                f"{self.backend_base_url}/teams/create.json",
                json={"name": temp_team_name},
            ) as resp:
                assert resp.status == 200
                create_data = await resp.json()
                team_id = create_data["id"]

            async with client.post(
                f"{self.backend_base_url}/teams/{team_id}/modify.json",
                json={
                    "agents": [
                        {
                            "name": "tom",
                            "role_template_id": template_id,
                            "model": "gpt-4o",
                            "driver": "native",
                        }
                    ]
                },
            ) as resp:
                assert resp.status == 200
                modify_data = await resp.json()
                assert modify_data["status"] == "updated"

            async with client.get(f"{self.backend_base_url}/agents/list.json?team_id={team_id}") as resp:
                assert resp.status == 200
                agents_data = await resp.json()

            async with client.post(f"{self.backend_base_url}/teams/{team_id}/delete.json") as resp:
                assert resp.status == 200

        tom = next(agent for agent in agents_data["agents"] if agent["name"] == "tom")
        assert tom["role_template_id"] == template_id
        assert tom["model"] == "gpt-4o"
        assert tom["driver"] == "native"

    async def test_team_modify_agents_with_invalid_role_template_id(self):
        temp_team_name = f"team_modify_invalid_members_{int(time.time() * 1000)}"

        async with aiohttp.ClientSession() as client:
            async with client.post(
                f"{self.backend_base_url}/teams/create.json",
                json={"name": temp_team_name},
            ) as resp:
                assert resp.status == 200
                create_data = await resp.json()
                team_id = create_data["id"]

            async with client.post(
                f"{self.backend_base_url}/teams/{team_id}/modify.json",
                json={
                    "agents": [
                        {
                            "name": "tom",
                            "role_template_id": 99999999,
                            "model": "",
                            "driver": "native",
                        }
                    ]
                },
            ) as resp:
                assert resp.status == 400
                error_data = await resp.json()
                assert error_data["error_code"] == "role_template_not_found"

            async with client.post(f"{self.backend_base_url}/teams/{team_id}/delete.json") as resp:
                assert resp.status == 200

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

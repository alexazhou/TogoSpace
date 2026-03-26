import os
import sys

import aiohttp
import pytest

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

    async def test_team_detail_includes_members_and_rooms(self):
        team_id = await self._get_team_id("e2e")

        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/teams/{team_id}.json") as resp:
                assert resp.status == 200
                data = await resp.json()

        assert data["name"] == "e2e"
        assert data["working_directory"] == ""
        assert data["config"] == {}
        assert data["members"] == [{"name": "alice", "agent": "alice"}]
        assert len(data["rooms"]) == 1
        room = data["rooms"][0]
        assert room["name"] == "general"
        assert room["members"] == ["Operator", "alice"]
        assert room["max_turns"] == 50

    async def test_create_team_and_fetch_detail(self):
        payload = {
            "name": "new_team",
            "working_directory": "/tmp/new_team",
            "config": {
                "slogan": "使命必达",
                "rules": "先沟通后执行",
            },
            "members": [{"name": "alice", "agent": "alice"}],
            "preset_rooms": [
                {
                    "name": "团队群聊",
                    "members": ["alice"],
                    "initial_topic": "hello",
                    "max_turns": 100,
                }
            ],
        }

        async with aiohttp.ClientSession() as client:
            async with client.post(f"{self.backend_base_url}/teams/create.json", json=payload) as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["status"] == "created"

            async with client.get(f"{self.backend_base_url}/teams/list.json") as resp:
                assert resp.status == 200
                teams_data = await resp.json()

        assert any(team["name"] == "new_team" for team in teams_data["teams"])

        team_id = await self._get_team_id("new_team")
        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/teams/{team_id}.json") as resp:
                assert resp.status == 200
                detail = await resp.json()

        assert detail["members"] == [{"name": "alice", "agent": "alice"}]
        assert detail["working_directory"] == "/tmp/new_team"
        assert detail["config"] == {
            "slogan": "使命必达",
            "rules": "先沟通后执行",
        }
        assert len(detail["rooms"]) == 1
        assert detail["rooms"][0]["name"] == "团队群聊"

    async def test_team_filtered_agents_and_rooms(self):
        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/members/list.json?team_name=e2e") as resp:
                assert resp.status == 200
                agents_data = await resp.json()

            async with client.get(f"{self.backend_base_url}/rooms/list.json?team_name=e2e") as resp:
                assert resp.status == 200
                rooms_data = await resp.json()

        assert len(agents_data["agents"]) == 1
        assert agents_data["agents"][0]["team_name"] == "e2e"
        assert agents_data["agents"][0]["name"] == "alice"
        assert len(rooms_data["rooms"]) == 1
        assert rooms_data["rooms"][0]["team_name"] == "e2e"
        assert rooms_data["rooms"][0]["room_name"] == "general"

    async def test_team_filtered_agents_and_rooms_by_team_id(self):
        team_id = await self._get_team_id("e2e")

        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/members/list.json?team_id={team_id}") as resp:
                assert resp.status == 200
                agents_data = await resp.json()

            async with client.get(f"{self.backend_base_url}/rooms/list.json?team_id={team_id}") as resp:
                assert resp.status == 200
                rooms_data = await resp.json()

        assert len(agents_data["agents"]) == 1
        assert agents_data["agents"][0]["team_name"] == "e2e"
        assert agents_data["agents"][0]["name"] == "alice"
        assert len(rooms_data["rooms"]) == 1
        assert rooms_data["rooms"][0]["team_name"] == "e2e"
        assert rooms_data["rooms"][0]["room_name"] == "general"

    async def test_agent_detail_returns_prompt_and_driver(self):
        team_id = await self._get_team_id("e2e")

        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/teams/{team_id}/members/alice.json") as resp:
                assert resp.status == 200
                data = await resp.json()

        assert data["name"] == "alice"
        assert data["team_name"] == "e2e"
        assert data["agent_name"] == "alice"
        assert data["driver_type"] == "native"
        assert "Alice" in data["prompt"]

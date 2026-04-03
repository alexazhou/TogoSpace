import os
import sys
import time
import aiohttp
import pytest
from tests.base import ServiceTestCase

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

class TestConfigApi(ServiceTestCase):
    requires_backend = True
    requires_mock_llm = True

    async def _get_team_id(self, team_name: str) -> int:
        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/teams/list.json") as resp:
                assert resp.status == 200
                data = await resp.json()
        team = next(team for team in data["teams"] if team["name"] == team_name)
        return team["id"]

    async def _get_team_agents(self, team_id: int) -> list[dict]:
        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/agents/list.json?team_id={team_id}") as resp:
                assert resp.status == 200
                data = await resp.json()
        return data["agents"]

    async def test_team_modify_and_delete(self):
        # Create a temporary team for modification and deletion
        temp_team_name = f"temp_team_mod_{int(time.time() * 1000)}"
        payload = {
            "name": temp_team_name,
        }
        async with aiohttp.ClientSession() as client:
            async with client.post(f"{self.backend_base_url}/teams/create.json", json=payload) as resp:
                assert resp.status == 200
                create_data = await resp.json()
                team_id = create_data["id"]

        # 1. Modify Team
        modify_payload = {
            "working_directory": "/tmp/modified_temp",
            "config": {"note": "modified"}
        }
        async with aiohttp.ClientSession() as client:
            async with client.post(f"{self.backend_base_url}/teams/{team_id}/modify.json", json=modify_payload) as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["status"] == "updated"

            # Verify modification
            async with client.get(f"{self.backend_base_url}/teams/{team_id}.json") as resp:
                detail = await resp.json()
                assert detail["working_directory"] == "/tmp/modified_temp"
                assert detail["config"] == {"note": "modified"}

        # 2. Delete Team
        async with aiohttp.ClientSession() as client:
            async with client.post(f"{self.backend_base_url}/teams/{team_id}/delete.json") as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["status"] == "deleted"

            # Verify deletion
            async with client.get(f"{self.backend_base_url}/teams/list.json") as resp:
                teams_data = await resp.json()
                assert not any(t["id"] == team_id for t in teams_data["teams"])

    async def test_team_room_lifecycle(self):
        # Use existing e2e team or create one
        team_id = await self._get_team_id("e2e")
        room_name = f"new_room_{int(time.time() * 1000)}"
        
        # 1. List Team Rooms
        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/teams/{team_id}/rooms/list.json") as resp:
                assert resp.status == 200
                data = await resp.json()
                assert len(data["rooms"]) >= 1

        agents = await self._get_team_agents(team_id)
        alice = next(agent for agent in agents if agent["name"] == "alice")
        bob = next(agent for agent in agents if agent["name"] == "bob")

        # 2. Create Team Room
        create_payload = {
            "name": room_name,
            "type": "GROUP",
            "initial_topic": "testing",
            "max_turns": 20,
            "agent_ids": [alice["id"], bob["id"]],
        }
        async with aiohttp.ClientSession() as client:
            async with client.post(f"{self.backend_base_url}/teams/{team_id}/rooms/create.json", json=create_payload) as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["status"] == "created"

            # Verify creation
            async with client.get(f"{self.backend_base_url}/teams/{team_id}/rooms/list.json") as resp:
                rooms_data = await resp.json()
                assert any(r["name"] == room_name for r in rooms_data["rooms"])
                new_room = next(r for r in rooms_data["rooms"] if r["name"] == room_name)
                new_room_id = new_room["id"]

        # 3. Get Room Detail
        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/teams/{team_id}/rooms/{new_room_id}.json") as resp:
                assert resp.status == 200
                detail = await resp.json()
                assert detail["name"] == room_name
                assert detail["initial_topic"] == "testing"

        # 4. Modify Room
        modify_payload = {
            "type": "PRIVATE",
            "initial_topic": "updated topic",
            "max_turns": 30
        }
        async with aiohttp.ClientSession() as client:
            async with client.post(f"{self.backend_base_url}/teams/{team_id}/rooms/{new_room_id}/modify.json", json=modify_payload) as resp:
                assert resp.status == 200
                
            # Verify modification
            async with client.get(f"{self.backend_base_url}/teams/{team_id}/rooms/{new_room_id}.json") as resp:
                detail = await resp.json()
                assert detail["initial_topic"] == "updated topic"
                assert detail["max_turns"] == 30

        # 5. Room Agents Management
        # List Agents
        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/teams/{team_id}/rooms/{new_room_id}/agents/list.json") as resp:
                assert resp.status == 200
                
            # Modify Agents
            agents_payload = {"agent_ids": [alice["id"], -1]}
            async with client.post(f"{self.backend_base_url}/teams/{team_id}/rooms/{new_room_id}/agents/modify.json", json=agents_payload) as resp:
                assert resp.status == 200
                
            # Verify agents
            async with client.get(f"{self.backend_base_url}/teams/{team_id}/rooms/{new_room_id}/agents/list.json") as resp:
                data = await resp.json()
                agent_ids = set(data["agent_ids"])
                assert alice["id"] in agent_ids
                assert -1 in agent_ids

        # 6. Delete Room
        async with aiohttp.ClientSession() as client:
            async with client.post(f"{self.backend_base_url}/teams/{team_id}/rooms/{new_room_id}/delete.json") as resp:
                assert resp.status == 200
                
            # Verify deletion
            async with client.get(f"{self.backend_base_url}/teams/{team_id}/rooms/list.json") as resp:
                rooms_data = await resp.json()
                assert not any(r["id"] == new_room_id for r in rooms_data["rooms"])

    async def test_team_room_create_with_agent_ids(self):
        team_id = await self._get_team_id("e2e")
        room_name = f"room_with_agent_ids_{int(time.time() * 1000)}"
        agents = await self._get_team_agents(team_id)
        alice = next(agent for agent in agents if agent["name"] == "alice")
        bob = next(agent for agent in agents if agent["name"] == "bob")

        create_payload = {
            "name": room_name,
            "type": "GROUP",
            "initial_topic": "room created by agent ids",
            "max_turns": 12,
            "agent_ids": [alice["id"], bob["id"]],
        }
        async with aiohttp.ClientSession() as client:
            async with client.post(f"{self.backend_base_url}/teams/{team_id}/rooms/create.json", json=create_payload) as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["status"] == "created"

            async with client.get(f"{self.backend_base_url}/teams/{team_id}/rooms/list.json") as resp:
                assert resp.status == 200
                rooms_data = await resp.json()
                created_room = next(room for room in rooms_data["rooms"] if room["name"] == room_name)
                room_id = created_room["id"]

            async with client.get(f"{self.backend_base_url}/teams/{team_id}/rooms/{room_id}/agents/list.json") as resp:
                assert resp.status == 200
                agents_data = await resp.json()
                assert set(agents_data["agent_ids"]) == {alice["id"], bob["id"]}

            async with client.post(f"{self.backend_base_url}/teams/{team_id}/rooms/{room_id}/delete.json") as resp:
                assert resp.status == 200

    async def test_team_room_create_with_invalid_agent_ids(self):
        team_id = await self._get_team_id("e2e")
        create_payload = {
            "name": "room_with_invalid_agent_ids",
            "type": "GROUP",
            "initial_topic": "invalid agent ids",
            "max_turns": 12,
            "agent_ids": [99999999],
        }
        async with aiohttp.ClientSession() as client:
            async with client.post(f"{self.backend_base_url}/teams/{team_id}/rooms/create.json", json=create_payload) as resp:
                assert resp.status == 400
                data = await resp.json()
                assert data["error_code"] == "agent_not_found"

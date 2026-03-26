import os
import sys
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

    async def test_team_modify_and_delete(self):
        # Create a temporary team for modification and deletion
        payload = {
            "name": "temp_team_mod",
            "members": [{"name": "alice", "agent": "alice"}],
            "preset_rooms": []
        }
        async with aiohttp.ClientSession() as client:
            await client.post(f"{self.backend_base_url}/teams/create.json", json=payload)
        
        team_id = await self._get_team_id("temp_team_mod")
        
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
        
        # 1. List Team Rooms
        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/teams/{team_id}/rooms/list.json") as resp:
                assert resp.status == 200
                data = await resp.json()
                assert len(data["rooms"]) >= 1

        # 2. Create Team Room
        create_payload = {
            "name": "new_room",
            "type": "GROUP",
            "initial_topic": "testing",
            "max_turns": 20
        }
        async with aiohttp.ClientSession() as client:
            async with client.post(f"{self.backend_base_url}/teams/{team_id}/rooms/create.json", json=create_payload) as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["status"] == "created"

            # Verify creation
            async with client.get(f"{self.backend_base_url}/teams/{team_id}/rooms/list.json") as resp:
                rooms_data = await resp.json()
                assert any(r["name"] == "new_room" for r in rooms_data["rooms"])
                new_room = next(r for r in rooms_data["rooms"] if r["name"] == "new_room")
                new_room_id = new_room["id"]

        # 3. Get Room Detail
        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/teams/{team_id}/rooms/{new_room_id}.json") as resp:
                assert resp.status == 200
                detail = await resp.json()
                assert detail["name"] == "new_room"
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

        # 5. Room Members Management
        # List Members
        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/teams/{team_id}/rooms/{new_room_id}/members/list.json") as resp:
                assert resp.status == 200
                
            # Modify Members
            members_payload = {"members": ["alice", "Operator"]}
            async with client.post(f"{self.backend_base_url}/teams/{team_id}/rooms/{new_room_id}/members/modify.json", json=members_payload) as resp:
                assert resp.status == 200
                
            # Verify members
            async with client.get(f"{self.backend_base_url}/teams/{team_id}/rooms/{new_room_id}/members/list.json") as resp:
                data = await resp.json()
                assert set(data["members"]) == {"alice", "Operator"}

        # 6. Delete Room
        async with aiohttp.ClientSession() as client:
            async with client.post(f"{self.backend_base_url}/teams/{team_id}/rooms/{new_room_id}/delete.json") as resp:
                assert resp.status == 200
                
            # Verify deletion
            async with client.get(f"{self.backend_base_url}/teams/{team_id}/rooms/list.json") as resp:
                rooms_data = await resp.json()
                assert not any(r["id"] == new_room_id for r in rooms_data["rooms"])

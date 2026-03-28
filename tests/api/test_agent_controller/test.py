import os
import sys

import aiohttp
import pytest

from ...base import ServiceTestCase

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


class _ApiServiceCase(ServiceTestCase):
    """API 测试基类：每个测试类在独立子进程中启动后端与 MockLLM。"""


class TestAgentController(_ApiServiceCase):
    requires_backend = True
    requires_mock_llm = True

    async def _get_team_id(self, team_name: str) -> int:
        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/teams/list.json") as resp:
                assert resp.status == 200
                data = await resp.json()
        team = next(team for team in data["teams"] if team["name"] == team_name)
        return team["id"]

    async def test_get_agents_by_team(self):
        """验证 GET /agents/list.json?team_id=<id> 返回团队成员列表。"""
        team_id = await self._get_team_id("e2e")

        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/agents/list.json?team_id={team_id}") as resp:
                assert resp.status == 200
                data = await resp.json()

        assert "agents" in data
        assert len(data["agents"]) == 1
        agent = data["agents"][0]
        assert "id" in agent
        assert agent["name"] == "alice"
        assert "employee_number" in agent
        assert agent["role_template_name"] == "alice"
        assert "employ_status" in agent
        assert "model" in agent
        assert "driver" in agent

    async def test_get_agents_without_team_id(self):
        """验证 GET /agents/list.json 无 team_id 时返回空列表。"""
        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/agents/list.json") as resp:
                assert resp.status == 200
                data = await resp.json()

        assert data["agents"] == []

    async def test_agent_detail(self):
        """验证 GET /teams/<id>/agents/<name>.json 返回成员详情。"""
        team_id = await self._get_team_id("e2e")

        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/teams/{team_id}/agents/alice.json") as resp:
                assert resp.status == 200
                data = await resp.json()

        assert data["name"] == "alice"
        assert data["role_template_name"] == "alice"
        assert "employ_status" in data
        assert "model" in data
        assert "driver" in data


class TestAgentBatchUpdate(_ApiServiceCase):
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
            async with client.get(f"{self.backend_base_url}/agents/list.json?team_id={team_id}") as resp:
                data = await resp.json()
        agent = next(a for a in data["agents"] if a["name"] == agent_name)
        return agent["id"]

    async def test_batch_update_agents(self):
        """验证 PUT /teams/<id>/agents/batch_update.json 批量更新成员。"""
        team_id = await self._get_team_id("e2e")
        agent_id = await self._get_agent_id(team_id, "alice")

        # 更新成员
        payload = {
            "agents": [
                {
                    "id": agent_id,
                    "name": "alice",
                    "role_template_name": "alice",
                    "model": "gpt-4",
                    "driver": '{"type": "test"}',
                }
            ]
        }

        async with aiohttp.ClientSession() as client:
            async with client.put(
                f"{self.backend_base_url}/teams/{team_id}/agents/batch_update.json",
                json=payload,
            ) as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["status"] == "ok"

            # 验证更新结果
            async with client.get(f"{self.backend_base_url}/agents/list.json?team_id={team_id}") as resp:
                data = await resp.json()

        agent = data["agents"][0]
        assert agent["model"] == "gpt-4"
        assert agent["driver"] == '{"type": "test"}'

    async def test_batch_update_with_invalid_id(self):
        """验证批量更新时使用不存在的 id 返回错误。"""
        team_id = await self._get_team_id("e2e")

        payload = {
            "agents": [
                {
                    "id": 99999,
                    "name": "not_exist",
                    "role_template_name": "alice",
                    "model": "",
                    "driver": "{}",
                }
            ]
        }

        async with aiohttp.ClientSession() as client:
            async with client.put(
                f"{self.backend_base_url}/teams/{team_id}/agents/batch_update.json",
                json=payload,
            ) as resp:
                assert resp.status != 200
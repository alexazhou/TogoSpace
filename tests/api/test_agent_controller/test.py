import os
import sys

import aiohttp
import pytest

from ...base import ServiceTestCase

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


class _ApiServiceCase(ServiceTestCase):
    """API 测试基类：每个测试类在独立子进程中启动后端与 MockLLM。"""

    async def _disable_team(self, team_id: int) -> None:
        """停用团队（修改 agents/dept_tree 前必须停用）。"""
        async with aiohttp.ClientSession() as client:
            async with client.post(f"{self.backend_base_url}/teams/{team_id}/set_enabled.json", json={"enabled": False}) as resp:
                assert resp.status == 200


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

    async def _get_role_template_id(self, template_name: str) -> int:
        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/role_templates/list.json") as resp:
                assert resp.status == 200
                data = await resp.json()
        template = next(item for item in data["role_templates"] if item["name"] == template_name)
        return template["id"]

    async def test_get_agents_by_team(self):
        """验证 GET /agents/list.json?team_id=<id> 返回团队成员列表。"""
        team_id = await self._get_team_id("e2e")

        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/agents/list.json?team_id={team_id}") as resp:
                assert resp.status == 200
                data = await resp.json()

        assert "agents" in data
        assert len(data["agents"]) == 2
        names = {a["name"] for a in data["agents"]}
        assert "alice" in names
        assert "bob" in names
        agent = data["agents"][0]
        assert "id" in agent
        assert "employee_number" in agent
        assert isinstance(agent["role_template_id"], int)
        assert agent["team_id"] == team_id
        assert "employ_status" in agent
        assert "model" in agent
        assert "driver" in agent
        assert "display_name" not in agent
        assert "i18n" in agent

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
        assert isinstance(data["role_template_id"], int)
        assert "employ_status" in data
        assert "model" in data
        assert "driver" in data
        assert "display_name" not in data
        assert "i18n" in data


class TestAgentsSave(_ApiServiceCase):
    """测试 PUT /teams/<id>/agents/save.json 全量覆盖 Agent 接口。"""

    requires_backend = True
    requires_mock_llm = True

    async def _get_team_id(self, team_name: str) -> int:
        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/teams/list.json") as resp:
                data = await resp.json()
        team = next(team for team in data["teams"] if team["name"] == team_name)
        return team["id"]

    async def _get_agents(self, team_id: int) -> list:
        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/agents/list.json?team_id={team_id}") as resp:
                data = await resp.json()
        return data["agents"]

    async def _get_role_template_id(self, template_name: str) -> int:
        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/role_templates/list.json") as resp:
                data = await resp.json()
        template = next(item for item in data["role_templates"] if item["name"] == template_name)
        return template["id"]

    async def test_save_agents_create_new(self):
        """验证可以创建新 Agent。"""
        team_id = await self._get_team_id("e2e")
        # 修改 agents 前必须停用团队
        await self._disable_team(team_id)
        agents = await self._get_agents(team_id)
        alice_id = next(a["id"] for a in agents if a["name"] == "alice")
        template_id = await self._get_role_template_id("alice")

        # 保留 alice，新增 bob
        payload = {
            "agents": [
                {"id": alice_id, "name": "alice", "role_template_id": template_id, "model": "", "driver": "native"},
                {"id": None, "name": "bob", "role_template_id": template_id, "model": "", "driver": "native"},
            ]
        }

        async with aiohttp.ClientSession() as client:
            async with client.put(
                f"{self.backend_base_url}/teams/{team_id}/agents/save.json",
                json=payload,
            ) as resp:
                assert resp.status == 200
                data = await resp.json()

        assert data["status"] == "ok"
        assert all("display_name" not in agent for agent in data["agents"])
        assert all("i18n" in agent for agent in data["agents"])
        assert len(data["agents"]) == 2
        names = {a["name"] for a in data["agents"]}
        assert "alice" in names
        assert "bob" in names
        # 验证新成员有 id
        bob = next(a for a in data["agents"] if a["name"] == "bob")
        assert bob["id"] is not None
        assert "employee_number" in bob

    async def test_save_agents_update_existing(self):
        """验证可以更新现有 Agent。"""
        team_id = await self._get_team_id("e2e")
        # 修改 agents 前必须停用团队
        await self._disable_team(team_id)
        agents = await self._get_agents(team_id)
        alice_id = next(a["id"] for a in agents if a["name"] == "alice")
        template_id = await self._get_role_template_id("alice")

        # 更新 alice 的 model
        payload = {
            "agents": [
                {"id": alice_id, "name": "alice", "role_template_id": template_id, "model": "gpt-4o", "driver": "native"},
            ]
        }

        async with aiohttp.ClientSession() as client:
            async with client.put(
                f"{self.backend_base_url}/teams/{team_id}/agents/save.json",
                json=payload,
            ) as resp:
                assert resp.status == 200
                data = await resp.json()

        alice = next(a for a in data["agents"] if a["name"] == "alice")
        assert alice["model"] == "gpt-4o"

    async def test_save_agents_offboard_missing(self):
        """验证不在列表中的 Agent 被设为离职状态。"""
        team_id = await self._get_team_id("e2e")
        # 修改 agents 前必须停用团队
        await self._disable_team(team_id)
        agents = await self._get_agents(team_id)
        alice_id = next(a["id"] for a in agents if a["name"] == "alice")
        template_id = await self._get_role_template_id("alice")

        # 先创建 bob
        payload = {
            "agents": [
                {"id": alice_id, "name": "alice", "role_template_id": template_id, "model": "", "driver": "native"},
                {"id": None, "name": "bob", "role_template_id": template_id, "model": "", "driver": "native"},
            ]
        }
        async with aiohttp.ClientSession() as client:
            async with client.put(
                f"{self.backend_base_url}/teams/{team_id}/agents/save.json",
                json=payload,
            ) as resp:
                assert resp.status == 200

        # 只保留 alice，bob 会被设为离职
        payload = {
            "agents": [
                {"id": alice_id, "name": "alice", "role_template_id": template_id, "model": "", "driver": "native"},
            ]
        }
        async with aiohttp.ClientSession() as client:
            async with client.put(
                f"{self.backend_base_url}/teams/{team_id}/agents/save.json",
                json=payload,
            ) as resp:
                assert resp.status == 200
                data = await resp.json()

        # 在职成员只有 alice
        assert len(data["agents"]) == 1
        assert data["agents"][0]["name"] == "alice"

        # bob 应该还在数据库但状态为 OFF_BOARD
        all_agents = await self._get_agents(team_id)
        bob = next((a for a in all_agents if a["name"] == "bob"), None)
        assert bob is not None
        assert bob["employ_status"] == "OFF_BOARD"

    async def test_save_agents_reuse_offboard_name(self):
        """验证离职 Agent 的名字可以被新 Agent 复用。"""
        team_id = await self._get_team_id("e2e")
        # 修改 agents 前必须停用团队
        await self._disable_team(team_id)
        agents = await self._get_agents(team_id)
        alice_id = next(a["id"] for a in agents if a["name"] == "alice")
        template_id = await self._get_role_template_id("alice")

        # 创建 bob
        payload = {
            "agents": [
                {"id": alice_id, "name": "alice", "role_template_id": template_id, "model": "", "driver": "native"},
                {"id": None, "name": "bob", "role_template_id": template_id, "model": "", "driver": "native"},
            ]
        }
        async with aiohttp.ClientSession() as client:
            async with client.put(
                f"{self.backend_base_url}/teams/{team_id}/agents/save.json",
                json=payload,
            ) as resp:
                assert resp.status == 200

        # 让 bob 离职
        payload = {
            "agents": [
                {"id": alice_id, "name": "alice", "role_template_id": template_id, "model": "", "driver": "native"},
            ]
        }
        async with aiohttp.ClientSession() as client:
            async with client.put(
                f"{self.backend_base_url}/teams/{team_id}/agents/save.json",
                json=payload,
            ) as resp:
                assert resp.status == 200

        # 创建新的 bob（复用名字）
        payload = {
            "agents": [
                {"id": alice_id, "name": "alice", "role_template_id": template_id, "model": "", "driver": "native"},
                {"id": None, "name": "bob", "role_template_id": template_id, "model": "", "driver": "native"},
            ]
        }
        async with aiohttp.ClientSession() as client:
            async with client.put(
                f"{self.backend_base_url}/teams/{team_id}/agents/save.json",
                json=payload,
            ) as resp:
                assert resp.status == 200
                data = await resp.json()

        # 应该有两个在职成员
        assert len(data["agents"]) == 2
        names = {a["name"] for a in data["agents"]}
        assert names == {"alice", "bob"}

    async def test_save_agents_duplicate_names(self):
        """验证请求中 Agent 名字重复时报错。"""
        team_id = await self._get_team_id("e2e")
        agents = await self._get_agents(team_id)
        alice_id = next(a["id"] for a in agents if a["name"] == "alice")
        template_id = await self._get_role_template_id("alice")

        # 两个同名成员
        payload = {
            "agents": [
                {"id": alice_id, "name": "alice", "role_template_id": template_id, "model": "", "driver": "native"},
                {"id": None, "name": "alice", "role_template_id": template_id, "model": "", "driver": "native"},
            ]
        }

        async with aiohttp.ClientSession() as client:
            async with client.put(
                f"{self.backend_base_url}/teams/{team_id}/agents/save.json",
                json=payload,
            ) as resp:
                assert resp.status != 200
                data = await resp.json()
                assert "重复" in data.get("error_message", "") or "duplicate" in data.get("error_code", "").lower()

    async def test_save_agents_invalid_id(self):
        """验证使用不存在的 Agent id 报错。"""
        team_id = await self._get_team_id("e2e")

        payload = {
            "agents": [
                {"id": 99999, "name": "not_exist", "role_template_id": 1, "model": "", "driver": "native"},
            ]
        }

        async with aiohttp.ClientSession() as client:
            async with client.put(
                f"{self.backend_base_url}/teams/{team_id}/agents/save.json",
                json=payload,
            ) as resp:
                assert resp.status != 200

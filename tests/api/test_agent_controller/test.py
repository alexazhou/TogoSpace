import os
import sys

import aiohttp
import pytest

from ...base import ServiceTestCase

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


@pytest.mark.forked
class _ApiServiceCase(ServiceTestCase):
    """API 测试基类：每个测试类在独立子进程中启动后端与 MockLLM。"""


class TestAgentController(_ApiServiceCase):
    requires_backend = True
    requires_mock_llm = True

    async def test_get_agents(self):
        """验证 GET /agents 返回正确的 agents 列表及字段结构。"""
        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/agents/list.json") as resp:
                assert resp.status == 200
                data = await resp.json()
        assert "agents" in data
        assert len(data["agents"]) > 0
        agent = data["agents"][0]
        assert "name" in agent
        assert "model" in agent
        assert "team_name" in agent

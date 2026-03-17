import aiohttp

from ...base import ServiceTestCase


class _ApiServiceCase(ServiceTestCase):
    @classmethod
    def setup_class(cls):
        super().setup_class()
        cls.reset_services()

    @classmethod
    def teardown_class(cls):
        cls.cleanup_services()
        super().teardown_class()


class TestAgentController(_ApiServiceCase):
    requires_backend = True
    requires_mock_llm = True

    async def test_get_agents(self):
        """验证 GET /agents 返回正确的 agents 列表及字段结构。"""
        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/agents") as resp:
                assert resp.status == 200
                data = await resp.json()
        assert "agents" in data
        assert len(data["agents"]) > 0
        agent = data["agents"][0]
        assert "name" in agent
        assert "model" in agent
        assert "team_name" in agent

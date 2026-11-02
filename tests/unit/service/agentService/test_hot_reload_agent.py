"""Agent 生命周期的单元测试（不触碰模块级注册表，不启动任何 service）。"""
from unittest.mock import MagicMock

from constants import AgentStatus
from service.agentService.agent import Agent


def test_agent_ignores_start_when_closed():
    # Create an agent mock
    agent = Agent(gt_agent=MagicMock(id=1, name="Test"), system_prompt="test")
    agent.job_consumer = MagicMock()

    # Start when active
    agent.job_consumer.status = AgentStatus.ACTIVE
    agent.start_consumer_task()
    agent.job_consumer.start.assert_called_once()

    # Start when closed
    agent.job_consumer.start.reset_mock()
    agent.job_consumer.status = AgentStatus.CLOSED
    agent.start_consumer_task()
    agent.job_consumer.start.assert_not_called()

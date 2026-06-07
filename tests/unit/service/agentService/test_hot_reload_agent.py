import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from constants import EmployStatus, AgentStatus
from service.agentService import core

@pytest.fixture
def mock_managers():
    with patch("service.agentService.core.gtAgentManager") as mock_agent_mgr, \
         patch("service.agentService.core.gtTeamManager") as mock_team_mgr, \
         patch("service.agentService.core.gtRoleTemplateManager") as mock_template_mgr, \
         patch("service.agentService.core.deptService") as mock_dept_svc, \
         patch("service.agentService.core.configUtil") as mock_config_util, \
         patch("service.agentService.core.llmService") as mock_llm_svc, \
         patch("service.agentService.core._init_and_start_agent") as mock_init_agent, \
         patch("service.agentService.core._restore_agent_runtime_state") as mock_restore:
        
        yield {
            "agent": mock_agent_mgr,
            "team": mock_team_mgr,
            "template": mock_template_mgr,
            "dept": mock_dept_svc,
            "config": mock_config_util,
            "llm": mock_llm_svc,
            "init_agent": mock_init_agent,
            "restore": mock_restore
        }

@pytest.mark.asyncio
async def test_hot_reload_agent_success(mock_managers):
    agent_id = 1
    team_id = 10
    
    # Mock DB objects
    gt_agent = MagicMock(id=agent_id, team_id=team_id, employ_status=EmployStatus.ON_BOARD, name="TestAgent", role_template_id=2)
    mock_managers["agent"].get_agent_by_id = AsyncMock(return_value=gt_agent)
    
    gt_team = MagicMock(id=team_id, name="TestTeam", config={})
    mock_managers["team"].get_team_by_id = AsyncMock(return_value=gt_team)
    
    gt_role_template = MagicMock(id=2, name="TestTemplate")
    mock_managers["template"].get_role_template = AsyncMock(return_value=gt_role_template)
    
    dept_root = MagicMock(manager_id=None)
    mock_managers["dept"].get_dept_tree = AsyncMock(return_value=dept_root)
    
    app_config = MagicMock()
    app_config.setting.workspace_root = "/tmp/ws"
    mock_managers["config"].get_app_config.return_value = app_config
    mock_managers["llm"].get_default_model_or_none.return_value = "default-model"
    
    # Mock old and new agents
    old_agent = MagicMock()
    old_agent.stop_consumer_task = MagicMock()
    old_agent.close = AsyncMock()
    
    new_agent = MagicMock()
    new_agent.start_consumer_task = MagicMock()
    
    mock_managers["init_agent"].return_value = new_agent
    mock_managers["restore"].return_value = None
    
    # Setup global state
    core._agents = {agent_id: old_agent}
    
    # Run
    await core.hot_reload_agent(agent_id)
    
    # Verifications
    old_agent.stop_consumer_task.assert_called_once()
    old_agent.close.assert_awaited_once()
    mock_managers["restore"].assert_awaited_once_with(new_agent, running_task_error_message="Agent attributes reloaded")
    
    assert core._agents[agent_id] is new_agent
    new_agent.start_consumer_task.assert_called_once()


def test_agent_ignores_start_when_closed():
    from service.agentService.agent import Agent
    
    # Create an agent mock
    agent = Agent(gt_agent=MagicMock(id=1, name="Test"), system_prompt="test")
    agent.task_consumer = MagicMock()
    
    # Start when active
    agent.task_consumer.status = AgentStatus.ACTIVE
    agent.start_consumer_task()
    agent.task_consumer.start.assert_called_once()
    
    # Start when closed
    agent.task_consumer.start.reset_mock()
    agent.task_consumer.status = AgentStatus.CLOSED
    agent.start_consumer_task()
    agent.task_consumer.start.assert_not_called()

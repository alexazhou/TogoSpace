"""agentService.hot_reload_agent 集成测试。

hot_reload_agent 直接读写模块级 Agent 注册表，且 agentService.startup() 依赖 ORM
（需要写入 SpecialAgent），因此按测试分层规则放在 integration，由 ServiceTestCase
负责服务启动与注册表清理。
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from constants import EmployStatus
from service import agentService, ormService
from service.agentService import core
from ...base import ServiceTestCase

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


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


class TestHotReloadAgent(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        await ormService.startup(cls._get_test_db_path())
        await agentService.startup()

    @classmethod
    async def async_teardown_class(cls):
        # 用例往注册表里放了测试替身，其 close() 不是 awaitable；
        # 先清空再 shutdown，避免把 MagicMock 当真实 Agent 去关闭。
        core._agents.clear()
        await agentService.shutdown()
        await ormService.shutdown()

    async def test_hot_reload_agent_success(self, mock_managers):
        agent_id = 1
        team_id = 10

        # Mock DB objects
        gt_agent = MagicMock(id=agent_id, team_id=team_id, employ_status=EmployStatus.ON_BOARD, name="TestAgent", role_template_id=2)
        mock_managers["agent"].get_agent_by_id = AsyncMock(return_value=gt_agent)

        gt_team = MagicMock(id=team_id, name="TestTeam", config={})
        mock_managers["team"].get_team_by_id = AsyncMock(return_value=gt_team)

        gt_role_template = MagicMock(id=2, name="TestTemplate")
        mock_managers["template"].get_role_template_by_id = AsyncMock(return_value=gt_role_template)

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

        # 注册表由 agentService.startup() 清空，这里放入待替换的旧 Agent
        core._agents[agent_id] = old_agent

        await core.hot_reload_agent(agent_id)

        # Verifications
        old_agent.stop_consumer_task.assert_called_once()
        old_agent.close.assert_awaited_once()
        mock_managers["restore"].assert_awaited_once_with(new_agent, running_task_error_message="Agent attributes reloaded")

        assert core._agents[agent_id] is new_agent
        new_agent.start_consumer_task.assert_called_once()

"""Integration tests for load_skill tool function with real database."""
import os
import sys

import pytest

import service.ormService as ormService
import service.persistenceService as persistenceService
import service.roomService as roomService
import service.agentService as agentService
import service.skillService as skillService
from dal.db import gtTeamManager, gtAgentManager, gtRoleTemplateManager
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtRoleTemplate import GtRoleTemplate
from model.dbModel.gtTeam import GtTeam
from service.funcToolService.tools import load_skill
from service.roomService.core import ToolCallContext
from unittest.mock import MagicMock

from ...base import ServiceTestCase

TEAM = "test_skill_team"

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


class TestLoadSkillTool(ServiceTestCase):
    """load_skill 工具函数的集成测试，使用真实数据库验证权限和查询。"""

    @classmethod
    async def async_setup_class(cls):
        db_path = cls._get_test_db_path()
        await ormService.startup(db_path)
        await persistenceService.startup()
        await agentService.startup()
        await roomService.startup()
        skillService.startup()

        team = await gtTeamManager.save_team(GtTeam(name=TEAM))
        template = await gtRoleTemplateManager.save_role_template(
            GtRoleTemplate(name="skill_test_role", soul="test")
        )
        # Agent with allow_skills
        agent_with_skills = GtAgent(
            team_id=team.id,
            name="skilled_agent",
            role_template_id=template.id,
            allow_skills=["code_review", "test_design"],
        )
        # Agent without allow_skills (None)
        agent_no_skills = GtAgent(
            team_id=team.id,
            name="noskill_agent",
            role_template_id=template.id,
            allow_skills=None,
        )
        # Agent with empty allow_skills
        agent_empty_skills = GtAgent(
            team_id=team.id,
            name="empty_skill_agent",
            role_template_id=template.id,
            allow_skills=[],
        )
        await gtAgentManager.batch_save_agents(
            team.id,
            [agent_with_skills, agent_no_skills, agent_empty_skills],
        )
        agents = await gtAgentManager.get_team_all_agents(team.id)
        cls.team_id = team.id
        cls.role_template_id = template.id
        cls.agent_ids = {a.name: a.id for a in agents}

    @classmethod
    async def async_teardown_class(cls):
        roomService.shutdown()
        await persistenceService.shutdown()
        await ormService.shutdown()

    async def test_load_skill_no_context(self):
        """无上下文时返回错误。"""
        result = await load_skill(skill_name="code_review", _context=None)
        assert result["success"] is False
        assert "无法获取当前 Agent 上下文" in result["message"]

    async def test_load_skill_no_agent_id(self):
        """_context.agent_id 为空时返回错误。"""
        ctx = ToolCallContext(agent_id=None, team_id=self.team_id, chat_room=MagicMock())
        result = await load_skill(skill_name="code_review", _context=ctx)
        assert result["success"] is False
        assert "无法获取当前 Agent 上下文" in result["message"]

    async def test_load_skill_agent_not_found(self):
        """Agent 不存在时返回错误。"""
        ctx = ToolCallContext(agent_id=99999, team_id=self.team_id, chat_room=MagicMock())
        result = await load_skill(skill_name="code_review", _context=ctx)
        assert result["success"] is False
        assert "未找到 Agent" in result["message"]

    async def test_load_skill_unauthorized_none(self):
        """allow_skills 为 None 时返回权限错误。"""
        agent_id = self.agent_ids["noskill_agent"]
        ctx = ToolCallContext(agent_id=agent_id, team_id=self.team_id, chat_room=MagicMock())
        result = await load_skill(skill_name="code_review", _context=ctx)
        assert result["success"] is False
        assert "未对当前 Agent 开放" in result["message"]

    async def test_load_skill_unauthorized_empty(self):
        """allow_skills 为空列表时返回权限错误。"""
        agent_id = self.agent_ids["empty_skill_agent"]
        ctx = ToolCallContext(agent_id=agent_id, team_id=self.team_id, chat_room=MagicMock())
        result = await load_skill(skill_name="code_review", _context=ctx)
        assert result["success"] is False
        assert "未对当前 Agent 开放" in result["message"]

    async def test_load_skill_skill_not_in_allow_list(self):
        """Skill 名称不在 allow_skills 列表中时返回权限错误。"""
        agent_id = self.agent_ids["skilled_agent"]
        ctx = ToolCallContext(agent_id=agent_id, team_id=self.team_id, chat_room=MagicMock())
        result = await load_skill(skill_name="unauthorized_skill", _context=ctx)
        assert result["success"] is False
        assert "未对当前 Agent 开放" in result["message"]

    async def test_load_skill_skill_not_in_registry(self):
        """Skill 名称在 allow_skills 中但注册表不存在时返回错误。"""
        agent_id = self.agent_ids["skilled_agent"]
        ctx = ToolCallContext(agent_id=agent_id, team_id=self.team_id, chat_room=MagicMock())
        # code_review 在 allow_skills 中，但 skillRegistry 中没有这个 Skill
        result = await load_skill(skill_name="code_review", _context=ctx)
        assert result["success"] is False
        assert "不存在" in result["message"]
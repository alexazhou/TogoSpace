import os
import sys

import pytest

from tests.base import ServiceTestCase
from dal.db import gtTeamManager, gtAgentManager, gtRoleTemplateManager
from model.dbModel.gtTeam import GtTeam
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtRoleTemplate import GtRoleTemplate
from service import ormService, presetService, exportService
from exception import TogoException
from constants import DriverType
from util.configTypes import TeamPreset, AgentPreset, DeptNodePreset, TeamRoomPreset


if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


class TestGetTeamByUuid(ServiceTestCase):
    """测试 gtTeamManager.get_team_by_uuid 的 include_deleted 参数。"""

    @classmethod
    async def async_setup_class(cls):
        await ormService.startup(cls._get_test_db_path())

    @classmethod
    async def async_teardown_class(cls):
        await ormService.shutdown()

    async def async_setup_method(self):
        await GtTeam.delete().aio_execute()

    async def async_teardown_method(self):
        await GtTeam.delete().aio_execute()

    async def test_default_not_include_deleted(self):
        """默认不返回已删除的团队。"""
        team = await gtTeamManager.save_team(GtTeam(
            name="team-001",
            uuid="uuid-001",
            deleted=0,
        ))
        result = await gtTeamManager.get_team_by_uuid("uuid-001")
        assert result is not None
        assert result.id == team.id

    async def test_deleted_team_not_found_by_default(self):
        """已删除团队默认查不到。"""
        await gtTeamManager.save_team(GtTeam(
            name="team-002",
            uuid="uuid-002",
            deleted=1,
        ))
        result = await gtTeamManager.get_team_by_uuid("uuid-002")
        assert result is None

    async def test_include_deleted_returns_deleted_team(self):
        """include_deleted=True 返回已删除团队。"""
        team = await gtTeamManager.save_team(GtTeam(
            name="team-003",
            uuid="uuid-003",
            deleted=1,
        ))
        result = await gtTeamManager.get_team_by_uuid("uuid-003", include_deleted=True)
        assert result is not None
        assert result.id == team.id
        assert result.deleted == 1


class TestPresetTeamImport(ServiceTestCase):
    """测试 presetService._import_team_from_config 的去重逻辑。"""

    @classmethod
    async def async_setup_class(cls):
        await ormService.startup(cls._get_test_db_path())

    @classmethod
    async def async_teardown_class(cls):
        await ormService.shutdown()

    async def async_setup_method(self):
        await GtTeam.delete().aio_execute()
        await GtRoleTemplate.delete().aio_execute()
        # 创建基础角色模板
        await gtRoleTemplateManager.save_role_template(GtRoleTemplate(name="dummy", model="gpt-4o"))

    async def async_teardown_method(self):
        await GtTeam.delete().aio_execute()
        await GtRoleTemplate.delete().aio_execute()

    def _make_team_config(self, uuid: str, name: str) -> TeamPreset:
        return TeamPreset(
            uuid=uuid,
            name=name,
            agents=[AgentPreset(name="agent1", role_template="dummy")],
            auto_start=True,
        )

    async def test_import_new_team_success(self):
        """UUID 不存在时正常导入。"""
        config = self._make_team_config("uuid-new", "new-team")
        team = await presetService._import_team_from_config(config)
        assert team is not None
        assert team.uuid == "uuid-new"
        assert team.name == "new-team"

    async def test_import_existing_team_skipped(self):
        """UUID 存在（deleted=0）时跳过导入。"""
        existing = await gtTeamManager.save_team(GtTeam(
            name="existing-team",
            uuid="uuid-existing",
            deleted=0,
        ))
        config = self._make_team_config("uuid-existing", "existing-team")
        team = await presetService._import_team_from_config(config)
        assert team is None
        # 验证原团队未被修改
        result = await gtTeamManager.get_team_by_id(existing.id)
        assert result is not None
        assert result.name == "existing-team"

    async def test_import_deleted_team_skipped(self):
        """UUID 存在（deleted=1）时跳过导入，不复活。"""
        await gtTeamManager.save_team(GtTeam(
            name="deleted-team",
            uuid="uuid-deleted",
            deleted=1,
        ))
        config = self._make_team_config("uuid-deleted", "deleted-team")
        team = await presetService._import_team_from_config(config)
        assert team is None
        # 验证团队仍处于删除状态
        result = await gtTeamManager.get_team_by_uuid("uuid-deleted", include_deleted=True)
        assert result is not None
        assert result.deleted == 1

    async def test_import_without_uuid_by_name(self):
        """无 UUID 时按 name 匹配已存在的团队。"""
        existing = await gtTeamManager.save_team(GtTeam(
            name="name-match-team",
            deleted=0,
        ))
        config = TeamPreset(
            name="name-match-team",
            agents=[AgentPreset(name="agent1", role_template="dummy")],
            auto_start=True,
        )
        team = await presetService._import_team_from_config(config)
        assert team is None
        # 验证原团队未被修改
        result = await gtTeamManager.get_team_by_id(existing.id)
        assert result is not None
        assert result.name == "name-match-team"

    async def test_export_team_preset_matches_runtime_structure(self):
        await gtRoleTemplateManager.save_role_template(GtRoleTemplate(name="dummy", model="gpt-4o"))

        team_config = TeamPreset(
            uuid="uuid-export-001",
            name="export-team",
            i18n={"display_name": {"zh-CN": "导出团队"}},
            config={"working_directory": "/tmp/export-team", "slogan": "先沟通后执行", "rules": "先评审再执行"},
            agents=[
                AgentPreset(
                    name="manager1",
                    role_template="dummy",
                    i18n={"display_name": {"zh-CN": "经理1"}},
                    model="gpt-4o-mini",
                    driver=DriverType.NATIVE,
                    allow_tools=["send_chat_msg"],
                ),
                AgentPreset(
                    name="agent1",
                    role_template="dummy",
                    i18n={"display_name": {"zh-CN": "成员1"}},
                ),
            ],
            dept_tree=DeptNodePreset(
                dept_name="研发部",
                responsibility="负责研发",
                manager="manager1",
                agents=["manager1", "agent1"],
                children=[],
                i18n={"dept_name": {"zh-CN": "研发部"}},
            ),
            preset_rooms=[
                TeamRoomPreset(
                    name="manager1",
                    agents=["OPERATOR", "manager1"],
                    initial_topic="私聊沟通",
                    max_rounds=20,
                    tags=["private"],
                    i18n={"display_name": {"zh-CN": "经理私聊"}},
                )
            ],
            auto_start=True,
        )

        team = await presetService._import_team_from_config(team_config)
        assert team is not None

        exported = await exportService.export_team_preset(team.id)

        assert exported["uuid"] == "uuid-export-001"
        assert exported["name"] == "export-team"
        assert exported["i18n"] == {"display_name": {"zh-CN": "导出团队"}}
        assert exported["config"] == {"working_directory": "/tmp/export-team", "slogan": "先沟通后执行", "rules": "先评审再执行"}
        assert len(exported["rule_templates"]) == 1
        assert exported["rule_templates"][0]["name"] == "dummy"
        assert exported["rule_templates"][0]["model"] == "gpt-4o"
        assert exported["rule_templates"][0]["soul"] == ""
        assert exported["auto_start"] is True
        assert [agent["name"] for agent in exported["agents"]] == ["agent1", "manager1"]
        assert {agent["role_template"] for agent in exported["agents"]} == {"dummy"}
        exported_manager = next(agent for agent in exported["agents"] if agent["name"] == "manager1")
        assert exported_manager["driver"] == DriverType.NATIVE.value
        assert exported_manager["model"] == "gpt-4o-mini"
        assert exported_manager["allow_tools"] == ["send_chat_msg"]
        assert exported["dept_tree"] is not None
        assert exported["dept_tree"]["manager"] == "manager1"
        assert exported["dept_tree"]["agents"] == ["manager1", "agent1"]
        assert len(exported["preset_rooms"]) == 1
        assert exported["preset_rooms"][0]["name"] == "manager1"
        assert exported["preset_rooms"][0]["agents"] == ["OPERATOR", "manager1"]
        assert exported["preset_rooms"][0]["initial_topic"] == "私聊沟通"


class TestDeptTreeValidation(ServiceTestCase):
    """测试 presetService._to_dept_tree_node 的子部门 manager 验证。"""

    @classmethod
    async def async_setup_class(cls):
        await ormService.startup(cls._get_test_db_path())

    @classmethod
    async def async_teardown_class(cls):
        await ormService.shutdown()

    def setup_method(self):
        self._run_on_class_loop(self._async_setup())

    def teardown_method(self):
        self._run_on_class_loop(self._async_teardown())

    async def _async_setup(self):
        await GtTeam.delete().aio_execute()
        await GtAgent.delete().aio_execute()
        await GtRoleTemplate.delete().aio_execute()
        template = await gtRoleTemplateManager.save_role_template(
            GtRoleTemplate(name="dummy", model="gpt-4o")
        )
        assert template is not None

    async def _async_teardown(self):
        await GtAgent.delete().aio_execute()
        await GtTeam.delete().aio_execute()
        await GtRoleTemplate.delete().aio_execute()

    def _make_team_config_with_dept(
        self,
        uuid: str,
        name: str,
        dept_tree: DeptNodePreset | None,
    ) -> TeamPreset:
        return TeamPreset(
            uuid=uuid,
            name=name,
            agents=[
                AgentPreset(name="manager1", role_template="dummy"),
                AgentPreset(name="child_manager", role_template="dummy"),
                AgentPreset(name="agent1", role_template="dummy"),
            ],
            dept_tree=dept_tree,
            auto_start=True,
        )

    async def test_child_manager_not_in_parent_agents_raises(self):
        """子部门 manager 不在父部门 agents 中时抛出异常。"""
        dept_tree = DeptNodePreset(
            dept_name="parent_dept",
            manager="manager1",
            agents=["manager1", "agent1"],  # 不包含 child_manager
            children=[
                DeptNodePreset(
                    dept_name="child_dept",
                    manager="child_manager",
                    agents=["child_manager"],
                    children=[],
                ),
            ],
        )
        config = self._make_team_config_with_dept("uuid-dept-001", "dept-team", dept_tree)
        with pytest.raises(TogoException) as exc_info:
            await presetService._import_team_from_config(config)
        assert exc_info.value.error_code == "CHILD_MANAGER_NOT_IN_PARENT_AGENTS"
        assert "child_manager" in str(exc_info.value)
        assert "parent_dept" in str(exc_info.value)

    async def test_child_manager_in_parent_agents_success(self):
        """子部门 manager 在父部门 agents 中时正常导入。"""
        dept_tree = DeptNodePreset(
            dept_name="parent_dept",
            manager="manager1",
            agents=["manager1", "child_manager", "agent1"],  # 包含 child_manager
            children=[
                DeptNodePreset(
                    dept_name="child_dept",
                    manager="child_manager",
                    agents=["child_manager", "agent1"],  # 至少 2 人
                    children=[],
                ),
            ],
        )
        config = self._make_team_config_with_dept("uuid-dept-002", "dept-team-ok", dept_tree)
        team = await presetService._import_team_from_config(config)
        assert team is not None
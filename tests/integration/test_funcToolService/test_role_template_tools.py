"""integration tests for role template management tools"""
import asyncio
import os
import sys
import pytest
from unittest.mock import AsyncMock
from typing import Optional

import service.agentService as agentService
import service.ormService as ormService
import service.persistenceService as persistenceService
import service.roomService as roomService
from constants import EmployStatus, RoleTemplateType, ToolCategory
from dal.db import gtAgentManager, gtRoleTemplateManager, gtTeamManager
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtRoleTemplate import GtRoleTemplate
from model.dbModel.gtTeam import GtTeam
from service.funcToolService.core import (
    load_func_tools,
    get_tools,
    build_tools,
)
from service.agentService.toolRegistry import (
    CATEGORY_CONFIG,
    build_runtime_allow_specs,
    AgentToolRegistry,
)
from service.funcToolService.funcToolType import FuncTool
from service.funcToolService.funcToolType import get_function_metadata, python_type_to_json_schema
from service.funcToolService.tools import (
    delete_role_template,
    get_role_template,
    list_role_templates,
    reload_team,
    save_agent,
    save_role_template,
    wake_up_agent,
    send_chat_msg,
    finish_action,
)
from service.roomService import ToolCallContext
from ...base import ServiceTestCase

TEAM = "test_team"

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


class TestRoleTemplateToolMetadata(ServiceTestCase):
    async def test_optional_list_str_maps_to_array(self) -> None:
        """Optional[list[str]] 应映射为 array。"""
        assert python_type_to_json_schema(Optional[list[str]]) == {"type": "array"}

    async def test_role_template_tools_registered(self) -> None:
        """role template 管理工具应加入注册表。"""
        load_func_tools()
        assert {
            "reload_team",
            "list_role_templates",
            "get_role_template",
            "save_agent",
            "save_role_template",
            "delete_role_template",
        } <= {t.function.name for t in get_tools()}

    async def test_role_template_tools_build(self) -> None:
        """role template 工具应能构建为 OpenAITool 定义。"""
        tools = build_tools([
            FuncTool("list_role_templates", list_role_templates),
            FuncTool("get_role_template", get_role_template),
            FuncTool("reload_team", reload_team),
            FuncTool("save_agent", save_agent),
            FuncTool("save_role_template", save_role_template),
            FuncTool("delete_role_template", delete_role_template),
        ])
        assert {tool.function.name for tool in tools} == {
            "reload_team",
            "list_role_templates",
            "get_role_template",
            "save_agent",
            "save_role_template",
            "delete_role_template",
        }

    async def test_role_template_tool_metadata_category_via_registry(self) -> None:
        """工具分类应通过 AgentToolRegistry 补齐。"""
        registry = AgentToolRegistry()
        for t in get_tools():
            registry.register(t, lambda x, y: None)
        
        list_tool = registry.get_registered_tool("list_role_templates")
        reload_tool = registry.get_registered_tool("reload_team")
        save_agent_tool = registry.get_registered_tool("save_agent")
        save_tool = registry.get_registered_tool("save_role_template")
        assert list_tool.category == ToolCategory.ADMIN
        assert reload_tool.category == ToolCategory.ADMIN
        assert save_agent_tool.category == ToolCategory.ADMIN
        assert save_tool.category == ToolCategory.ADMIN

    async def test_all_local_tools_define_category(self) -> None:
        """每个本地工具都应声明 category。"""
        load_func_tools()
        assert {t.function.name for t in get_tools()} <= set(CATEGORY_CONFIG)

    async def test_basic_chat_tools_use_basic_category(self) -> None:
        """基础行动工具应归类到 BASIC。"""
        assert CATEGORY_CONFIG["wake_up_agent"] == ToolCategory.BASIC
        assert CATEGORY_CONFIG["send_chat_msg"] == ToolCategory.BASIC
        assert CATEGORY_CONFIG["finish_action"] == ToolCategory.BASIC

    async def test_tsp_tools_define_categories(self) -> None:
        """gtsp 导出的 TSP 工具也应补齐分类。"""
        assert CATEGORY_CONFIG["list_dir"] == ToolCategory.READ
        assert CATEGORY_CONFIG["read_file"] == ToolCategory.READ
        assert CATEGORY_CONFIG["write_file"] == ToolCategory.WRITE
        assert CATEGORY_CONFIG["edit"] == ToolCategory.WRITE
        assert CATEGORY_CONFIG["grep_search"] == ToolCategory.READ
        assert CATEGORY_CONFIG["glob"] == ToolCategory.READ
        assert CATEGORY_CONFIG["execute_bash"] == ToolCategory.EXECUTE
        assert CATEGORY_CONFIG["process_output"] == ToolCategory.EXECUTE
        assert CATEGORY_CONFIG["process_stop"] == ToolCategory.EXECUTE
        assert CATEGORY_CONFIG["process_list"] == ToolCategory.EXECUTE

    async def test_category_spec_helpers(self) -> None:
        """Category:Read 这类写法应能正确展开本地工具。运行时不再执行过滤（交给保存时校验）。"""
        load_func_tools()
        assert ToolCategory.from_spec("Category:Read") == ToolCategory.READ
        assert ToolCategory.from_spec("category:admin") == ToolCategory.ADMIN

        registry = AgentToolRegistry()
        for t in get_tools():
            registry.register(t, lambda x, y: None)

        read_tools = registry.resolve_enabled_tool_names(
            build_runtime_allow_specs(
                ["Category:Read"],
                is_root_leader=False,
            ),
        )
        root_tools = registry.resolve_enabled_tool_names(
            build_runtime_allow_specs(
                ["Category:Read"],
                is_root_leader=True,
            ),
        )

        assert "get_time" in read_tools
        assert "save_agent" not in read_tools
        assert "save_role_template" not in read_tools
        # root leader 自动补齐 admin 分类
        assert "save_agent" in root_tools
        assert "save_role_template" in root_tools


class TestRoleTemplateTools(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls) -> None:
        db_path = cls._get_test_db_path()
        await ormService.startup(db_path)
        await persistenceService.startup()
        await agentService.startup()
        await roomService.startup()
        team = await gtTeamManager.save_team(GtTeam(name=TEAM))
        await gtAgentManager.batch_save_agents(
            team.id,
            [
                GtAgent(team_id=team.id, name="alice", role_template_id=0),
                GtAgent(team_id=team.id, name="bob", role_template_id=0),
            ],
        )
        cls.team_id = team.id

    @classmethod
    async def async_teardown_class(cls) -> None:
        roomService.shutdown()
        await persistenceService.shutdown()
        await ormService.shutdown()

    async def test_list_role_templates_and_detail(self) -> None:
        """角色模板工具应支持列表和详情查询，列表不返回 soul。"""
        await gtRoleTemplateManager.save_role_template(
            GtRoleTemplate(
                name="planner",
                model="gpt-4o",
                soul="plan carefully",
                type=RoleTemplateType.USER,
                i18n={"display_name": {"zh-CN": "规划师", "en": "Planner"}},
            )
        )

        list_result = await list_role_templates()
        detail_result = await get_role_template("planner")

        assert list_result["success"]
        planner = next(item for item in list_result["role_templates"] if item["name"] == "planner")
        assert planner["display_name"] == "规划师"
        assert "soul" not in planner
        assert detail_result["success"]
        assert detail_result["role_template"]["soul"] == "plan carefully"
        assert detail_result["role_template"]["type"] == "USER"

    async def test_reload_team_uses_current_team_context(self, monkeypatch) -> None:
        """reload_team 应基于当前 team 上下文触发 team 级热重载。
        reload_team 是自中断工具：会创建内部 task 后永久挂起，等待被 stop_team_runtime 取消。
        测试在 task 中运行它，让内部 hot_reload_team task 有机会执行后手动取消。
        """
        hot_reload_team = AsyncMock()
        monkeypatch.setattr("service.teamService.hot_reload_team", hot_reload_team)

        ctx = ToolCallContext(agent_id=1, team_id=self.team_id, chat_room=None)
        task = asyncio.ensure_future(reload_team(_context=ctx))
        # hot_reload 通过 create_task 调度，调度前需等待 DB 查询（aiosqlite 线程池）完成。
        # 轮询直到 hot_reload_team 被同步调用（create_task 内部），最多等待 2 秒。
        loop = asyncio.get_event_loop()
        deadline = loop.time() + 2.0
        while hot_reload_team.call_count == 0:
            if loop.time() > deadline:
                pytest.fail("reload_team 未在 2 秒内调用 hot_reload_team")
            await asyncio.sleep(0.005)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        hot_reload_team.assert_called_once_with(TEAM)

    async def test_list_role_templates_with_search(self) -> None:
        """list_role_templates 应支持关键词搜索 (OR 逻辑，支持 i18n)。"""
        # 1. 准备测试数据
        await save_role_template(
            name="search_writer",
            type="USER",
            soul="draft documentation",
            i18n={"display_name": {"zh-CN": "专业写手", "en": "Pro Writer"}},
            overwrite_existing=True,
        )
        await save_role_template(
            name="search_coder",
            type="USER",
            soul="write python code",
            overwrite_existing=True,
        )

        # 2. 搜索名称
        res = await list_role_templates(keywords=["coder"])
        names = [t["name"] for t in res["role_templates"]]
        assert "search_coder" in names
        assert "search_writer" not in names

        # 3. 搜索 soul
        res = await list_role_templates(keywords=["documentation"])
        names = [t["name"] for t in res["role_templates"]]
        assert "search_writer" in names

        # 4. 搜索 i18n (display_name)
        res = await list_role_templates(keywords=["专业写手"])
        names = [t["name"] for t in res["role_templates"]]
        assert "search_writer" in names

        # 5. 多个词 OR 逻辑 (命中任何一个即可)
        res = await list_role_templates(keywords=["python", "Pro"])
        names = [t["name"] for t in res["role_templates"]]
        assert "search_coder" in names   # 命中 python
        assert "search_writer" in names  # 命中 Pro

        # 6. 大小写不敏感测试
        res = await list_role_templates(keywords=["CODER", "PRO"])
        names = [t["name"] for t in res["role_templates"]]
        assert "search_coder" in names
        assert "search_writer" in names

        # 7. 子串匹配测试
        res = await list_role_templates(keywords=["write"])
        names = [t["name"] for t in res["role_templates"]]
        # search_writer 的名称包含 writer，search_coder 的 soul 包含 write
        assert "search_writer" in names
        assert "search_coder" in names

        # 8. 空关键词列表 (应返回全部)
        res = await list_role_templates(keywords=[])
        assert len(res["role_templates"]) >= 2

        # 9. 不存在的词
        res = await list_role_templates(keywords=["none_existing_word"])
        assert len(res["role_templates"]) == 0

    async def test_save_role_template_creates_and_updates(self) -> None:
        """save_role_template 应在 overwrite_existing=true 时覆盖同名模板。"""
        create_result = await save_role_template(
            name="writer",
            type="USER",
            soul="draft docs",
            model="gpt-4o-mini",
            i18n={"display_name": {"zh-CN": "写手", "en": "Writer"}},
        )
        update_result = await save_role_template(
            name="writer",
            type="SYSTEM",
            soul="draft docs carefully",
            model="gpt-4.1",
            i18n={"display_name": {"zh-CN": "高级写手", "en": "Senior Writer"}},
            overwrite_existing=True,
        )

        assert create_result["success"]
        assert "已创建角色模板 writer" in create_result["message"]
        assert update_result["success"]
        assert "已更新角色模板 writer" in update_result["message"]
        detail = await gtRoleTemplateManager.get_role_template_by_name("writer")
        assert detail is not None
        assert detail.type == RoleTemplateType.SYSTEM
        assert detail.soul == "draft docs carefully"
        assert detail.model == "gpt-4.1"
        assert detail.i18n["display_name"]["zh-CN"] == "高级写手"

    async def test_save_role_template_rejects_existing_without_overwrite(self) -> None:
        """同名模板默认不覆盖，需显式打开 overwrite_existing。"""
        await save_role_template(
            name="writer_no_overwrite",
            type="USER",
            soul="draft docs",
        )

        result = await save_role_template(
            name="writer_no_overwrite",
            type="USER",
            soul="updated",
        )

        assert not result["success"]
        assert "overwrite_existing" in result["message"]

    async def test_save_role_template_rejects_invalid_type(self) -> None:
        """非法 type 应被工具层拒绝。"""
        result = await save_role_template(
            name="invalid_type_template",
            type="ADMIN",
            soul="noop",
        )

        assert not result["success"]
        assert "SYSTEM 或 USER" in result["message"]

    async def test_save_role_template_rejects_system_create_and_update(self) -> None:
        """工具不允许创建或修改 SYSTEM 角色模板。"""
        create_result = await save_role_template(
            name="system_created_by_tool",
            type="SYSTEM",
            soul="noop",
        )
        await gtRoleTemplateManager.save_role_template(
            GtRoleTemplate(
                name="built_in_system_template",
                soul="built in",
                type=RoleTemplateType.SYSTEM,
            )
        )
        update_result = await save_role_template(
            name="built_in_system_template",
            type="SYSTEM",
            soul="updated",
            overwrite_existing=True,
        )

        assert not create_result["success"]
        assert "不允许通过工具创建" in create_result["message"]
        assert not update_result["success"]
        assert "不允许通过工具修改" in update_result["message"]

    async def test_save_agent_creates_and_updates(self) -> None:
        """save_agent 应在当前 team 中创建成员，并在 overwrite_existing=true 时更新。"""
        await gtRoleTemplateManager.save_role_template(
            GtRoleTemplate(
                name="agent_writer",
                soul="draft docs",
                model="gpt-4o-mini",
                type=RoleTemplateType.USER,
            )
        )
        ctx = ToolCallContext(agent_id=1, team_id=self.team_id, chat_room=None)

        create_result = await save_agent(
            name="charlie",
            role_template_name="agent_writer",
            model="gpt-4.1-mini",
            driver="native",
            allow_tools=["Category:Read", "list_dir"],
            i18n={"display_name": {"zh-CN": "查理", "en": "Charlie"}},
            _context=ctx,
        )
        update_result = await save_agent(
            name="charlie",
            role_template_name="agent_writer",
            model="gpt-4.1",
            driver="claude_sdk",
            allow_tools=["Category:Read"],
            i18n={"display_name": {"zh-CN": "高级查理", "en": "Senior Charlie"}},
            overwrite_existing=True,
            _context=ctx,
        )

        assert create_result["success"]
        assert "已创建成员 charlie" in create_result["message"]
        assert update_result["success"]
        assert "已更新成员 charlie" in update_result["message"]
        detail = await gtAgentManager.get_agent(self.team_id, "charlie", status=None)
        assert detail is not None
        assert create_result["agent"]["employ_status"] == "OFF_BOARD"
        assert detail.employ_status == EmployStatus.OFF_BOARD
        assert detail.role_template_id == create_result["agent"]["role_template_id"]
        assert detail.model == "gpt-4.1"
        assert detail.driver.value == "claude_sdk"
        assert detail.allow_tools == ["Category:Read"]
        assert detail.i18n["display_name"]["zh-CN"] == "高级查理"

    async def test_save_agent_rejects_existing_without_overwrite(self) -> None:
        """同名成员默认不覆盖，需显式打开 overwrite_existing。"""
        await gtRoleTemplateManager.save_role_template(
            GtRoleTemplate(
                name="agent_no_overwrite_template",
                soul="draft docs",
                type=RoleTemplateType.USER,
            )
        )
        ctx = ToolCallContext(agent_id=1, team_id=self.team_id, chat_room=None)

        first = await save_agent(
            name="delta",
            role_template_name="agent_no_overwrite_template",
            _context=ctx,
        )
        second = await save_agent(
            name="delta",
            role_template_name="agent_no_overwrite_template",
            _context=ctx,
        )

        assert first["success"]
        assert first["agent"]["employ_status"] == "OFF_BOARD"
        assert not second["success"]
        assert "overwrite_existing" in second["message"]

    async def test_save_agent_rejects_invalid_inputs(self) -> None:
        """save_agent 应拒绝非法模板、非法 driver、非法工具权限和保留成员名。"""
        await gtRoleTemplateManager.save_role_template(
            GtRoleTemplate(
                name="agent_invalid_template",
                soul="draft docs",
                type=RoleTemplateType.USER,
            )
        )
        ctx = ToolCallContext(agent_id=1, team_id=self.team_id, chat_room=None)

        missing_template = await save_agent(
            name="echo",
            role_template_name="missing_template",
            _context=ctx,
        )
        invalid_driver = await save_agent(
            name="foxtrot",
            role_template_name="agent_invalid_template",
            driver="invalid_driver",
            _context=ctx,
        )
        invalid_allow_tools = await save_agent(
            name="golf",
            role_template_name="agent_invalid_template",
            allow_tools=["Category:Admin"],
            _context=ctx,
        )
        special_agent = await save_agent(
            name="OPERATOR",
            role_template_name="agent_invalid_template",
            _context=ctx,
        )

        assert not missing_template["success"]
        assert "未找到角色模板" in missing_template["message"]
        assert not invalid_driver["success"]
        assert "driver 只允许" in invalid_driver["message"]
        assert not invalid_allow_tools["success"]
        assert "管理员类别权限" in invalid_allow_tools["message"]
        assert not special_agent["success"]
        assert "保留成员" in special_agent["message"]

    async def test_delete_role_template_supports_missing_unused_and_in_use(self) -> None:
        """删除角色模板时应分别处理不存在、未引用、被引用三种情况。"""
        missing_result = await delete_role_template("missing_template")

        await gtRoleTemplateManager.save_role_template(
            GtRoleTemplate(
                name="deletable_template",
                soul="temporary",
                type=RoleTemplateType.USER,
            )
        )
        delete_result = await delete_role_template("deletable_template")

        in_use = await gtRoleTemplateManager.save_role_template(
            GtRoleTemplate(
                name="in_use_template",
                soul="bound to alice",
                type=RoleTemplateType.USER,
            )
        )
        alice = await gtAgentManager.get_agent(self.team_id, "alice")
        assert alice is not None
        alice.role_template_id = in_use.id
        await alice.aio_save()
        in_use_result = await delete_role_template("in_use_template")

        assert not missing_result["success"]
        assert "未找到角色模板" in missing_result["message"]
        assert delete_result["success"]
        assert await gtRoleTemplateManager.get_role_template_by_name("deletable_template") is None
        assert not in_use_result["success"]
        assert in_use_result["agents"] == [{"name": "alice", "team_id": self.team_id}]
        assert "alice" in in_use_result["message"]

    async def test_delete_role_template_rejects_system_template(self) -> None:
        """工具不允许删除 SYSTEM 角色模板。"""
        await gtRoleTemplateManager.save_role_template(
            GtRoleTemplate(
                name="system_delete_forbidden",
                soul="built in",
                type=RoleTemplateType.SYSTEM,
            )
        )

        result = await delete_role_template("system_delete_forbidden")

        assert not result["success"]
        assert "不允许通过工具删除" in result["message"]

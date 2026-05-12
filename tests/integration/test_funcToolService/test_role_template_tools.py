"""integration tests for role template management tools"""
import os
import sys
from typing import Optional

import service.agentService as agentService
import service.ormService as ormService
import service.persistenceService as persistenceService
import service.roomService as roomService
from constants import RoleTemplateType, ToolCategory
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
    save_role_template,
    wake_up_agent,
    send_chat_msg,
    finish_chat_turn,
)
from ...base import ServiceTestCase

TEAM = "test_team"

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


class TestRoleTemplateToolMetadata(ServiceTestCase):
    async def test_optional_list_str_maps_to_array(self) -> None:
        """Optional[list[str]] 应映射为 array。"""
        assert python_type_to_json_schema(Optional[list[str]]) == {"type": "array"}

    async def test_save_role_template_uses_array_schema_for_allowed_tools(self) -> None:
        """save_role_template 的 allowed_tools 参数应暴露为 array。"""
        props = get_function_metadata("save_role_template", save_role_template)["parameters"]["properties"]
        assert props["allowed_tools"]["type"] == "array"

    async def test_role_template_tools_registered(self) -> None:
        """role template 管理工具应加入注册表。"""
        load_func_tools()
        assert {
            "list_role_templates",
            "get_role_template",
            "save_role_template",
            "delete_role_template",
        } <= {t.function.name for t in get_tools()}

    async def test_role_template_tools_build(self) -> None:
        """role template 工具应能构建为 OpenAITool 定义。"""
        tools = build_tools([
            FuncTool("list_role_templates", list_role_templates),
            FuncTool("get_role_template", get_role_template),
            FuncTool("save_role_template", save_role_template),
            FuncTool("delete_role_template", delete_role_template),
        ])
        assert {tool.function.name for tool in tools} == {
            "list_role_templates",
            "get_role_template",
            "save_role_template",
            "delete_role_template",
        }

    async def test_role_template_tool_metadata_category_via_registry(self) -> None:
        """工具分类应通过 AgentToolRegistry 补齐。"""
        registry = AgentToolRegistry()
        for t in get_tools():
            registry.register(t, lambda x, y: None)
        
        list_tool = registry.get_registered_tool("list_role_templates")
        save_tool = registry.get_registered_tool("save_role_template")
        assert list_tool.category == ToolCategory.ADMIN
        assert save_tool.category == ToolCategory.ADMIN

    async def test_all_local_tools_define_category(self) -> None:
        """每个本地工具都应声明 category。"""
        load_func_tools()
        assert {t.function.name for t in get_tools()} <= set(CATEGORY_CONFIG)

    async def test_basic_chat_tools_use_basic_category(self) -> None:
        """基础行动工具应归类到 BASIC。"""
        assert CATEGORY_CONFIG["wake_up_agent"] == ToolCategory.BASIC
        assert CATEGORY_CONFIG["send_chat_msg"] == ToolCategory.BASIC
        assert CATEGORY_CONFIG["finish_chat_turn"] == ToolCategory.BASIC

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
        assert "save_role_template" not in read_tools
        # root leader 自动补齐 admin 分类
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
                allowed_tools=["get_time"],
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

    async def test_list_role_templates_with_search(self) -> None:
        """list_role_templates 应支持关键词搜索 (OR 逻辑，支持 i18n)。"""
        # 1. 准备测试数据
        await save_role_template(
            name="search_writer",
            type="USER",
            soul="draft documentation",
            allowed_tools=[],
            i18n={"display_name": {"zh-CN": "专业写手", "en": "Pro Writer"}}
        )
        await save_role_template(
            name="search_coder",
            type="USER",
            soul="write python code",
            allowed_tools=[],
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
        """save_role_template 应按名称执行全字段 upsert。"""
        create_result = await save_role_template(
            name="writer",
            type="USER",
            soul="draft docs",
            allowed_tools=["get_time"],
            model="gpt-4o-mini",
            i18n={"display_name": {"zh-CN": "写手", "en": "Writer"}},
        )
        update_result = await save_role_template(
            name="writer",
            type="SYSTEM",
            soul="draft docs carefully",
            allowed_tools=["get_time", "get_room_info"],
            model="gpt-4.1",
            i18n={"display_name": {"zh-CN": "高级写手", "en": "Senior Writer"}},
        )

        assert create_result["success"]
        assert "已创建角色模板 writer" in create_result["message"]
        assert update_result["success"]
        assert "已更新角色模板 writer" in update_result["message"]
        detail = await gtRoleTemplateManager.get_role_template_by_name("writer")
        assert detail is not None
        assert detail.type == RoleTemplateType.SYSTEM
        assert detail.soul == "draft docs carefully"
        assert detail.allowed_tools == ["get_time", "get_room_info"]
        assert detail.model == "gpt-4.1"
        assert detail.i18n["display_name"]["zh-CN"] == "高级写手"

    async def test_save_role_template_rejects_admin_tools(self) -> None:
        """save_role_template 应拒绝包含管理员工具或类别的配置。"""
        # 1. 拒绝 Category:Admin
        result_cat = await save_role_template(
            name="hacker",
            type="USER",
            soul="try to get admin",
            allowed_tools=["Category:Admin"],
        )
        assert result_cat["success"] is False
        assert "不允许分配管理员类别权限" in result_cat["message"]

        # 2. 拒绝具体的 ADMIN 工具
        result_tool = await save_role_template(
            name="hacker2",
            type="USER",
            soul="try to get admin tool",
            allowed_tools=["save_role_template"],
        )
        assert result_tool["success"] is False
        assert "不允许分配管理员工具权限" in result_tool["message"]

    async def test_save_role_template_rejects_invalid_type(self) -> None:
        """非法 type 应被工具层拒绝。"""
        result = await save_role_template(
            name="invalid_type_template",
            type="ADMIN",
            soul="noop",
            allowed_tools=[],
        )

        assert not result["success"]
        assert "SYSTEM 或 USER" in result["message"]

    async def test_save_role_template_rejects_system_create_and_update(self) -> None:
        """工具不允许创建或修改 SYSTEM 角色模板。"""
        create_result = await save_role_template(
            name="system_created_by_tool",
            type="SYSTEM",
            soul="noop",
            allowed_tools=[],
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
            allowed_tools=[],
        )

        assert not create_result["success"]
        assert "不允许通过工具创建" in create_result["message"]
        assert not update_result["success"]
        assert "不允许通过工具修改" in update_result["message"]

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

import os
import sys

import pytest

from tests.base import ServiceTestCase
from dal.db import gtDeptManager, gtTeamManager, gtAgentManager, gtRoleTemplateManager, gtRoomManager
from exception import TogoException
from model.dbModel.gtDept import GtDept
from model.dbModel.gtAgentHistory import GtAgentHistory
from model.dbModel.gtRoom import GtRoom
from model.dbModel.gtRoomMessage import GtRoomMessage
from model.dbModel.gtTeam import GtTeam
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtRoleTemplate import GtRoleTemplate
from service import deptService, ormService, roomService, teamService
from util.configTypes import DeptNodeConfig, TeamConfig, AgentConfig
from constants import DriverType, EmployStatus


if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")



class TestDeptService(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        await ormService.startup(cls._get_test_db_path())

    @classmethod
    async def async_teardown_class(cls):
        await ormService.shutdown()

    async def _reset_tables(self):
        await GtDept.delete().aio_execute()
        await GtAgent.delete().aio_execute()
        await GtRoomMessage.delete().aio_execute()
        await GtAgentHistory.delete().aio_execute()
        await GtRoom.delete().aio_execute()
        await GtTeam.delete().aio_execute()
        await GtRoleTemplate.delete().aio_execute()

    async def _convert_to_gt_agents(self, team_id: int, configs: list[AgentConfig]) -> list[GtAgent]:
        agents = []
        for cfg in configs:
            rt_id = await gtRoleTemplateManager.resolve_role_template_id_by_name(cfg.role_template)
            agents.append(GtAgent(
                team_id=team_id,
                name=cfg.name,
                role_template_id=rt_id,
                model=cfg.model or "",
                driver=cfg.driver,
                employ_status=EmployStatus.ON_BOARD,
            ))
        return agents

    async def _setup_team_with_agents(self, team_name: str, agent_names: list[str]) -> GtTeam:
        """创建 team 并写入 Agent，返回 GtTeam 对象。"""
        # 先创建角色模板
        await gtRoleTemplateManager.save_role_template(
            GtRoleTemplate(name="dummy", model="gpt-4o")
        )
        team = await gtTeamManager.save_team(GtTeam(name=team_name))
        configs = [AgentConfig(name=n, role_template="dummy") for n in agent_names]
        agents = await self._convert_to_gt_agents(team.id, configs)
        await gtAgentManager.batch_save_agents(team.id, agents)
        return team

    async def _disable_team(self, team_id: int) -> None:
        """停用团队，用于编辑组织树或成员前的准备。"""
        await gtTeamManager.set_team_enabled(team_id, False)

    async def _get_room_agent_names(self, room_id: int) -> list[str]:
        room = await gtRoomManager.get_room_by_id(room_id)
        assert room is not None
        agent_rows = await gtAgentManager.get_agents_by_ids(room.agent_ids or [])
        by_id = {agent.id: agent.name for agent in agent_rows}
        return [by_id.get(agent_id, str(agent_id)) for agent_id in room.agent_ids or []]

    async def _get_agent_id(self, team_id: int, agent_name: str, status: EmployStatus | None = EmployStatus.ON_BOARD) -> int:
        agent = await gtAgentManager.get_agent(team_id, agent_name, status=status)
        assert agent is not None
        return agent.id

    async def _to_dept_tree_node(self, team_id: int, node: DeptNodeConfig) -> GtDept:
        agent_rows = await gtAgentManager.get_team_agents_by_names(
            team_id,
            list(dict.fromkeys([*node.agents, node.manager])),
        )
        agent_id_map = {agent.name: agent.id for agent in agent_rows}
        return GtDept(
            name=node.dept_name,
            responsibility=node.responsibility,
            manager_id=agent_id_map.get(node.manager, 0),
            agent_ids=[agent_id_map.get(name, 0) for name in node.agents],
            i18n=node.i18n or {},
            children=[await self._to_dept_tree_node(team_id, child) for child in node.children],
        )

    # ------------------------------------------------------------------
    # gtDeptManager CRUD
    # ------------------------------------------------------------------

    async def test_dept_manager_upsert_and_get_by_name(self):
        await self._reset_tables()

        team = await self._setup_team_with_agents("t1", ["alice", "bob"])
        alice = await gtAgentManager.get_agent(team.id, "alice")
        bob = await gtAgentManager.get_agent(team.id, "bob")
        assert alice is not None and bob is not None

        dept = await gtDeptManager.save_dept(
            team_id=team.id,
            name="engineering",
            responsibility="build stuff",
            parent_id=None,
            manager_id=alice.id,
            agent_ids=[alice.id, bob.id],
        )
        assert dept.name == "engineering"
        assert dept.responsibility == "build stuff"
        assert dept.parent_id is None
        assert dept.manager_id == alice.id
        assert set(dept.agent_ids) == {alice.id, bob.id}

        fetched = await gtDeptManager.get_dept_by_name(team.id, "engineering")
        assert fetched is not None
        assert fetched.id == dept.id

        missing = await gtDeptManager.get_dept_by_name(team.id, "nonexistent")
        assert missing is None

    async def test_dept_manager_upsert_updates_on_conflict(self):
        await self._reset_tables()

        team = await self._setup_team_with_agents("t_upsert", ["alice", "bob", "charlie"])
        alice = await gtAgentManager.get_agent(team.id, "alice")
        bob = await gtAgentManager.get_agent(team.id, "bob")
        charlie = await gtAgentManager.get_agent(team.id, "charlie")
        assert alice is not None and bob is not None and charlie is not None

        first = await gtDeptManager.save_dept(
            team_id=team.id, name="eng", responsibility="v1",
            parent_id=None, manager_id=alice.id, agent_ids=[alice.id, bob.id],
        )
        second = await gtDeptManager.save_dept(
            team_id=team.id, name="eng", responsibility="v2",
            parent_id=None, manager_id=bob.id, agent_ids=[alice.id, bob.id, charlie.id],
        )

        # id 不变（upsert），内容已更新
        assert second.id == first.id
        assert second.responsibility == "v2"
        assert second.manager_id == bob.id
        assert charlie.id in second.agent_ids

    async def test_dept_manager_get_all_depts_ordered_by_id(self):
        await self._reset_tables()

        team = await self._setup_team_with_agents("t_all", ["alice", "bob"])
        alice = await gtAgentManager.get_agent(team.id, "alice")
        bob = await gtAgentManager.get_agent(team.id, "bob")
        assert alice is not None and bob is not None

        root = await gtDeptManager.save_dept(
            team_id=team.id, name="root", responsibility="", parent_id=None,
            manager_id=alice.id, agent_ids=[alice.id],
        )
        child = await gtDeptManager.save_dept(
            team_id=team.id, name="child", responsibility="", parent_id=root.id,
            manager_id=bob.id, agent_ids=[bob.id],
        )

        depts = await gtDeptManager.get_all_depts(team.id)
        assert len(depts) == 2
        assert depts[0].id == root.id
        assert depts[1].id == child.id

    # ------------------------------------------------------------------
    # deptService.overwrite_dept_tree
    # ------------------------------------------------------------------

    async def test_import_dept_tree_single_node(self):
        await self._reset_tables()

        team = await self._setup_team_with_agents("t_import", ["alice", "bob"])
        await self._disable_team(team.id)

        tree = DeptNodeConfig(dept_name="product",
            responsibility="owns the roadmap",
            manager="alice",
            agents=["alice", "bob"],
        )
        await deptService.overwrite_dept_tree(team.id, await self._to_dept_tree_node(team.id, tree))

        dept = await gtDeptManager.get_dept_by_name(team.id, "product")
        assert dept is not None
        assert dept.responsibility == "owns the roadmap"

        alice = await gtAgentManager.get_agent(team.id, "alice")
        assert alice is not None
        assert dept.manager_id == alice.id
        assert alice.id in dept.agent_ids

    async def test_import_dept_tree_hierarchical(self):
        await self._reset_tables()

        team = await self._setup_team_with_agents(
            "t_hier", ["cto", "eng_lead", "dev_a", "dev_b"]
        )
        await self._disable_team(team.id)

        tree = DeptNodeConfig(dept_name="company",
            responsibility="top level",
            manager="cto",
            agents=["cto", "eng_lead"],
            children=[
                DeptNodeConfig(dept_name="engineering",
                    responsibility="builds product",
                    manager="eng_lead",
                    agents=["eng_lead", "dev_a", "dev_b"],
                )
            ],
        )
        await deptService.overwrite_dept_tree(team.id, await self._to_dept_tree_node(team.id, tree))

        all_depts = await gtDeptManager.get_all_depts(team.id)
        assert len(all_depts) == 2

        company = await gtDeptManager.get_dept_by_name(team.id, "company")
        eng = await gtDeptManager.get_dept_by_name(team.id, "engineering")
        assert company is not None and eng is not None
        assert eng.parent_id == company.id

    async def test_overwrite_dept_tree_updates_existing(self):
        await self._reset_tables()

        team = await self._setup_team_with_agents("t_overwrite", ["alice", "bob", "charlie"])
        await self._disable_team(team.id)

        original = DeptNodeConfig(dept_name="dept_x",
            responsibility="original",
            manager="alice",
            agents=["alice", "bob"],
        )
        await deptService.overwrite_dept_tree(team.id, await self._to_dept_tree_node(team.id, original))

        # 第二次调用应更新已存在的部门
        modified = DeptNodeConfig(dept_name="dept_x",
            responsibility="updated",
            manager="alice",
            agents=["alice", "bob", "charlie"],
        )
        modified_root = await self._to_dept_tree_node(team.id, modified)
        existing = await gtDeptManager.get_dept_by_name(team.id, "dept_x")
        assert existing is not None
        modified_root.id = existing.id
        await deptService.overwrite_dept_tree(team.id, modified_root)

        dept = await gtDeptManager.get_dept_by_name(team.id, "dept_x")
        assert dept is not None
        assert dept.responsibility == "updated"
        charlie = await gtAgentManager.get_agent(team.id, "charlie")
        assert charlie is not None
        assert charlie.id in dept.agent_ids

    async def test_import_dept_tree_manager_not_in_members_raises(self):
        await self._reset_tables()

        team = await self._setup_team_with_agents("t_err", ["alice", "bob"])
        await self._disable_team(team.id)

        bad_tree = DeptNodeConfig(dept_name="broken",
            responsibility="",
            manager="charlie",  # charlie 不在 agents 中
            agents=["alice", "bob"],
        )
        with pytest.raises(TogoException) as exc_info:
            await deptService.overwrite_dept_tree(team.id, await self._to_dept_tree_node(team.id, bad_tree))
        assert exc_info.value.error_code == "DEPT_MANAGER_NOT_IN_AGENTS"

    async def test_import_dept_tree_unknown_agent_raises(self):
        await self._reset_tables()

        team = await self._setup_team_with_agents("t_unknown", ["alice"])
        await self._disable_team(team.id)

        bad_tree = DeptNodeConfig(dept_name="dept_y",
            responsibility="",
            manager="alice",
            agents=["alice", "ghost"],  # ghost 不在 team_agents 中
        )
        with pytest.raises(TogoException) as exc_info:
            await deptService.overwrite_dept_tree(team.id, await self._to_dept_tree_node(team.id, bad_tree))
        assert exc_info.value.error_code == "DEPT_AGENT_NOT_FOUND"

    # ------------------------------------------------------------------
    # deptService.get_dept_tree (round-trip)
    # ------------------------------------------------------------------

    async def test_get_dept_tree_round_trip(self):
        await self._reset_tables()

        team = await self._setup_team_with_agents(
            "t_round", ["cto", "dev_a", "dev_b"]
        )
        await self._disable_team(team.id)
        original = DeptNodeConfig(dept_name="root",
            responsibility="root dept",
            manager="cto",
            agents=["cto", "dev_a"],
            children=[
                DeptNodeConfig(dept_name="dev",
                    responsibility="development",
                    manager="dev_a",
                    agents=["dev_a", "dev_b"],
                )
            ],
        )
        await deptService.overwrite_dept_tree(team.id, await self._to_dept_tree_node(team.id, original))

        cto_id = await self._get_agent_id(team.id, "cto")
        dev_a_id = await self._get_agent_id(team.id, "dev_a")
        dev_b_id = await self._get_agent_id(team.id, "dev_b")

        rebuilt = await deptService.get_dept_tree(team.id)
        assert rebuilt is not None
        assert rebuilt.name == "root"
        assert rebuilt.responsibility == "root dept"
        assert rebuilt.manager_id == cto_id
        assert cto_id in rebuilt.agent_ids
        assert len(rebuilt.children) == 1

        child = rebuilt.children[0]
        assert child.name == "dev"
        assert child.responsibility == "development"
        assert child.manager_id == dev_a_id
        assert set(child.agent_ids) == {dev_a_id, dev_b_id}
        assert child.children == []

    async def test_get_dept_tree_returns_none_when_no_depts(self):
        await self._reset_tables()

        team = await self._setup_team_with_agents("t_empty", ["alice"])
        result = await deptService.get_dept_tree(team.id)
        assert result is None

    # ------------------------------------------------------------------
    # deptService.set_dept_manager
    # ------------------------------------------------------------------

    async def test_set_dept_manager_changes_manager(self):
        await self._reset_tables()

        team = await self._setup_team_with_agents("t_setmgr", ["alice", "bob"])
        await self._disable_team(team.id)
        tree = DeptNodeConfig(dept_name="the_dept",
            responsibility="",
            manager="alice",
            agents=["alice", "bob"],
        )
        await deptService.overwrite_dept_tree(team.id, await self._to_dept_tree_node(team.id, tree))

        bob_id = await self._get_agent_id(team.id, "bob")
        await deptService.set_dept_manager(team.id, "the_dept", bob_id)

        dept = await gtDeptManager.get_dept_by_name(team.id, "the_dept")
        bob = await gtAgentManager.get_agent(team.id, "bob")
        assert dept is not None and bob is not None
        assert dept.manager_id == bob.id

    async def test_set_dept_manager_agent_not_in_dept_raises(self):
        await self._reset_tables()

        team = await self._setup_team_with_agents("t_setmgr_err", ["alice", "bob", "charlie"])
        await self._disable_team(team.id)
        tree = DeptNodeConfig(dept_name="small_dept",
            responsibility="",
            manager="alice",
            agents=["alice", "bob"],
        )
        await deptService.overwrite_dept_tree(team.id, await self._to_dept_tree_node(team.id, tree))

        # charlie 不在 small_dept 中（已被设置为 OFF_BOARD）
        charlie_id = await self._get_agent_id(team.id, "charlie", status=None)
        with pytest.raises(TogoException) as exc_info:
            await deptService.set_dept_manager(team.id, "small_dept", charlie_id)
        assert exc_info.value.error_code == "AGENT_NOT_IN_DEPT"

    async def test_set_dept_manager_dept_not_found_raises(self):
        await self._reset_tables()

        team = await self._setup_team_with_agents("t_setmgr_nodept", ["alice"])

        alice_id = await self._get_agent_id(team.id, "alice")
        with pytest.raises(TogoException) as exc_info:
            await deptService.set_dept_manager(team.id, "ghost_dept", alice_id)
        assert exc_info.value.error_code == "DEPT_NOT_FOUND"

    # ------------------------------------------------------------------
    # get_off_board_agents
    # ------------------------------------------------------------------

    async def test_get_off_board_agents(self):
        await self._reset_tables()

        team = await self._setup_team_with_agents("t_offboard", ["alice", "bob", "charlie"])
        await self._disable_team(team.id)
        tree = DeptNodeConfig(dept_name="base",
            responsibility="",
            manager="alice",
            agents=["alice", "bob", "charlie"],
        )
        await deptService.overwrite_dept_tree(team.id, await self._to_dept_tree_node(team.id, tree))

        bob_id = await self._get_agent_id(team.id, "bob")
        charlie_id = await self._get_agent_id(team.id, "charlie")
        await (
            GtAgent.update(employ_status=EmployStatus.OFF_BOARD)
            .where((GtAgent.team_id == team.id) & (GtAgent.id.in_([bob_id, charlie_id])))
            .aio_execute()
        )

        off_board = await deptService.get_off_board_agents(team.id)
        names = {m.name for m in off_board}
        assert names == {"bob", "charlie"}
        assert all(m.employ_status == EmployStatus.OFF_BOARD for m in off_board)

    # ------------------------------------------------------------------
    # EmployStatus EnumField 序列化与反序列化
    # ------------------------------------------------------------------

    async def test_employ_status_enum_field_serialization(self):
        await self._reset_tables()

        team = await self._setup_team_with_agents("t_enum", ["alice"])

        alice = await gtAgentManager.get_agent(team.id, "alice")
        assert alice is not None
        # 默认应为 ON_BOARD
        assert alice.employ_status == EmployStatus.ON_BOARD

        # 直接写 OFF_BOARD，再读回，应能正确反序列化
        await (
            GtAgent.update(employ_status=EmployStatus.OFF_BOARD)
            .where(GtAgent.id == alice.id)
            .aio_execute()
        )
        alice_after = await gtAgentManager.get_agent(team.id, "alice", status=None)
        assert alice_after is not None
        assert alice_after.employ_status == EmployStatus.OFF_BOARD

        # 确认 DB 中存的是字符串 "OFF_BOARD"，而非数字或小写
        db = ormService.get_db()
        with db.allow_sync():
            cursor = db.execute_sql("SELECT employ_status FROM agents WHERE id = ?", (alice.id,))
            row = cursor.fetchone()
        assert row is not None
        assert row[0] == "OFF_BOARD"

    # ------------------------------------------------------------------
    # AgentConfig model/driver 字段持久化
    # ------------------------------------------------------------------

    async def test_team_agent_model_driver_persist_and_reload(self):
        await self._reset_tables()

        # 先创建角色模板
        await gtRoleTemplateManager.save_role_template(
            GtRoleTemplate(name="gpt_agent", model="gpt-4o")
        )
        await gtRoleTemplateManager.save_role_template(
            GtRoleTemplate(name="glm_agent", model="glm-4")
        )

        team = await gtTeamManager.save_team(GtTeam(name="t_model_driver"))
        configs = [
            AgentConfig(name="alice", role_template="gpt_agent", model="gpt-4o", driver=DriverType.NATIVE),
            AgentConfig(name="bob", role_template="glm_agent", model="", driver=DriverType.CLAUDE_SDK),
        ]
        agents = await self._convert_to_gt_agents(team.id, configs)
        await gtAgentManager.batch_save_agents(team.id, agents)

        saved_agents = await gtAgentManager.get_team_all_agents(team.id)
        agent_map = {agent.name: agent for agent in saved_agents}

        assert agent_map["alice"].model == "gpt-4o"
        assert agent_map["alice"].driver == DriverType.NATIVE
        assert agent_map["bob"].model == ""
        assert agent_map["bob"].driver == DriverType.CLAUDE_SDK

    # ------------------------------------------------------------------
    # get_agent_dept
    # ------------------------------------------------------------------

    async def test_get_agent_dept_returns_correct_dept(self):
        await self._reset_tables()

        team = await self._setup_team_with_agents("t_get_dept", ["alice", "bob", "charlie"])
        await self._disable_team(team.id)
        tree = DeptNodeConfig(
            dept_name="found_dept",
            responsibility="",
            manager="alice",
            agents=["alice", "bob"],
        )
        await deptService.overwrite_dept_tree(team.id, await self._to_dept_tree_node(team.id, tree))

        alice = await gtAgentManager.get_agent(team.id, "alice")
        bob = await gtAgentManager.get_agent(team.id, "bob")
        # charlie 不在树中，已被设置为 OFF_BOARD，需要不限状态查询
        charlie = await gtAgentManager.get_agent(team.id, "charlie", status=None)
        assert alice is not None and bob is not None and charlie is not None

        alice_dept = await deptService.get_agent_dept(team.id, alice.id)
        assert alice_dept is not None
        assert alice_dept.name == "found_dept"

        # charlie 不在任何部门
        charlie_dept = await deptService.get_agent_dept(team.id, charlie.id)
        assert charlie_dept is None

    # ------------------------------------------------------------------
    # overwrite_dept_tree 部门房间 agents
    # ------------------------------------------------------------------

    async def test_overwrite_dept_tree_creates_room_with_agents(self):
        """验证 overwrite_dept_tree 创建新部门房间时，部门 Agent 会被自动加入。"""
        await self._reset_tables()

        team = await self._setup_team_with_agents("t_room_create", ["alice", "bob", "charlie"])
        await self._disable_team(team.id)
        alice_id = await self._get_agent_id(team.id, "alice")
        bob_id = await self._get_agent_id(team.id, "bob")
        charlie_id = await self._get_agent_id(team.id, "charlie")

        root = GtDept(
            name="engineering",
            responsibility="开发部门",
            manager_id=alice_id,
            agent_ids=[alice_id, bob_id, charlie_id],
        )

        await deptService.overwrite_dept_tree(team.id, root)

        # 验证部门房间已创建
        dept = await gtDeptManager.get_dept_by_name(team.id, "engineering")
        assert dept is not None
        biz_id = f"DEPT:{dept.id}"
        room = await gtRoomManager.get_room_by_biz_id(team.id, biz_id)
        assert room is not None
        assert room.name == "engineering"
        assert "DEPT" in room.tags

        room_agents = await self._get_room_agent_names(room.id)
        assert set(room_agents) == {"alice", "bob", "charlie"}

    async def test_overwrite_dept_tree_updates_existing_room_agents(self):
        """验证 overwrite_dept_tree 更新已有部门房间时，Agent 列表会同步更新。"""
        await self._reset_tables()

        team = await self._setup_team_with_agents("t_room_update", ["alice", "bob", "charlie", "david"])
        await self._disable_team(team.id)
        alice_id = await self._get_agent_id(team.id, "alice")
        bob_id = await self._get_agent_id(team.id, "bob")
        charlie_id = await self._get_agent_id(team.id, "charlie")
        david_id = await self._get_agent_id(team.id, "david")

        # 第一次创建
        root = GtDept(
            name="marketing",
            responsibility="市场部门",
            manager_id=alice_id,
            agent_ids=[alice_id, bob_id],
        )
        await deptService.overwrite_dept_tree(team.id, root)

        dept = await gtDeptManager.get_dept_by_name(team.id, "marketing")
        assert dept is not None
        biz_id = f"DEPT:{dept.id}"
        room = await gtRoomManager.get_room_by_biz_id(team.id, biz_id)
        assert room is not None
        room_agents = await self._get_room_agent_names(room.id)
        assert set(room_agents) == {"alice", "bob"}

        # 第二次更新，增加成员
        root_updated = GtDept(
            id=dept.id,
            name="marketing",
            responsibility="市场部门",
            manager_id=alice_id,
            agent_ids=[alice_id, bob_id, charlie_id, david_id],
        )
        await deptService.overwrite_dept_tree(team.id, root_updated)

        room_after = await gtRoomManager.get_room_by_biz_id(team.id, biz_id)
        assert room_after is not None
        room_agents_after = await self._get_room_agent_names(room_after.id)
        assert set(room_agents_after) == {"alice", "bob", "charlie", "david"}

    async def test_overwrite_dept_tree_renames_existing_dept_room(self):
        """验证已存在部门改名后，对应部门群名称会同步更新。"""
        await self._reset_tables()

        team = await self._setup_team_with_agents("t_room_rename", ["alice", "bob"])
        await self._disable_team(team.id)
        alice_id = await self._get_agent_id(team.id, "alice")
        bob_id = await self._get_agent_id(team.id, "bob")

        root = GtDept(
            name="engineering",
            responsibility="开发部门",
            manager_id=alice_id,
            agent_ids=[alice_id, bob_id],
        )
        await deptService.overwrite_dept_tree(team.id, root)

        dept = await gtDeptManager.get_dept_by_name(team.id, "engineering")
        assert dept is not None
        biz_id = f"DEPT:{dept.id}"
        before_room = await gtRoomManager.get_room_by_biz_id(team.id, biz_id)
        assert before_room is not None
        assert before_room.name == "engineering"

        renamed = GtDept(
            id=dept.id,
            name="platform",
            responsibility="平台部门",
            manager_id=alice_id,
            agent_ids=[alice_id, bob_id],
        )
        await deptService.overwrite_dept_tree(team.id, renamed)

        after_room = await gtRoomManager.get_room_by_biz_id(team.id, biz_id)
        assert after_room is not None
        assert after_room.id == before_room.id
        assert after_room.name == "platform"
        assert after_room.initial_topic == f"这里是{renamed.name}部门的公共群聊，部门人员可在这里互相沟通。"
        assert "DEPT" in after_room.tags

    async def test_overwrite_dept_tree_keeps_room_display_name_i18n(self):
        """验证部门树 i18n 会写入 DEPT 房间 display_name。"""
        await self._reset_tables()

        team = await self._setup_team_with_agents("t_room_i18n", ["alice", "bob"])
        await self._disable_team(team.id)
        alice_id = await self._get_agent_id(team.id, "alice")
        bob_id = await self._get_agent_id(team.id, "bob")

        root = GtDept(
            name="engineering",
            responsibility="开发部门",
            manager_id=alice_id,
            agent_ids=[alice_id, bob_id],
            i18n={
                "dept_name": {
                    "zh-CN": "研发部",
                    "en": "R&D Dept",
                },
            },
        )
        await deptService.overwrite_dept_tree(team.id, root)

        dept = await gtDeptManager.get_dept_by_name(team.id, "engineering")
        assert dept is not None
        room = await gtRoomManager.get_room_by_biz_id(team.id, f"DEPT:{dept.id}")
        assert room is not None
        assert room.i18n["display_name"]["zh-CN"] == "研发部"
        assert room.i18n["display_name"]["en"] == "R&D Dept"

    async def test_load_team_rooms_keeps_dept_room_tags(self):
        """验证重新加载 Team 房间内存对象时，部门房间标签不会丢失。"""
        await self._reset_tables()
        await roomService.startup()

        try:
            team = await self._setup_team_with_agents("t_room_tags", ["alice", "bob"])
            await self._disable_team(team.id)
            alice_id = await self._get_agent_id(team.id, "alice")
            bob_id = await self._get_agent_id(team.id, "bob")

            root = GtDept(
                name="engineering",
                responsibility="开发部门",
                manager_id=alice_id,
                agent_ids=[alice_id, bob_id],
            )
            await deptService.overwrite_dept_tree(team.id, root)

            persisted_room = next(
                (room for room in await gtRoomManager.get_rooms_by_team(team.id) if room.name == "engineering"),
                None,
            )
            assert persisted_room is not None
            assert "DEPT" in persisted_room.tags

            await roomService.load_team_rooms(team.id)

            runtime_room = roomService.get_room_by_key("engineering@t_room_tags")
            assert "DEPT" in runtime_room.tags
        finally:
            roomService.shutdown()

    async def test_overwrite_dept_tree_hierarchical_rooms_all_have_agents(self):
        """验证层级部门结构中，每个部门房间都有对应的 Agent。"""
        await self._reset_tables()

        team = await self._setup_team_with_agents(
            "t_room_hier", ["cto", "ceo", "eng_mgr", "dev_a", "dev_b", "sales_mgr", "sales_a"]
        )
        await self._disable_team(team.id)
        cto_id = await self._get_agent_id(team.id, "cto")
        ceo_id = await self._get_agent_id(team.id, "ceo")
        eng_mgr_id = await self._get_agent_id(team.id, "eng_mgr")
        dev_a_id = await self._get_agent_id(team.id, "dev_a")
        dev_b_id = await self._get_agent_id(team.id, "dev_b")
        sales_mgr_id = await self._get_agent_id(team.id, "sales_mgr")
        sales_a_id = await self._get_agent_id(team.id, "sales_a")

        root = GtDept(
            name="company",
            responsibility="公司",
            manager_id=cto_id,
            agent_ids=[cto_id, ceo_id],  # 至少 2 人
            children=[
                GtDept(
                    name="engineering",
                    responsibility="技术部",
                    manager_id=eng_mgr_id,
                    agent_ids=[eng_mgr_id, dev_a_id, dev_b_id],
                ),
                GtDept(
                    name="sales",
                    responsibility="销售部",
                    manager_id=sales_mgr_id,
                    agent_ids=[sales_mgr_id, sales_a_id],
                ),
            ],
        )

        await deptService.overwrite_dept_tree(team.id, root)

        # 验证所有部门房间
        for dept_name, expected_agents in [
            ("company", {"cto", "ceo"}),
            ("engineering", {"eng_mgr", "dev_a", "dev_b"}),
            ("sales", {"sales_mgr", "sales_a"}),
        ]:
            dept = await gtDeptManager.get_dept_by_name(team.id, dept_name)
            assert dept is not None
            biz_id = f"DEPT:{dept.id}"
            room = await gtRoomManager.get_room_by_biz_id(team.id, biz_id)
            assert room is not None
            room_agents = await self._get_room_agent_names(room.id)
            assert set(room_agents) == expected_agents

    async def test_overwrite_dept_tree_requires_team_disabled(self):
        """验证编辑团队组织树之前必须停用团队。"""
        await self._reset_tables()

        team = await self._setup_team_with_agents("t_disabled_check", ["alice", "bob"])
        # 不停用团队，直接尝试编辑组织树
        alice_id = await self._get_agent_id(team.id, "alice")
        bob_id = await self._get_agent_id(team.id, "bob")

        root = GtDept(
            name="engineering",
            responsibility="开发部门",
            manager_id=alice_id,
            agent_ids=[alice_id, bob_id],
        )

        with pytest.raises(TogoException) as exc_info:
            await deptService.overwrite_dept_tree(team.id, root)
        assert exc_info.value.error_code == "team_not_stopped"
        assert "团队必须处于停用状态才能编辑组织树" in str(exc_info.value)

        # 停用后再编辑，应正常执行
        await self._disable_team(team.id)
        await deptService.overwrite_dept_tree(team.id, root)
        dept = await gtDeptManager.get_dept_by_name(team.id, "engineering")
        assert dept is not None

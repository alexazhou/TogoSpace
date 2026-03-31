import os
import sys

import aiosqlite
import pytest

from tests.base import ServiceTestCase
from dal.db import gtDeptManager, gtTeamManager, gtAgentManager, gtRoleTemplateManager, gtRoomManager
from exception import TeamAgentException
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
            rt_id = await gtAgentManager.resolve_role_template_id_by_name(cfg.role_template)
            agents.append(GtAgent(
                team_id=team_id,
                name=cfg.name,
                role_template_id=rt_id,
                model=cfg.model or "",
                driver=cfg.driver,
                employ_status=EmployStatus.ON_BOARD,
            ))
        return agents

    async def _setup_team_with_members(self, team_name: str, member_names: list[str]) -> GtTeam:
        """创建 team 并写入成员，返回 GtTeam 对象。"""
        # 先创建角色模板
        await gtRoleTemplateManager.save_role_template(
            GtRoleTemplate(name="dummy", model="gpt-4o")
        )
        team = await gtTeamManager.save_team(GtTeam(name=team_name))
        configs = [AgentConfig(name=n, role_template="dummy") for n in member_names]
        agents = await self._convert_to_gt_agents(team.id, configs)
        await gtAgentManager.batch_save_agents(team.id, agents)
        return team

    async def _get_room_member_names(self, room_id: int) -> list[str]:
        room = await gtRoomManager.get_room_by_id(room_id)
        assert room is not None
        agent_rows = await gtAgentManager.get_agents_by_ids(room.agent_ids or [])
        by_id = {agent.id: agent.name for agent in agent_rows}
        return [by_id.get(agent_id, str(agent_id)) for agent_id in room.agent_ids or []]

    async def _get_agent_id(self, team_id: int, member_name: str) -> int:
        agent = await gtAgentManager.get_agent(team_id, member_name)
        assert agent is not None
        return agent.id

    # ------------------------------------------------------------------
    # gtDeptManager CRUD
    # ------------------------------------------------------------------

    async def test_dept_manager_upsert_and_get_by_name(self):
        await self._reset_tables()

        team = await self._setup_team_with_members("t1", ["alice", "bob"])
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

        team = await self._setup_team_with_members("t_upsert", ["alice", "bob", "charlie"])
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

        team = await self._setup_team_with_members("t_all", ["alice", "bob"])
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
    # deptService.import_dept_tree
    # ------------------------------------------------------------------

    async def test_import_dept_tree_single_node(self):
        await self._reset_tables()

        team = await self._setup_team_with_members("t_import", ["alice", "bob"])

        tree = DeptNodeConfig(
            dept_name="product",
            dept_responsibility="owns the roadmap",
            manager="alice",
            members=["alice", "bob"],
        )
        await deptService.import_dept_tree(team.id, tree)

        dept = await gtDeptManager.get_dept_by_name(team.id, "product")
        assert dept is not None
        assert dept.responsibility == "owns the roadmap"

        alice = await gtAgentManager.get_agent(team.id, "alice")
        assert alice is not None
        assert dept.manager_id == alice.id
        assert alice.id in dept.agent_ids

    async def test_import_dept_tree_hierarchical(self):
        await self._reset_tables()

        team = await self._setup_team_with_members(
            "t_hier", ["cto", "eng_lead", "dev_a", "dev_b"]
        )

        tree = DeptNodeConfig(
            dept_name="company",
            dept_responsibility="top level",
            manager="cto",
            members=["cto"],
            children=[
                DeptNodeConfig(
                    dept_name="engineering",
                    dept_responsibility="builds product",
                    manager="eng_lead",
                    members=["eng_lead", "dev_a", "dev_b"],
                )
            ],
        )
        await deptService.import_dept_tree(team.id, tree)

        all_depts = await gtDeptManager.get_all_depts(team.id)
        assert len(all_depts) == 2

        company = await gtDeptManager.get_dept_by_name(team.id, "company")
        eng = await gtDeptManager.get_dept_by_name(team.id, "engineering")
        assert company is not None and eng is not None
        assert eng.parent_id == company.id

    async def test_import_dept_tree_idempotent_skips_on_second_call(self):
        await self._reset_tables()

        team = await self._setup_team_with_members("t_idem", ["alice", "bob", "charlie"])

        original = DeptNodeConfig(
            dept_name="dept_x",
            dept_responsibility="original",
            manager="alice",
            members=["alice", "bob"],
        )
        await deptService.import_dept_tree(team.id, original)

        # 第二次调用应整棵跳过
        modified = DeptNodeConfig(
            dept_name="dept_x",
            dept_responsibility="should_not_overwrite",
            manager="alice",
            members=["alice", "bob", "charlie"],
        )
        await deptService.import_dept_tree(team.id, modified)

        dept = await gtDeptManager.get_dept_by_name(team.id, "dept_x")
        assert dept is not None
        assert dept.responsibility == "original"
        charlie = await gtAgentManager.get_agent(team.id, "charlie")
        assert charlie is not None
        assert charlie.id not in dept.agent_ids

    async def test_import_dept_tree_manager_not_in_members_raises(self):
        await self._reset_tables()

        team = await self._setup_team_with_members("t_err", ["alice", "bob"])

        bad_tree = DeptNodeConfig(
            dept_name="broken",
            dept_responsibility="",
            manager="charlie",  # charlie 不在 members 中
            members=["alice", "bob"],
        )
        with pytest.raises(TeamAgentException) as exc_info:
            await deptService.import_dept_tree(team.id, bad_tree)
        assert exc_info.value.error_code == "DEPT_MANAGER_NOT_IN_MEMBERS"

    async def test_import_dept_tree_unknown_member_raises(self):
        await self._reset_tables()

        team = await self._setup_team_with_members("t_unknown", ["alice"])

        bad_tree = DeptNodeConfig(
            dept_name="dept_y",
            dept_responsibility="",
            manager="alice",
            members=["alice", "ghost"],  # ghost 不在 team_members 中
        )
        with pytest.raises(TeamAgentException) as exc_info:
            await deptService.import_dept_tree(team.id, bad_tree)
        assert exc_info.value.error_code == "DEPT_MEMBER_NOT_FOUND"

    # ------------------------------------------------------------------
    # deptService.get_dept_tree (round-trip)
    # ------------------------------------------------------------------

    async def test_get_dept_tree_round_trip(self):
        await self._reset_tables()

        team = await self._setup_team_with_members(
            "t_round", ["cto", "dev_a", "dev_b"]
        )
        original = DeptNodeConfig(
            dept_name="root",
            dept_responsibility="root dept",
            manager="cto",
            members=["cto"],
            children=[
                DeptNodeConfig(
                    dept_name="dev",
                    dept_responsibility="development",
                    manager="dev_a",
                    members=["dev_a", "dev_b"],
                )
            ],
        )
        await deptService.import_dept_tree(team.id, original)

        cto_id = await self._get_agent_id(team.id, "cto")
        dev_a_id = await self._get_agent_id(team.id, "dev_a")
        dev_b_id = await self._get_agent_id(team.id, "dev_b")

        rebuilt = await deptService.get_dept_tree(team.id)
        assert rebuilt is not None
        assert rebuilt.dept_name == "root"
        assert rebuilt.dept_responsibility == "root dept"
        assert rebuilt.manager_id == cto_id
        assert cto_id in rebuilt.member_ids
        assert len(rebuilt.children) == 1

        child = rebuilt.children[0]
        assert child.dept_name == "dev"
        assert child.dept_responsibility == "development"
        assert child.manager_id == dev_a_id
        assert set(child.member_ids) == {dev_a_id, dev_b_id}
        assert child.children == []

    async def test_get_dept_tree_returns_none_when_no_depts(self):
        await self._reset_tables()

        team = await self._setup_team_with_members("t_empty", ["alice"])
        result = await deptService.get_dept_tree(team.id)
        assert result is None

    # ------------------------------------------------------------------
    # deptService.remove_member_from_dept
    # ------------------------------------------------------------------

    async def test_remove_member_from_dept_sets_off_board(self):
        await self._reset_tables()

        team = await self._setup_team_with_members("t_remove", ["alice", "bob"])
        tree = DeptNodeConfig(
            dept_name="team_dept",
            dept_responsibility="",
            manager="alice",
            members=["alice", "bob"],
        )
        await deptService.import_dept_tree(team.id, tree)

        bob_id = await self._get_agent_id(team.id, "bob")
        await deptService.remove_member_from_dept(team.id, bob_id)

        bob = await gtAgentManager.get_agent(team.id, "bob")
        assert bob is not None
        assert bob.employ_status == EmployStatus.OFF_BOARD

        dept = await gtDeptManager.get_dept_by_name(team.id, "team_dept")
        assert dept is not None
        assert bob.id not in dept.agent_ids

    async def test_remove_member_from_dept_manager_without_new_manager_raises(self):
        await self._reset_tables()

        team = await self._setup_team_with_members("t_mgr_err", ["alice", "bob"])
        tree = DeptNodeConfig(
            dept_name="mgr_dept",
            dept_responsibility="",
            manager="alice",
            members=["alice", "bob"],
        )
        await deptService.import_dept_tree(team.id, tree)

        alice_id = await self._get_agent_id(team.id, "alice")
        with pytest.raises(TeamAgentException) as exc_info:
            await deptService.remove_member_from_dept(team.id, alice_id)
        assert exc_info.value.error_code == "MANAGER_REMOVAL_REQUIRES_NEW_MANAGER"

    async def test_remove_member_from_dept_manager_with_new_manager_succeeds(self):
        await self._reset_tables()

        team = await self._setup_team_with_members("t_mgr_ok", ["alice", "bob"])
        tree = DeptNodeConfig(
            dept_name="handoff_dept",
            dept_responsibility="",
            manager="alice",
            members=["alice", "bob"],
        )
        await deptService.import_dept_tree(team.id, tree)

        alice_id = await self._get_agent_id(team.id, "alice")
        bob_id = await self._get_agent_id(team.id, "bob")
        await deptService.remove_member_from_dept(team.id, alice_id, new_manager_id=bob_id)

        dept = await gtDeptManager.get_dept_by_name(team.id, "handoff_dept")
        assert dept is not None

        alice = await gtAgentManager.get_agent(team.id, "alice")
        bob = await gtAgentManager.get_agent(team.id, "bob")
        assert alice is not None and bob is not None

        assert alice.employ_status == EmployStatus.OFF_BOARD
        assert alice.id not in dept.agent_ids
        assert dept.manager_id == bob.id

    async def test_remove_member_from_dept_new_manager_not_in_dept_raises(self):
        await self._reset_tables()

        team = await self._setup_team_with_members("t_mgr_bad", ["alice", "bob", "charlie"])
        tree = DeptNodeConfig(
            dept_name="bad_handoff_dept",
            dept_responsibility="",
            manager="alice",
            members=["alice", "bob"],
        )
        await deptService.import_dept_tree(team.id, tree)

        # charlie 不在部门中
        alice_id = await self._get_agent_id(team.id, "alice")
        charlie_id = await self._get_agent_id(team.id, "charlie")
        with pytest.raises(TeamAgentException) as exc_info:
            await deptService.remove_member_from_dept(team.id, alice_id, new_manager_id=charlie_id)
        assert exc_info.value.error_code == "NEW_MANAGER_NOT_IN_DEPT"

    async def test_remove_member_from_dept_not_in_any_dept_sets_off_board(self):
        await self._reset_tables()

        team = await self._setup_team_with_members("t_nodept", ["alice"])
        # 没有任何 dept，直接 remove
        alice_id = await self._get_agent_id(team.id, "alice")
        await deptService.remove_member_from_dept(team.id, alice_id)

        alice = await gtAgentManager.get_agent(team.id, "alice")
        assert alice is not None
        assert alice.employ_status == EmployStatus.OFF_BOARD

    # ------------------------------------------------------------------
    # deptService.set_dept_manager
    # ------------------------------------------------------------------

    async def test_set_dept_manager_changes_manager(self):
        await self._reset_tables()

        team = await self._setup_team_with_members("t_setmgr", ["alice", "bob"])
        tree = DeptNodeConfig(
            dept_name="the_dept",
            dept_responsibility="",
            manager="alice",
            members=["alice", "bob"],
        )
        await deptService.import_dept_tree(team.id, tree)

        bob_id = await self._get_agent_id(team.id, "bob")
        await deptService.set_dept_manager(team.id, "the_dept", bob_id)

        dept = await gtDeptManager.get_dept_by_name(team.id, "the_dept")
        bob = await gtAgentManager.get_agent(team.id, "bob")
        assert dept is not None and bob is not None
        assert dept.manager_id == bob.id

    async def test_set_dept_manager_member_not_in_dept_raises(self):
        await self._reset_tables()

        team = await self._setup_team_with_members("t_setmgr_err", ["alice", "bob", "charlie"])
        tree = DeptNodeConfig(
            dept_name="small_dept",
            dept_responsibility="",
            manager="alice",
            members=["alice", "bob"],
        )
        await deptService.import_dept_tree(team.id, tree)

        # charlie 不在 small_dept 中
        charlie_id = await self._get_agent_id(team.id, "charlie")
        with pytest.raises(TeamAgentException) as exc_info:
            await deptService.set_dept_manager(team.id, "small_dept", charlie_id)
        assert exc_info.value.error_code == "MEMBER_NOT_IN_DEPT"

    async def test_set_dept_manager_dept_not_found_raises(self):
        await self._reset_tables()

        team = await self._setup_team_with_members("t_setmgr_nodept", ["alice"])

        alice_id = await self._get_agent_id(team.id, "alice")
        with pytest.raises(TeamAgentException) as exc_info:
            await deptService.set_dept_manager(team.id, "ghost_dept", alice_id)
        assert exc_info.value.error_code == "DEPT_NOT_FOUND"

    # ------------------------------------------------------------------
    # get_off_board_members
    # ------------------------------------------------------------------

    async def test_get_off_board_members(self):
        await self._reset_tables()

        team = await self._setup_team_with_members("t_offboard", ["alice", "bob", "charlie"])
        tree = DeptNodeConfig(
            dept_name="base",
            dept_responsibility="",
            manager="alice",
            members=["alice", "bob", "charlie"],
        )
        await deptService.import_dept_tree(team.id, tree)

        bob_id = await self._get_agent_id(team.id, "bob")
        charlie_id = await self._get_agent_id(team.id, "charlie")
        await deptService.remove_member_from_dept(team.id, bob_id)
        await deptService.remove_member_from_dept(team.id, charlie_id)

        off_board = await deptService.get_off_board_members(team.id)
        names = {m.name for m in off_board}
        assert names == {"bob", "charlie"}
        assert all(m.employ_status == EmployStatus.OFF_BOARD for m in off_board)

    # ------------------------------------------------------------------
    # EmployStatus EnumField 序列化与反序列化
    # ------------------------------------------------------------------

    async def test_employ_status_enum_field_serialization(self):
        await self._reset_tables()

        team = await self._setup_team_with_members("t_enum", ["alice"])

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
        alice_after = await gtAgentManager.get_agent(team.id, "alice")
        assert alice_after is not None
        assert alice_after.employ_status == EmployStatus.OFF_BOARD

        # 确认 DB 中存的是字符串 "OFF_BOARD"，而非数字或小写
        db_path = ormService.get_db_path()
        assert db_path is not None
        async with aiosqlite.connect(db_path) as conn:
            async with conn.execute(
                "SELECT employ_status FROM agents WHERE id = ?", (alice.id,)
            ) as cursor:
                row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "OFF_BOARD"

    # ------------------------------------------------------------------
    # AgentConfig model/driver 字段持久化
    # ------------------------------------------------------------------

    async def test_team_member_model_driver_persist_and_reload(self):
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

        saved_agents = await gtAgentManager.get_team_agents(team.id)
        member_map = {m.name: m for m in saved_agents}

        assert member_map["alice"].model == "gpt-4o"
        assert member_map["alice"].driver == DriverType.NATIVE
        assert member_map["bob"].model == ""
        assert member_map["bob"].driver == DriverType.CLAUDE_SDK

    # ------------------------------------------------------------------
    # get_member_dept
    # ------------------------------------------------------------------

    async def test_get_member_dept_returns_correct_dept(self):
        await self._reset_tables()

        team = await self._setup_team_with_members("t_get_dept", ["alice", "bob"])
        tree = DeptNodeConfig(
            dept_name="found_dept",
            dept_responsibility="",
            manager="alice",
            members=["alice"],
        )
        await deptService.import_dept_tree(team.id, tree)

        alice = await gtAgentManager.get_agent(team.id, "alice")
        bob = await gtAgentManager.get_agent(team.id, "bob")
        assert alice is not None and bob is not None

        alice_dept = await deptService.get_member_dept(team.id, alice.id)
        assert alice_dept is not None
        assert alice_dept.name == "found_dept"

        # bob 不在任何部门
        bob_dept = await deptService.get_member_dept(team.id, bob.id)
        assert bob_dept is None

    # ------------------------------------------------------------------
    # save_dept_tree 部门房间成员
    # ------------------------------------------------------------------

    async def test_save_dept_tree_creates_room_with_members(self):
        """验证 save_dept_tree 创建新部门房间时，部门成员会被自动加入。"""
        await self._reset_tables()

        team = await self._setup_team_with_members("t_room_create", ["alice", "bob", "charlie"])
        alice_id = await self._get_agent_id(team.id, "alice")
        bob_id = await self._get_agent_id(team.id, "bob")
        charlie_id = await self._get_agent_id(team.id, "charlie")

        root = deptService.DeptTreeNode(
            dept_name="engineering",
            dept_responsibility="开发部门",
            manager_id=alice_id,
            member_ids=[alice_id, bob_id, charlie_id],
        )

        await deptService.save_dept_tree(team.id, root)

        # 验证部门房间已创建
        dept = await gtDeptManager.get_dept_by_name(team.id, "engineering")
        assert dept is not None
        biz_id = f"DEPT:{dept.id}"
        room = await gtRoomManager.get_room_by_biz_id(team.id, biz_id)
        assert room is not None
        assert room.name == "engineering"
        assert "DEPT" in room.tags

        # 验证部门成员已加入房间
        room_members = await self._get_room_member_names(room.id)
        assert set(room_members) == {"alice", "bob", "charlie"}

    async def test_save_dept_tree_updates_existing_room_members(self):
        """验证 save_dept_tree 更新已有部门房间时，成员列表会同步更新。"""
        await self._reset_tables()

        team = await self._setup_team_with_members("t_room_update", ["alice", "bob", "charlie", "david"])
        alice_id = await self._get_agent_id(team.id, "alice")
        bob_id = await self._get_agent_id(team.id, "bob")
        charlie_id = await self._get_agent_id(team.id, "charlie")
        david_id = await self._get_agent_id(team.id, "david")

        # 第一次创建
        root = deptService.DeptTreeNode(
            dept_name="marketing",
            dept_responsibility="市场部门",
            manager_id=alice_id,
            member_ids=[alice_id, bob_id],
        )
        await deptService.save_dept_tree(team.id, root)

        dept = await gtDeptManager.get_dept_by_name(team.id, "marketing")
        assert dept is not None
        biz_id = f"DEPT:{dept.id}"
        room = await gtRoomManager.get_room_by_biz_id(team.id, biz_id)
        assert room is not None
        room_members = await self._get_room_member_names(room.id)
        assert set(room_members) == {"alice", "bob"}

        # 第二次更新，增加成员
        root_updated = deptService.DeptTreeNode(
            dept_id=dept.id,
            dept_name="marketing",
            dept_responsibility="市场部门",
            manager_id=alice_id,
            member_ids=[alice_id, bob_id, charlie_id, david_id],
        )
        await deptService.save_dept_tree(team.id, root_updated)

        room_after = await gtRoomManager.get_room_by_biz_id(team.id, biz_id)
        assert room_after is not None
        room_members_after = await self._get_room_member_names(room_after.id)
        assert set(room_members_after) == {"alice", "bob", "charlie", "david"}

    async def test_save_dept_tree_renames_existing_dept_room(self):
        """验证已存在部门改名后，对应部门群名称会同步更新。"""
        await self._reset_tables()

        team = await self._setup_team_with_members("t_room_rename", ["alice", "bob"])
        alice_id = await self._get_agent_id(team.id, "alice")
        bob_id = await self._get_agent_id(team.id, "bob")

        root = deptService.DeptTreeNode(
            dept_name="engineering",
            dept_responsibility="开发部门",
            manager_id=alice_id,
            member_ids=[alice_id, bob_id],
        )
        await deptService.save_dept_tree(team.id, root)

        dept = await gtDeptManager.get_dept_by_name(team.id, "engineering")
        assert dept is not None
        biz_id = f"DEPT:{dept.id}"
        before_room = await gtRoomManager.get_room_by_biz_id(team.id, biz_id)
        assert before_room is not None
        assert before_room.name == "engineering"

        renamed = deptService.DeptTreeNode(
            dept_id=dept.id,
            dept_name="platform",
            dept_responsibility="平台部门",
            manager_id=alice_id,
            member_ids=[alice_id, bob_id],
        )
        await deptService.save_dept_tree(team.id, renamed)

        after_room = await gtRoomManager.get_room_by_biz_id(team.id, biz_id)
        assert after_room is not None
        assert after_room.id == before_room.id
        assert after_room.name == "platform"
        assert after_room.initial_topic == "平台部门"
        assert "DEPT" in after_room.tags

    async def test_refresh_rooms_for_team_keeps_dept_room_tags(self):
        """验证热刷新运行态房间时，部门房间标签不会丢失。"""
        await self._reset_tables()
        await roomService.startup()

        try:
            team = await self._setup_team_with_members("t_room_tags", ["alice", "bob"])
            alice_id = await self._get_agent_id(team.id, "alice")
            bob_id = await self._get_agent_id(team.id, "bob")

            root = deptService.DeptTreeNode(
                dept_name="engineering",
                dept_responsibility="开发部门",
                manager_id=alice_id,
                member_ids=[alice_id, bob_id],
            )
            await deptService.save_dept_tree(team.id, root)

            persisted_room = next(
                (room for room in await gtRoomManager.get_rooms_by_team(team.id) if room.name == "engineering"),
                None,
            )
            assert persisted_room is not None
            assert "DEPT" in persisted_room.tags

            await roomService.refresh_rooms_for_team(team.id)

            runtime_room = roomService.get_room_by_key("engineering@t_room_tags")
            assert "DEPT" in runtime_room.tags
        finally:
            roomService.shutdown()

    async def test_save_dept_tree_hierarchical_rooms_all_have_members(self):
        """验证层级部门结构中，每个部门房间都有对应的成员。"""
        await self._reset_tables()

        team = await self._setup_team_with_members(
            "t_room_hier", ["cto", "ceo", "eng_mgr", "dev_a", "dev_b", "sales_mgr", "sales_a"]
        )
        cto_id = await self._get_agent_id(team.id, "cto")
        ceo_id = await self._get_agent_id(team.id, "ceo")
        eng_mgr_id = await self._get_agent_id(team.id, "eng_mgr")
        dev_a_id = await self._get_agent_id(team.id, "dev_a")
        dev_b_id = await self._get_agent_id(team.id, "dev_b")
        sales_mgr_id = await self._get_agent_id(team.id, "sales_mgr")
        sales_a_id = await self._get_agent_id(team.id, "sales_a")

        root = deptService.DeptTreeNode(
            dept_name="company",
            dept_responsibility="公司",
            manager_id=cto_id,
            member_ids=[cto_id, ceo_id],  # 至少 2 人
            children=[
                deptService.DeptTreeNode(
                    dept_name="engineering",
                    dept_responsibility="技术部",
                    manager_id=eng_mgr_id,
                    member_ids=[eng_mgr_id, dev_a_id, dev_b_id],
                ),
                deptService.DeptTreeNode(
                    dept_name="sales",
                    dept_responsibility="销售部",
                    manager_id=sales_mgr_id,
                    member_ids=[sales_mgr_id, sales_a_id],
                ),
            ],
        )

        await deptService.save_dept_tree(team.id, root)

        # 验证所有部门房间
        for dept_name, expected_members in [
            ("company", {"cto", "ceo"}),
            ("engineering", {"eng_mgr", "dev_a", "dev_b"}),
            ("sales", {"sales_mgr", "sales_a"}),
        ]:
            dept = await gtDeptManager.get_dept_by_name(team.id, dept_name)
            assert dept is not None
            biz_id = f"DEPT:{dept.id}"
            room = await gtRoomManager.get_room_by_biz_id(team.id, biz_id)
            assert room is not None
            room_members = await self._get_room_member_names(room.id)
            assert set(room_members) == expected_members

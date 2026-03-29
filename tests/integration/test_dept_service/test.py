import os
import sys

import aiosqlite
import pytest

from tests.base import ServiceTestCase
from dal.db import gtDeptManager, gtTeamManager, gtAgentManager
from exception import TeamAgentException
from model.dbModel.gtDept import GtDept
from model.dbModel.gtAgentHistory import GtAgentHistory
from model.dbModel.gtRoom import GtRoom
from model.dbModel.gtRoomMessage import GtRoomMessage
from model.dbModel.gtTeam import GtTeam
from model.dbModel.gtAgent import GtAgent
from service import deptService, ormService
from util.configTypes import DeptNodeConfig, TeamConfig, AgentConfig
from constants import DriverType, EmployStatus


if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


@pytest.mark.forked
class TestDeptService(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        await ormService.startup(cls.TEST_DB_PATH)

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

    async def _setup_team_with_members(self, team_name: str, member_names: list[str]) -> GtTeam:
        """创建 team 并写入成员，返回 GtTeam 对象。"""
        team = await gtTeamManager.upsert_team(TeamConfig(name=team_name))
        await gtAgentManager.upsert_agents(
            team.id,
            [AgentConfig(name=n, role_template="dummy") for n in member_names],
        )
        return team

    # ------------------------------------------------------------------
    # gtDeptManager CRUD
    # ------------------------------------------------------------------

    async def test_dept_manager_upsert_and_get_by_name(self):
        await self._reset_tables()

        team = await self._setup_team_with_members("t1", ["alice", "bob"])
        alice = await gtAgentManager.get_agent(team.id, "alice")
        bob = await gtAgentManager.get_agent(team.id, "bob")
        assert alice is not None and bob is not None

        dept = await gtDeptManager.upsert_dept(
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

        first = await gtDeptManager.upsert_dept(
            team_id=team.id, name="eng", responsibility="v1",
            parent_id=None, manager_id=alice.id, agent_ids=[alice.id, bob.id],
        )
        second = await gtDeptManager.upsert_dept(
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

        root = await gtDeptManager.upsert_dept(
            team_id=team.id, name="root", responsibility="", parent_id=None,
            manager_id=alice.id, agent_ids=[alice.id],
        )
        child = await gtDeptManager.upsert_dept(
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
    # deptService.get_dept_tree_async (round-trip)
    # ------------------------------------------------------------------

    async def test_get_dept_tree_async_round_trip(self):
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

        rebuilt = await deptService.get_dept_tree_async(team.id)
        assert rebuilt is not None
        assert rebuilt.dept_name == "root"
        assert rebuilt.dept_responsibility == "root dept"
        assert rebuilt.manager == "cto"
        assert "cto" in rebuilt.members
        assert len(rebuilt.children) == 1

        child = rebuilt.children[0]
        assert child.dept_name == "dev"
        assert child.dept_responsibility == "development"
        assert child.manager == "dev_a"
        assert set(child.members) == {"dev_a", "dev_b"}
        assert child.children == []

    async def test_get_dept_tree_async_returns_none_when_no_depts(self):
        await self._reset_tables()

        team = await self._setup_team_with_members("t_empty", ["alice"])
        result = await deptService.get_dept_tree_async(team.id)
        assert result is None

    # ------------------------------------------------------------------
    # deptService.remove_member
    # ------------------------------------------------------------------

    async def test_remove_member_sets_off_board(self):
        await self._reset_tables()

        team = await self._setup_team_with_members("t_remove", ["alice", "bob"])
        tree = DeptNodeConfig(
            dept_name="team_dept",
            dept_responsibility="",
            manager="alice",
            members=["alice", "bob"],
        )
        await deptService.import_dept_tree(team.id, tree)

        await deptService.remove_member(team.id, "bob")

        bob = await gtAgentManager.get_agent(team.id, "bob")
        assert bob is not None
        assert bob.employ_status == EmployStatus.OFF_BOARD

        dept = await gtDeptManager.get_dept_by_name(team.id, "team_dept")
        assert dept is not None
        assert bob.id not in dept.agent_ids

    async def test_remove_member_manager_without_new_manager_raises(self):
        await self._reset_tables()

        team = await self._setup_team_with_members("t_mgr_err", ["alice", "bob"])
        tree = DeptNodeConfig(
            dept_name="mgr_dept",
            dept_responsibility="",
            manager="alice",
            members=["alice", "bob"],
        )
        await deptService.import_dept_tree(team.id, tree)

        with pytest.raises(TeamAgentException) as exc_info:
            await deptService.remove_member(team.id, "alice")
        assert exc_info.value.error_code == "MANAGER_REMOVAL_REQUIRES_NEW_MANAGER"

    async def test_remove_member_manager_with_new_manager_succeeds(self):
        await self._reset_tables()

        team = await self._setup_team_with_members("t_mgr_ok", ["alice", "bob"])
        tree = DeptNodeConfig(
            dept_name="handoff_dept",
            dept_responsibility="",
            manager="alice",
            members=["alice", "bob"],
        )
        await deptService.import_dept_tree(team.id, tree)

        await deptService.remove_member(team.id, "alice", new_manager="bob")

        dept = await gtDeptManager.get_dept_by_name(team.id, "handoff_dept")
        assert dept is not None

        alice = await gtAgentManager.get_agent(team.id, "alice")
        bob = await gtAgentManager.get_agent(team.id, "bob")
        assert alice is not None and bob is not None

        assert alice.employ_status == EmployStatus.OFF_BOARD
        assert alice.id not in dept.agent_ids
        assert dept.manager_id == bob.id

    async def test_remove_member_new_manager_not_in_dept_raises(self):
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
        with pytest.raises(TeamAgentException) as exc_info:
            await deptService.remove_member(team.id, "alice", new_manager="charlie")
        assert exc_info.value.error_code == "NEW_MANAGER_NOT_IN_DEPT"

    async def test_remove_member_not_in_any_dept_sets_off_board(self):
        await self._reset_tables()

        team = await self._setup_team_with_members("t_nodept", ["alice"])
        # 没有任何 dept，直接 remove
        await deptService.remove_member(team.id, "alice")

        alice = await gtAgentManager.get_agent(team.id, "alice")
        assert alice is not None
        assert alice.employ_status == EmployStatus.OFF_BOARD

    # ------------------------------------------------------------------
    # deptService.move_member
    # ------------------------------------------------------------------

    async def test_move_member_to_another_dept(self):
        await self._reset_tables()

        team = await self._setup_team_with_members("t_move", ["alice", "bob", "charlie"])
        alice = await gtAgentManager.get_agent(team.id, "alice")
        bob = await gtAgentManager.get_agent(team.id, "bob")
        charlie = await gtAgentManager.get_agent(team.id, "charlie")
        assert alice is not None and bob is not None and charlie is not None

        # 两个部门：eng (alice, bob) / design (charlie)
        eng = await gtDeptManager.upsert_dept(
            team_id=team.id, name="eng", responsibility="", parent_id=None,
            manager_id=alice.id, agent_ids=[alice.id, bob.id],
        )
        design = await gtDeptManager.upsert_dept(
            team_id=team.id, name="design", responsibility="", parent_id=None,
            manager_id=charlie.id, agent_ids=[charlie.id],
        )

        # bob 从 eng 移入 design
        await deptService.move_member(team.id, "bob", "design")

        eng_after = await gtDeptManager.get_dept_by_name(team.id, "eng")
        design_after = await gtDeptManager.get_dept_by_name(team.id, "design")
        assert eng_after is not None and design_after is not None
        assert bob.id not in eng_after.agent_ids
        assert bob.id in design_after.agent_ids

        bob_after = await gtAgentManager.get_agent(team.id, "bob")
        assert bob_after is not None
        assert bob_after.employ_status == EmployStatus.ON_BOARD

    async def test_move_member_as_manager(self):
        await self._reset_tables()

        team = await self._setup_team_with_members("t_move_mgr", ["alice", "bob"])
        alice = await gtAgentManager.get_agent(team.id, "alice")
        bob = await gtAgentManager.get_agent(team.id, "bob")
        assert alice is not None and bob is not None

        dept = await gtDeptManager.upsert_dept(
            team_id=team.id, name="dept_m", responsibility="", parent_id=None,
            manager_id=alice.id, agent_ids=[alice.id],
        )

        await deptService.move_member(team.id, "bob", "dept_m", is_manager=True)

        dept_after = await gtDeptManager.get_dept_by_name(team.id, "dept_m")
        assert dept_after is not None
        assert bob.id in dept_after.agent_ids
        assert dept_after.manager_id == bob.id

    async def test_move_member_off_board_becomes_on_board(self):
        await self._reset_tables()

        team = await self._setup_team_with_members("t_rehire", ["alice", "bob"])
        tree = DeptNodeConfig(
            dept_name="main",
            dept_responsibility="",
            manager="alice",
            members=["alice", "bob"],
        )
        await deptService.import_dept_tree(team.id, tree)

        # 先把 bob 移出
        await deptService.remove_member(team.id, "bob")
        bob = await gtAgentManager.get_agent(team.id, "bob")
        assert bob is not None
        assert bob.employ_status == EmployStatus.OFF_BOARD

        # 再把 bob 移回
        await deptService.move_member(team.id, "bob", "main")
        bob_after = await gtAgentManager.get_agent(team.id, "bob")
        assert bob_after is not None
        assert bob_after.employ_status == EmployStatus.ON_BOARD

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

        await deptService.set_dept_manager(team.id, "the_dept", "bob")

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
        with pytest.raises(TeamAgentException) as exc_info:
            await deptService.set_dept_manager(team.id, "small_dept", "charlie")
        assert exc_info.value.error_code == "MEMBER_NOT_IN_DEPT"

    async def test_set_dept_manager_dept_not_found_raises(self):
        await self._reset_tables()

        team = await self._setup_team_with_members("t_setmgr_nodept", ["alice"])

        with pytest.raises(TeamAgentException) as exc_info:
            await deptService.set_dept_manager(team.id, "ghost_dept", "alice")
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

        await deptService.remove_member(team.id, "bob")
        await deptService.remove_member(team.id, "charlie")

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

        team = await gtTeamManager.upsert_team(TeamConfig(name="t_model_driver"))
        members = [
            AgentConfig(name="alice", role_template="gpt_agent", model="gpt-4o", driver=DriverType.NATIVE),
            AgentConfig(name="bob", role_template="glm_agent", model="", driver=DriverType.CLAUDE_SDK),
        ]
        await gtAgentManager.upsert_agents(team.id, members)

        cfg = await gtTeamManager.get_team_config("t_model_driver")
        assert cfg is not None
        member_map = {m.name: m for m in cfg.members}

        assert member_map["alice"].model == "gpt-4o"
        assert member_map["alice"].driver == DriverType.NATIVE
        assert member_map["bob"].model is None or member_map["bob"].model == ""
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

        alice_dept = await deptService.get_member_dept(team.id, "alice")
        assert alice_dept is not None
        assert alice_dept.name == "found_dept"

        # bob 不在任何部门
        bob_dept = await deptService.get_member_dept(team.id, "bob")
        assert bob_dept is None

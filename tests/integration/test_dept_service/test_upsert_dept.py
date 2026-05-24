import os
import sys

import pytest

from tests.base import ServiceTestCase
from dal.db import gtDeptManager, gtTeamManager, gtAgentManager, gtRoleTemplateManager
from exception import TogoException
from model.dbModel.gtDept import GtDept
from model.dbModel.gtAgentHistory import GtAgentHistory
from model.dbModel.gtRoom import GtRoom
from model.dbModel.gtRoomMessage import GtRoomMessage
from model.dbModel.gtTeam import GtTeam
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtRoleTemplate import GtRoleTemplate
from service import deptService, ormService
from util.configTypes import AgentConfig
from constants import EmployStatus


if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


class TestUpsertDept(ServiceTestCase):
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

    async def _setup_team_with_agents(self, team_name: str, agent_names: list[str]) -> GtTeam:
        await gtRoleTemplateManager.save_role_template(
            GtRoleTemplate(name="dummy", model="gpt-4o")
        )
        team = await gtTeamManager.save_team(GtTeam(name=team_name))
        configs = [AgentConfig(name=n, role_template="dummy") for n in agent_names]
        agents = []
        for cfg in configs:
            rt_id = await gtRoleTemplateManager.resolve_role_template_id_by_name(cfg.role_template)
            agents.append(GtAgent(
                team_id=team.id,
                name=cfg.name,
                role_template_id=rt_id,
                model=cfg.model or "",
                driver=cfg.driver,
                employ_status=EmployStatus.ON_BOARD,
            ))
        await gtAgentManager.batch_save_agents(team.id, agents)
        return team

    async def _get_agent_id(self, team_id: int, agent_name: str, status: EmployStatus | None = EmployStatus.ON_BOARD) -> int:
        agent = await gtAgentManager.get_agent(team_id, agent_name, status=status)
        assert agent is not None
        return agent.id

    # ------------------------------------------------------------------
    # deptService.upsert_dept
    # ------------------------------------------------------------------

    async def test_upsert_dept_creates_new_dept(self):
        """upsert_dept 可以创建新部门。"""
        await self._reset_tables()

        team = await self._setup_team_with_agents("t_upsert_create", ["alice", "bob"])
        alice_id = await self._get_agent_id(team.id, "alice")
        bob_id = await self._get_agent_id(team.id, "bob")

        saved = await deptService.upsert_dept(
            team_id=team.id,
            name="eng",
            responsibility="builds things",
            manager_id=alice_id,
            agent_ids=[alice_id, bob_id],
            parent_id=None,
        )

        assert saved.name == "eng"
        assert saved.manager_id == alice_id
        assert set(saved.agent_ids) == {alice_id, bob_id}
        assert saved.parent_id is None

    async def test_upsert_dept_updates_existing_dept(self):
        """upsert_dept 可以更新已有部门。"""
        await self._reset_tables()

        team = await self._setup_team_with_agents("t_upsert_update", ["alice", "bob", "charlie"])
        alice_id = await self._get_agent_id(team.id, "alice")
        bob_id = await self._get_agent_id(team.id, "bob")
        charlie_id = await self._get_agent_id(team.id, "charlie")

        first = await deptService.upsert_dept(
            team_id=team.id,
            name="eng",
            responsibility="v1",
            manager_id=alice_id,
            agent_ids=[alice_id, bob_id],
            parent_id=None,
        )
        updated = await deptService.upsert_dept(
            team_id=team.id,
            name="eng",
            responsibility="v2",
            manager_id=alice_id,
            agent_ids=[alice_id, bob_id, charlie_id],
            parent_id=None,
            dept_id=first.id,
        )

        assert updated.id == first.id
        assert updated.responsibility == "v2"
        assert charlie_id in updated.agent_ids

    async def test_upsert_dept_removes_member_from_other_dept(self):
        """新部门成员若已在其他部门，应从其他部门移除。"""
        await self._reset_tables()

        team = await self._setup_team_with_agents("t_upsert_dedup", ["alice", "bob", "charlie"])
        alice_id = await self._get_agent_id(team.id, "alice")
        bob_id = await self._get_agent_id(team.id, "bob")
        charlie_id = await self._get_agent_id(team.id, "charlie")

        # 先建一个包含 bob 的部门
        old_dept = await deptService.upsert_dept(
            team_id=team.id,
            name="old_dept",
            responsibility="",
            manager_id=alice_id,
            agent_ids=[alice_id, bob_id],
            parent_id=None,
        )

        # 新部门也包含 bob → bob 应从 old_dept 移除
        await deptService.upsert_dept(
            team_id=team.id,
            name="new_dept",
            responsibility="",
            manager_id=charlie_id,
            agent_ids=[charlie_id, bob_id],
            parent_id=None,
        )

        refreshed = await gtDeptManager.get_dept_by_name(team.id, "old_dept")
        assert refreshed is not None
        assert bob_id not in refreshed.agent_ids

    async def test_upsert_dept_manager_stays_in_parent_dept(self):
        """负责人可以同时保留在父部门中，不被移除。"""
        await self._reset_tables()

        team = await self._setup_team_with_agents(
            "t_upsert_mgr_parent", ["cto", "eng_lead", "dev_a"]
        )
        cto_id = await self._get_agent_id(team.id, "cto")
        eng_lead_id = await self._get_agent_id(team.id, "eng_lead")
        dev_a_id = await self._get_agent_id(team.id, "dev_a")

        # 先建父部门，eng_lead 在其中
        parent = await deptService.upsert_dept(
            team_id=team.id,
            name="company",
            responsibility="",
            manager_id=cto_id,
            agent_ids=[cto_id, eng_lead_id],
            parent_id=None,
        )

        # 建子部门，eng_lead 是负责人
        await deptService.upsert_dept(
            team_id=team.id,
            name="engineering",
            responsibility="",
            manager_id=eng_lead_id,
            agent_ids=[eng_lead_id, dev_a_id],
            parent_id=parent.id,
        )

        # eng_lead 应仍在父部门
        refreshed_parent = await gtDeptManager.get_dept_by_name(team.id, "company")
        assert refreshed_parent is not None
        assert eng_lead_id in refreshed_parent.agent_ids

    async def test_upsert_dept_non_manager_removed_from_parent_dept(self):
        """普通成员（非负责人）即使在父部门中，也应从父部门移除。"""
        await self._reset_tables()

        team = await self._setup_team_with_agents(
            "t_upsert_non_mgr", ["cto", "eng_lead", "dev_a"]
        )
        cto_id = await self._get_agent_id(team.id, "cto")
        eng_lead_id = await self._get_agent_id(team.id, "eng_lead")
        dev_a_id = await self._get_agent_id(team.id, "dev_a")

        # 父部门包含 dev_a
        parent = await deptService.upsert_dept(
            team_id=team.id,
            name="company",
            responsibility="",
            manager_id=cto_id,
            agent_ids=[cto_id, eng_lead_id, dev_a_id],
            parent_id=None,
        )

        # 子部门将 dev_a 作为普通成员 → 应从父部门移除
        await deptService.upsert_dept(
            team_id=team.id,
            name="engineering",
            responsibility="",
            manager_id=eng_lead_id,
            agent_ids=[eng_lead_id, dev_a_id],
            parent_id=parent.id,
        )

        refreshed_parent = await gtDeptManager.get_dept_by_name(team.id, "company")
        assert refreshed_parent is not None
        assert dev_a_id not in refreshed_parent.agent_ids

    async def test_upsert_dept_auto_adds_manager_to_parent(self):
        """负责人不在父部门时，应自动加入父部门成员列表。"""
        await self._reset_tables()

        team = await self._setup_team_with_agents(
            "t_upsert_mgr_add", ["cto", "eng_lead", "dev_a"]
        )
        cto_id = await self._get_agent_id(team.id, "cto")
        eng_lead_id = await self._get_agent_id(team.id, "eng_lead")
        dev_a_id = await self._get_agent_id(team.id, "dev_a")

        # 父部门不含 eng_lead
        parent = await deptService.upsert_dept(
            team_id=team.id,
            name="company",
            responsibility="",
            manager_id=cto_id,
            agent_ids=[cto_id],
            parent_id=None,
        )

        # 子部门以 eng_lead 为负责人 → eng_lead 应被自动加入父部门
        await deptService.upsert_dept(
            team_id=team.id,
            name="engineering",
            responsibility="",
            manager_id=eng_lead_id,
            agent_ids=[eng_lead_id, dev_a_id],
            parent_id=parent.id,
        )

        refreshed_parent = await gtDeptManager.get_dept_by_name(team.id, "company")
        assert refreshed_parent is not None
        assert eng_lead_id in refreshed_parent.agent_ids

    async def test_upsert_dept_manager_not_duplicated_in_parent(self):
        """负责人已在父部门时，不应重复添加。"""
        await self._reset_tables()

        team = await self._setup_team_with_agents(
            "t_upsert_no_dup", ["cto", "eng_lead", "dev_a"]
        )
        cto_id = await self._get_agent_id(team.id, "cto")
        eng_lead_id = await self._get_agent_id(team.id, "eng_lead")
        dev_a_id = await self._get_agent_id(team.id, "dev_a")

        parent = await deptService.upsert_dept(
            team_id=team.id,
            name="company",
            responsibility="",
            manager_id=cto_id,
            agent_ids=[cto_id, eng_lead_id],
            parent_id=None,
        )

        await deptService.upsert_dept(
            team_id=team.id,
            name="engineering",
            responsibility="",
            manager_id=eng_lead_id,
            agent_ids=[eng_lead_id, dev_a_id],
            parent_id=parent.id,
        )

        refreshed_parent = await gtDeptManager.get_dept_by_name(team.id, "company")
        assert refreshed_parent is not None
        assert refreshed_parent.agent_ids.count(eng_lead_id) == 1

    async def test_upsert_dept_off_board_agent_allowed(self):
        """OFF_BOARD 状态的成员可以加入部门。"""
        await self._reset_tables()

        team = await self._setup_team_with_agents("t_upsert_offboard", ["alice", "bob"])
        alice_id = await self._get_agent_id(team.id, "alice")
        bob_id = await self._get_agent_id(team.id, "bob")

        # 将 bob 设为 OFF_BOARD
        await (
            GtAgent.update(employ_status=EmployStatus.OFF_BOARD)
            .where(GtAgent.id == bob_id)
            .aio_execute()
        )

        saved = await deptService.upsert_dept(
            team_id=team.id,
            name="eng",
            responsibility="",
            manager_id=alice_id,
            agent_ids=[alice_id, bob_id],
            parent_id=None,
        )
        assert bob_id in saved.agent_ids

    async def test_upsert_dept_members_spread_across_depts_all_moved(self):
        """来自多个不同部门的成员，全部应移入新部门。"""
        await self._reset_tables()

        team = await self._setup_team_with_agents(
            "t_upsert_multi", ["alice", "bob", "charlie", "dave", "eve"]
        )
        alice_id = await self._get_agent_id(team.id, "alice")
        bob_id = await self._get_agent_id(team.id, "bob")
        charlie_id = await self._get_agent_id(team.id, "charlie")
        dave_id = await self._get_agent_id(team.id, "dave")
        eve_id = await self._get_agent_id(team.id, "eve")

        await deptService.upsert_dept(
            team_id=team.id, name="dept_a", responsibility="",
            manager_id=alice_id, agent_ids=[alice_id, bob_id], parent_id=None,
        )
        await deptService.upsert_dept(
            team_id=team.id, name="dept_b", responsibility="",
            manager_id=charlie_id, agent_ids=[charlie_id, dave_id], parent_id=None,
        )

        # 新部门抢走 bob 和 dave
        await deptService.upsert_dept(
            team_id=team.id, name="dept_c", responsibility="",
            manager_id=eve_id, agent_ids=[eve_id, bob_id, dave_id], parent_id=None,
        )

        dept_a = await gtDeptManager.get_dept_by_name(team.id, "dept_a")
        dept_b = await gtDeptManager.get_dept_by_name(team.id, "dept_b")
        assert dept_a is not None and dept_b is not None
        assert bob_id not in dept_a.agent_ids
        assert dave_id not in dept_b.agent_ids

    # ------------------------------------------------------------------
    # 情况1: DEPT_MANAGER_ALREADY_LEADS
    # 规则：指定的 leader 不能已经是其他部门的 leader
    # ------------------------------------------------------------------

    async def test_upsert_dept_raises_when_leader_already_leads_another_dept(self):
        """平级部门：alice 已是 dept_a 的 leader，不能再成为 dept_b 的 leader。"""
        await self._reset_tables()

        team = await self._setup_team_with_agents(
            "t_upsert_leader_dup", ["alice", "bob", "charlie"]
        )
        alice_id = await self._get_agent_id(team.id, "alice")
        bob_id = await self._get_agent_id(team.id, "bob")
        charlie_id = await self._get_agent_id(team.id, "charlie")

        await deptService.upsert_dept(
            team_id=team.id, name="dept_a", responsibility="",
            manager_id=alice_id, agent_ids=[alice_id, bob_id], parent_id=None,
        )

        with pytest.raises(TogoException) as exc_info:
            await deptService.upsert_dept(
                team_id=team.id, name="dept_b", responsibility="",
                manager_id=alice_id, agent_ids=[alice_id, charlie_id], parent_id=None,
            )
        assert exc_info.value.error_code == "DEPT_MANAGER_ALREADY_LEADS"

    async def test_upsert_dept_raises_when_parent_leader_becomes_child_leader(self):
        """父子层级：alice 已是父部门的 leader，不能再成为子部门的 leader。"""
        await self._reset_tables()

        team = await self._setup_team_with_agents(
            "t_upsert_parent_child_leader", ["alice", "bob", "charlie"]
        )
        alice_id = await self._get_agent_id(team.id, "alice")
        bob_id = await self._get_agent_id(team.id, "bob")
        charlie_id = await self._get_agent_id(team.id, "charlie")

        parent = await deptService.upsert_dept(
            team_id=team.id, name="company", responsibility="",
            manager_id=alice_id, agent_ids=[alice_id, bob_id], parent_id=None,
        )

        with pytest.raises(TogoException) as exc_info:
            await deptService.upsert_dept(
                team_id=team.id, name="engineering", responsibility="",
                manager_id=alice_id, agent_ids=[alice_id, charlie_id], parent_id=parent.id,
            )
        assert exc_info.value.error_code == "DEPT_MANAGER_ALREADY_LEADS"

    async def test_upsert_dept_updating_with_same_leader_does_not_raise(self):
        """更新已有部门时保持同一 leader，不应触发 DEPT_MANAGER_ALREADY_LEADS。"""
        await self._reset_tables()

        team = await self._setup_team_with_agents(
            "t_upsert_same_leader", ["alice", "bob", "charlie"]
        )
        alice_id = await self._get_agent_id(team.id, "alice")
        bob_id = await self._get_agent_id(team.id, "bob")
        charlie_id = await self._get_agent_id(team.id, "charlie")

        first = await deptService.upsert_dept(
            team_id=team.id, name="dept_a", responsibility="v1",
            manager_id=alice_id, agent_ids=[alice_id, bob_id], parent_id=None,
        )

        # 更新 dept_a，alice 继续担任 leader（传入 dept_id）
        updated = await deptService.upsert_dept(
            team_id=team.id, name="dept_a", responsibility="v2",
            manager_id=alice_id, agent_ids=[alice_id, bob_id, charlie_id],
            parent_id=None, dept_id=first.id,
        )
        assert updated.id == first.id
        assert updated.manager_id == alice_id

    # ------------------------------------------------------------------
    # 情况2: DEPT_MANAGER_CONFLICT
    # 规则：被跨部门移走的成员，不能是其原部门的 leader
    # ------------------------------------------------------------------

    async def test_upsert_dept_raises_when_moving_away_dept_leader(self):
        """平级部门：alice 是 dept_a 的 leader，不能作为普通成员加入 dept_b。"""
        await self._reset_tables()

        team = await self._setup_team_with_agents(
            "t_upsert_leader_conflict", ["alice", "bob", "charlie"]
        )
        alice_id = await self._get_agent_id(team.id, "alice")
        bob_id = await self._get_agent_id(team.id, "bob")
        charlie_id = await self._get_agent_id(team.id, "charlie")

        await deptService.upsert_dept(
            team_id=team.id, name="dept_a", responsibility="",
            manager_id=alice_id, agent_ids=[alice_id, bob_id], parent_id=None,
        )

        with pytest.raises(TogoException) as exc_info:
            await deptService.upsert_dept(
                team_id=team.id, name="dept_b", responsibility="",
                manager_id=charlie_id, agent_ids=[charlie_id, alice_id], parent_id=None,
            )
        assert exc_info.value.error_code == "DEPT_MANAGER_CONFLICT"

    async def test_upsert_dept_raises_when_dept_leader_added_to_child_dept(self):
        """父子层级：alice 是 dept_a 的 leader，不能作为普通成员加入 dept_a 的子部门。"""
        await self._reset_tables()

        team = await self._setup_team_with_agents(
            "t_upsert_leader_child_conflict", ["alice", "bob", "charlie"]
        )
        alice_id = await self._get_agent_id(team.id, "alice")
        bob_id = await self._get_agent_id(team.id, "bob")
        charlie_id = await self._get_agent_id(team.id, "charlie")

        parent = await deptService.upsert_dept(
            team_id=team.id, name="dept_a", responsibility="",
            manager_id=alice_id, agent_ids=[alice_id, bob_id], parent_id=None,
        )

        # alice 是 dept_a 的 leader，不能作为普通成员加入子部门
        with pytest.raises(TogoException) as exc_info:
            await deptService.upsert_dept(
                team_id=team.id, name="dept_a_child", responsibility="",
                manager_id=charlie_id, agent_ids=[charlie_id, alice_id], parent_id=parent.id,
            )
        assert exc_info.value.error_code == "DEPT_MANAGER_CONFLICT"

    async def test_upsert_dept_child_manager_stays_in_parent_no_conflict(self):
        """子部门 manager 保留在父部门（例外路径），不应触发 DEPT_MANAGER_CONFLICT。"""
        await self._reset_tables()

        team = await self._setup_team_with_agents(
            "t_upsert_mgr_no_conflict", ["cto", "eng_lead", "dev_a"]
        )
        cto_id = await self._get_agent_id(team.id, "cto")
        eng_lead_id = await self._get_agent_id(team.id, "eng_lead")
        dev_a_id = await self._get_agent_id(team.id, "dev_a")

        parent = await deptService.upsert_dept(
            team_id=team.id, name="company", responsibility="",
            manager_id=cto_id, agent_ids=[cto_id, eng_lead_id], parent_id=None,
        )

        # eng_lead 作为子部门 leader，按例外规则保留在父部门，不报错
        await deptService.upsert_dept(
            team_id=team.id, name="engineering", responsibility="",
            manager_id=eng_lead_id, agent_ids=[eng_lead_id, dev_a_id], parent_id=parent.id,
        )

        refreshed_parent = await gtDeptManager.get_dept_by_name(team.id, "company")
        assert refreshed_parent is not None
        assert eng_lead_id in refreshed_parent.agent_ids

    async def test_upsert_dept_update_parent_with_child_manager_no_conflict(self):
        """更新父部门（如改名）时，子部门主管仍在成员列表中，不应触发 DEPT_MANAGER_CONFLICT。"""
        await self._reset_tables()

        team = await self._setup_team_with_agents(
            "t_upsert_update_parent", ["cto", "eng_lead", "dev_a"]
        )
        cto_id = await self._get_agent_id(team.id, "cto")
        eng_lead_id = await self._get_agent_id(team.id, "eng_lead")
        dev_a_id = await self._get_agent_id(team.id, "dev_a")

        # 1. 创建父部门，eng_lead 是普通成员
        parent = await deptService.upsert_dept(
            team_id=team.id, name="old_parent", responsibility="v1",
            manager_id=cto_id, agent_ids=[cto_id, eng_lead_id], parent_id=None,
        )

        # 2. 创建子部门，eng_lead 是其 manager
        await deptService.upsert_dept(
            team_id=team.id, name="child_dept", responsibility="",
            manager_id=eng_lead_id, agent_ids=[eng_lead_id, dev_a_id], parent_id=parent.id,
        )

        # 3. 更新父部门（模拟改名），eng_lead 仍在成员列表中 → 不应报错
        updated = await deptService.upsert_dept(
            team_id=team.id, name="new_parent", responsibility="v2",
            manager_id=cto_id, agent_ids=[cto_id, eng_lead_id],
            parent_id=None, dept_id=parent.id,
        )

        assert updated.name == "new_parent"
        assert updated.responsibility == "v2"
        assert eng_lead_id in updated.agent_ids

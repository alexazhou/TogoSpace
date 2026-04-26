import os
import sqlite3
import sys

import pytest

import service.ormService as ormService
import service.presetService as presetService
import service.roomService as roomService
import service.teamService as teamService
from constants import AgentHistoryTag, AgentHistoryStatus, AgentTaskStatus, AgentTaskType, DriverType, EmployStatus, OpenaiApiRole, RoleTemplateType, RoomType
from dal.db import (
    gtRoleTemplateManager,
    gtAgentHistoryManager,
    gtAgentTaskManager,
    gtRoomManager,
    gtRoomMessageManager,
    gtTeamManager,
    gtAgentManager,
)
from model.dbModel.gtRoleTemplate import GtRoleTemplate
from model.dbModel.gtAgentHistory import GtAgentHistory
from model.dbModel.gtAgentTask import GtAgentTask
from model.dbModel.gtRoom import GtRoom
from model.dbModel.gtRoomMessage import GtRoomMessage
from model.dbModel.gtTeam import GtTeam
from model.dbModel.gtAgent import GtAgent
from util import llmApiUtil
from util.configTypes import TeamConfig, AgentConfig, TeamRoomConfig
from tests.base import ServiceTestCase


if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")



class TestDalManagers(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        db_path = cls._get_test_db_path()
        await ormService.startup(db_path)

    @classmethod
    async def async_teardown_class(cls):
        await ormService.shutdown()

    async def _reset_tables(self):
        gtAgentManager.clear_agent_cache()  # 清空缓存，避免测试间数据污染
        await GtRoleTemplate.delete().aio_execute()
        await GtAgent.delete().aio_execute()
        await GtAgentTask.delete().aio_execute()
        await GtRoomMessage.delete().aio_execute()
        await GtAgentHistory.delete().aio_execute()
        await GtRoom.delete().aio_execute()
        await GtTeam.delete().aio_execute()

    async def _save_role_template(
        self,
        template_name: str,
        model: str | None,
        soul: str = "",
        template_type: RoleTemplateType = RoleTemplateType.SYSTEM,
        allowed_tools: list[str] | None = None,
    ) -> GtRoleTemplate:
        return await gtRoleTemplateManager.save_role_template(
            GtRoleTemplate(
                name=template_name,
                model=model,
                soul=soul,
                type=template_type,
                allowed_tools=allowed_tools,
            )
        )

    async def _get_room_agent_names(self, room_id: int) -> list[str]:
        room = await gtRoomManager.get_room_by_id(room_id)
        assert room is not None
        agent_rows = await gtAgentManager.get_agents_by_ids(room.agent_ids or [])
        by_id = {agent.id: agent.name for agent in agent_rows}
        return [by_id.get(agent_id, str(agent_id)) for agent_id in room.agent_ids or []]

    # ------------------------------------------------------------------
    # gtRoleTemplateManager
    # ------------------------------------------------------------------
    async def test_role_template_manager_upsert_and_query_with_model(self):
        await self._reset_tables()

        saved_1 = await self._save_role_template(
            "alice",
            "glm-4.7",
            allowed_tools=["Read"],
        )
        assert saved_1.name == "alice"
        assert saved_1.model == "glm-4.7"
        assert saved_1.type == RoleTemplateType.SYSTEM
        assert saved_1.allowed_tools == ["Read"]

        saved_2 = await self._save_role_template(
            "alice",
            "gpt-4o",
            allowed_tools=["list_dir"],
        )
        assert saved_2.id == saved_1.id
        assert saved_2.model == "gpt-4o"
        assert saved_2.allowed_tools == ["list_dir"]

        row = await gtRoleTemplateManager.get_role_template_by_name("alice")
        assert row is not None
        assert row.model == "gpt-4o"
        assert row.type == RoleTemplateType.SYSTEM
        assert row.allowed_tools == ["list_dir"]

    async def test_role_template_table_has_model_column(self):
        await self._reset_tables()

        with sqlite3.connect(self._get_test_db_path()) as conn:
            cols = conn.execute("PRAGMA table_info('role_templates')").fetchall()
        col_names = {c[1] for c in cols}
        assert "model" in col_names
        assert "name" in col_names
        assert "type" in col_names
        assert "allowed_tools" in col_names

    # ------------------------------------------------------------------
    # gtTeamManager
    # ------------------------------------------------------------------
    async def test_team_manager_get_upsert_delete_and_exists(self):
        await self._reset_tables()

        created = await gtTeamManager.save_team(GtTeam(
            name="team_a",
            config={"slogan": "alpha"},
        ))
        assert created.name == "team_a"
        assert created.config == {"slogan": "alpha"}
        assert await gtTeamManager.team_exists("team_a") is True

        by_name = await gtTeamManager.get_team("team_a")
        by_id = await gtTeamManager.get_team_by_id(created.id)
        assert by_name is not None and by_name.id == created.id
        assert by_id is not None and by_id.name == "team_a"

        created.config = {"rules": "sync first", "slogan": "beta"}
        updated = await gtTeamManager.save_team(created)
        assert updated.id == created.id
        assert updated.config == {"rules": "sync first", "slogan": "beta"}

        await gtTeamManager.delete_team("team_a")
        assert await gtTeamManager.team_exists("team_a") is False
        deleted_row = await gtTeamManager.get_team("team_a")
        assert deleted_row is None

    async def test_team_manager_get_all_teams_returns_only_enabled_sorted(self):
        await self._reset_tables()

        await gtTeamManager.save_team(GtTeam(name="team_c"))
        await gtTeamManager.save_team(GtTeam(name="team_a"))
        await gtTeamManager.save_team(GtTeam(name="team_b"))
        await gtTeamManager.delete_team("team_b")

        teams = await gtTeamManager.get_all_teams()
        # 按 id 排序：team_c (id=1), team_a (id=2)
        assert [t.name for t in teams] == ["team_c", "team_a"]

    async def test_team_manager_persists_members_and_rooms(self):
        await self._reset_tables()

        # 先创建角色模板
        await self._save_role_template("alice", "gpt-4o")
        await self._save_role_template("bob", "gpt-4o")

        team_a = await gtTeamManager.save_team(GtTeam(
            name="team_a",
            config={"slogan": "ship fast"},
        ))
        team_b = await gtTeamManager.save_team(GtTeam(name="team_b"))
        configs = [
            AgentConfig(name="alice_1", role_template="alice"),
            AgentConfig(name="bob_1", role_template="bob"),
        ]
        agents = await ServiceTestCase.convert_to_gt_agents(team_a.id, configs)
        await gtAgentManager.batch_save_agents(team_a.id, agents)

        await roomService.create_team_rooms(team_a.id, await ServiceTestCase.convert_to_gt_rooms(team_a.id, [TeamRoomConfig(
            name="general",
            initial_topic="hello",
            max_turns=6,
            agents=["alice_1", "bob_1"],
        )]))
        room = next((item for item in await gtRoomManager.get_rooms_by_team(team_a.id) if item.name == "general"), None)
        assert room is not None
        # Note: upsert_rooms now handles agents internally

        team_a_agents = await gtAgentManager.get_team_all_agents(team_a.id)
        assert [(m.name, m.role_template_id) for m in team_a_agents] == [
            ("alice_1", team_a_agents[0].role_template_id),
            ("bob_1", team_a_agents[1].role_template_id),
        ]
        assert team_a.config == {"slogan": "ship fast"}
        assert room.name == "general"
        assert room.initial_topic == "hello"
        assert room.max_turns == 6
        assert await self._get_room_agent_names(room.id) == ["alice_1", "bob_1"]

        teams = await gtTeamManager.get_all_teams()
        assert [team.name for team in teams] == ["team_a", "team_b"]
        team_b_rooms = await gtRoomManager.get_rooms_by_team(team_b.id)
        assert team_b_rooms == []

    async def test_team_manager_import_team_from_config_imports_and_skips_existing(self):
        await self._reset_tables()

        # 先创建角色模板
        await self._save_role_template("alice", "gpt-4o")
        await self._save_role_template("bob", "gpt-4o")
        await self._save_role_template("charlie", "gpt-4o")

        payload = TeamConfig(
            name="imported",
            agents=[
                AgentConfig(name="alice_1", role_template="alice"),
                AgentConfig(name="bob_1", role_template="bob"),
            ],
            preset_rooms=[TeamRoomConfig(
                name="r1",
                initial_topic="topic 1",
                max_turns=8,
                agents=["alice_1", "bob_1"],
            )],
        )
        await presetService._import_team_from_config(payload)

        imported = await gtTeamManager.get_team("imported")
        assert imported is not None
        room = next((item for item in await gtRoomManager.get_rooms_by_team(imported.id) if item.name == "r1"), None)
        assert room is not None
        assert room.max_turns == 8
        assert await self._get_room_agent_names(room.id) == ["alice_1", "bob_1"]

        # 已存在时应跳过导入，不覆盖已有记录
        await presetService._import_team_from_config(TeamConfig(
            name="imported",
            agents=[AgentConfig(name="charlie", role_template="charlie")],
            preset_rooms=[TeamRoomConfig(name="r2", agents=["OPERATOR", "charlie"])],
        ))
        imported_after = await gtTeamManager.get_team("imported")
        assert imported_after is not None
        assert next((item for item in await gtRoomManager.get_rooms_by_team(imported_after.id) if item.name == "r2"), None) is None

    # ------------------------------------------------------------------
    # gtAgentManager
    # ------------------------------------------------------------------
    async def test_agent_manager_get_agents_by_ids_empty_list_short_circuits(self, monkeypatch):
        await self._reset_tables()

        def _select_should_not_be_called(*args, **kwargs):
            raise AssertionError("GtAgent.select should not be called when agent_ids is empty")

        monkeypatch.setattr(gtAgentManager.GtAgent, "select", _select_should_not_be_called)

        rows = await gtAgentManager.get_agents_by_ids([])
        assert rows == []

    async def test_agent_manager_batch_save_agents_uses_insert_many_for_new_rows(self, monkeypatch):
        await self._reset_tables()

        await self._save_role_template("rt_a", "gpt-4o")
        await self._save_role_template("rt_b", "gpt-4o")
        team = await gtTeamManager.save_team(GtTeam(name="batch_insert_agents_team"))

        agents = await ServiceTestCase.convert_to_gt_agents(team.id, [
            AgentConfig(name="alice", role_template="rt_a"),
            AgentConfig(name="bob", role_template="rt_b"),
        ])

        # 防回退：新增成员应走 insert_many，而不是逐条 insert
        def _insert_should_not_be_called(cls, *args, **kwargs):
            raise AssertionError("GtAgent.insert should not be called when batch creating agents")

        monkeypatch.setattr(gtAgentManager.GtAgent, "insert", classmethod(_insert_should_not_be_called))

        await gtAgentManager.batch_save_agents(team.id, agents)

        rows = await gtAgentManager.get_team_all_agents(team.id)
        assert [row.name for row in rows] == ["alice", "bob"]
        assert [row.employee_number for row in rows] == [1, 2]

    async def test_agent_manager_batch_save_agents_rejects_mismatched_team_id(self):
        await self._reset_tables()

        await self._save_role_template("rt_a", "gpt-4o")
        team = await gtTeamManager.save_team(GtTeam(name="batch_save_team_id_check"))

        role_template_id = await gtRoleTemplateManager.resolve_role_template_id_by_name("rt_a")
        wrong_team_agent = GtAgent(
            team_id=team.id + 1,
            name="alice",
            role_template_id=role_template_id,
            model="",
            driver=DriverType.NATIVE,
            employ_status=EmployStatus.ON_BOARD,
        )

        with pytest.raises(ValueError, match="all agents must have team_id"):
            await gtAgentManager.batch_save_agents(team.id, [wrong_team_agent])

    # ------------------------------------------------------------------
    # gtRoomManager
    # ------------------------------------------------------------------
    async def test_room_manager_get_rooms(self):
        await self._reset_tables()

        team = await gtTeamManager.save_team(GtTeam(name="room_team"))
        await roomService.create_team_rooms(team.id, await ServiceTestCase.convert_to_gt_rooms(team.id, [
            TeamRoomConfig(name="z_room", max_turns=2, agents=["alice", "bob"]),
            TeamRoomConfig(name="a_room", max_turns=3, agents=["OPERATOR", "alice"]),
        ]))

        rooms = await gtRoomManager.get_rooms_by_team(team.id)
        assert [r.name for r in rooms] == ["a_room", "z_room"]

        a_room = next((item for item in rooms if item.name == "a_room"), None)
        assert a_room is not None
        assert a_room.type == RoomType.PRIVATE
        assert next((item for item in rooms if item.name == "missing"), None) is None

    async def test_room_manager_get_rooms_by_names_keeps_input_order(self):
        await self._reset_tables()

        team = await gtTeamManager.save_team(GtTeam(name="room_query_team"))
        await roomService.create_team_rooms(team.id, await ServiceTestCase.convert_to_gt_rooms(team.id, [
            TeamRoomConfig(name="a_room", agents=["alice"]),
            TeamRoomConfig(name="b_room", agents=["bob"]),
            TeamRoomConfig(name="c_room", agents=["alice", "bob"]),
        ]))

        rooms = await gtRoomManager.get_rooms_by_team_and_names(
            team.id,
            ["c_room", "missing_room", "a_room", "c_room"],
        )
        assert [room.name for room in rooms] == ["c_room", "a_room", "c_room"]

    async def test_room_manager_save_room_create_and_update(self):
        await self._reset_tables()

        team = await gtTeamManager.save_team(GtTeam(name="ensure_team"))
        first = await gtRoomManager.save_room(GtRoom(
            team_id=team.id,
            name="stable",
            type=RoomType.GROUP,
            initial_topic="t1",
            max_turns=4,
            agent_ids=[],
            biz_id=None,
            tags=[],
        ))

        first.type = RoomType.PRIVATE
        first.initial_topic = "t2"
        first.max_turns = 9
        second = await gtRoomManager.save_room(first)

        assert second.id == first.id
        assert second.type == RoomType.PRIVATE
        assert second.initial_topic == "t2"
        assert second.max_turns == 9

    async def test_room_manager_batch_save_rooms_create_and_update(self):
        await self._reset_tables()

        team = await gtTeamManager.save_team(GtTeam(name="batch_save_team"))

        existing = await gtRoomManager.save_room(GtRoom(
            team_id=team.id,
            name="existing_room",
            type=RoomType.GROUP,
            initial_topic="old_topic",
            max_turns=3,
            agent_ids=[],
            biz_id=None,
            tags=[],
        ))

        existing.initial_topic = "updated_topic"
        existing.max_turns = 8

        new_room = GtRoom(
            team_id=team.id,
            name="new_room",
            type=RoomType.PRIVATE,
            initial_topic="new_topic",
            max_turns=5,
            agent_ids=[],
            biz_id=None,
            tags=["tagA"],
        )

        await gtRoomManager.batch_save_rooms([existing, new_room])

        rooms = await gtRoomManager.get_rooms_by_team(team.id)
        assert [room.name for room in rooms] == ["existing_room", "new_room"]

        existing_after = next(room for room in rooms if room.name == "existing_room")
        assert existing_after.initial_topic == "updated_topic"
        assert existing_after.max_turns == 8

        new_after = next(room for room in rooms if room.name == "new_room")
        assert new_after.type == RoomType.PRIVATE
        assert new_after.initial_topic == "new_topic"
        assert new_after.max_turns == 5
        assert new_after.tags == ["tagA"]

    async def test_room_manager_upsert_rooms_delete_replace_and_defaults(self):
        await self._reset_tables()

        team = await gtTeamManager.save_team(GtTeam(name="upsert_team"))
        await roomService.create_team_rooms(team.id, await ServiceTestCase.convert_to_gt_rooms(team.id, [
            TeamRoomConfig(name="old_room", max_turns=2, agents=["alice"]),
        ]))
        await roomService.overwrite_team_rooms(team.id, [
            GtRoom(
                team_id=team.id,
                name="new_room_1",
                type=RoomType.GROUP,
                initial_topic="",
                max_turns=10,
                agent_ids=[],
                biz_id=None,
                tags=[],
            ),
            GtRoom(
                team_id=team.id,
                name="new_room_2",
                type=RoomType.GROUP,
                initial_topic="x",
                max_turns=10,
                agent_ids=[],
                biz_id=None,
                tags=[],
            ),
        ])

        rooms = await gtRoomManager.get_rooms_by_team(team.id)
        assert [r.name for r in rooms] == ["new_room_1", "new_room_2"]
        assert all(r.type == RoomType.GROUP for r in rooms)
        assert all(r.max_turns == 10 for r in rooms)

    async def test_room_manager_delete_room_and_delete_rooms_by_team(self):
        await self._reset_tables()

        team = await gtTeamManager.save_team(GtTeam(name="delete_team"))
        await roomService.create_team_rooms(team.id, await ServiceTestCase.convert_to_gt_rooms(team.id, [
            TeamRoomConfig(name="r1", agents=["alice"]),
            TeamRoomConfig(name="r2", agents=["bob"]),
        ]))
        r1 = next((item for item in await gtRoomManager.get_rooms_by_team(team.id) if item.name == "r1"), None)
        assert r1 is not None

        await gtRoomManager.delete_room(r1.id)
        names_after_one_delete = [r.name for r in await gtRoomManager.get_rooms_by_team(team.id)]
        assert names_after_one_delete == ["r2"]

        await gtRoomManager.delete_rooms_by_team(team.id)
        assert await gtRoomManager.get_rooms_by_team(team.id) == []

    async def test_room_manager_delete_rooms_by_biz_ids_not_in_keeps_non_dept_null_biz_id(self):
        await self._reset_tables()

        team = await gtTeamManager.save_team(GtTeam(name="biz_cleanup_team"))

        non_dept_null = await gtRoomManager.save_room(GtRoom(
            team_id=team.id,
            name="non_dept_null",
            type=RoomType.GROUP,
            initial_topic="",
            max_turns=5,
            agent_ids=[],
            biz_id=None,
            tags=[],
        ))

        await gtRoomManager.delete_rooms_by_biz_ids_not_in(team.id, [])

        assert await gtRoomManager.get_room_by_id(non_dept_null.id) is not None

    async def test_room_manager_save_and_get_room_state(self):
        await self._reset_tables()

        team = await gtTeamManager.save_team(GtTeam(name="state_team"))
        room = await gtRoomManager.save_room(GtRoom(
            team_id=team.id,
            name="state_room",
            type=RoomType.GROUP,
            initial_topic="",
            max_turns=5,
            agent_ids=[],
        ))

        read_index, turn_pos = await gtRoomManager.get_room_state(room.id)
        assert read_index is None
        assert turn_pos == 0

        state = {"alice": 1, "bob": 3}
        await gtRoomManager.update_room_state(room.id, state, turn_pos=2)
        read_index, turn_pos = await gtRoomManager.get_room_state(room.id)
        assert read_index == state
        assert turn_pos == 2

        read_index_missing, turn_pos_missing = await gtRoomManager.get_room_state(999999)
        assert read_index_missing is None
        assert turn_pos_missing == 0

    # ------------------------------------------------------------------
    # gtRoomManager Member Management
    # ------------------------------------------------------------------
    async def test_room_manager_get_upsert_delete_members(self):
        await self._reset_tables()

        # 先创建角色模板
        await self._save_role_template("alice", "gpt-4o")
        await self._save_role_template("bob", "gpt-4o")
        await self._save_role_template("charlie", "gpt-4o")

        team = await gtTeamManager.save_team(GtTeam(name="agent_team"))
        configs = [
            AgentConfig(name="alice", role_template="alice"),
            AgentConfig(name="bob", role_template="bob"),
            AgentConfig(name="charlie", role_template="charlie"),
        ]
        agents = await ServiceTestCase.convert_to_gt_agents(team.id, configs)
        await gtAgentManager.batch_save_agents(team.id, agents)
        room = await gtRoomManager.save_room(GtRoom(
            team_id=team.id,
            name="agent_room",
            type=RoomType.GROUP,
            initial_topic="",
            max_turns=5,
            agent_ids=[],
        ))
        saved_agents = await gtAgentManager.get_team_all_agents(team.id)
        agent_ids = {agent.name: agent.id for agent in saved_agents}

        assert await self._get_room_agent_names(room.id) == []

        await roomService.update_room_agents(room.id, [agent_ids["charlie"], agent_ids["alice"]])
        assert await self._get_room_agent_names(room.id) == ["charlie", "alice"]

        # upsert 会覆盖旧成员
        await roomService.update_room_agents(room.id, [agent_ids["bob"]])
        assert await self._get_room_agent_names(room.id) == ["bob"]

        # clear agents
        await roomService.update_room_agents(room.id, [])
        assert await self._get_room_agent_names(room.id) == []

    # ------------------------------------------------------------------
    # gtRoomMessageManager
    # ------------------------------------------------------------------
    async def test_room_message_manager_append_and_query(self):
        await self._reset_tables()

        # 先创建角色模板
        await self._save_role_template("alice", "gpt-4o")
        await self._save_role_template("bob", "gpt-4o")

        team = await gtTeamManager.save_team(GtTeam(name="msg_team"))
        room = await gtRoomManager.save_room(GtRoom(
            team_id=team.id,
            name="msg_room",
            type=RoomType.GROUP,
            initial_topic="",
            max_turns=5,
            agent_ids=[],
        ))

        configs = [
            AgentConfig(name="alice", role_template="alice"),
            AgentConfig(name="bob", role_template="bob"),
        ]
        agents = await ServiceTestCase.convert_to_gt_agents(team.id, configs)
        await gtAgentManager.batch_save_agents(team.id, agents)
        alice = await gtAgentManager.get_agent(team.id, "alice")
        bob = await gtAgentManager.get_agent(team.id, "bob")
        assert alice is not None and bob is not None

        m1 = await gtRoomMessageManager.append_room_message(room.id, alice.id, "hello", "2026-03-23T10:00:00")
        m2 = await gtRoomMessageManager.append_room_message(room.id, bob.id, "world", "2026-03-23T10:01:00")
        m3 = await gtRoomMessageManager.append_room_message(room.id, alice.id, "again", "2026-03-23T10:02:00")

        assert m1.id < m2.id < m3.id
        all_msgs = await gtRoomMessageManager.get_room_messages(room.id)
        assert [m.content for m in all_msgs] == ["hello", "world", "again"]

        after_m1 = await gtRoomMessageManager.get_room_messages(room.id, after_id=m1.id)
        assert [m.content for m in after_m1] == ["world", "again"]

    # ------------------------------------------------------------------
    # gtAgentHistoryManager
    # ------------------------------------------------------------------
    async def test_agent_history_manager_append_single_is_idempotent(self):
        await self._reset_tables()

        # 先创建角色模板
        await self._save_role_template("alice", "gpt-4o")

        team = await gtTeamManager.save_team(GtTeam(name="history_team"))
        configs = [AgentConfig(name="alice", role_template="alice")]
        agents = await ServiceTestCase.convert_to_gt_agents(team.id, configs)
        await gtAgentManager.batch_save_agents(team.id, agents)
        alice = await gtAgentManager.get_agent(team.id, "alice")
        assert alice is not None

        first = GtAgentHistory(
            agent_id=alice.id,
            seq=1,
            role=OpenaiApiRole.USER,
            message=llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "v1"),
            tags=[AgentHistoryTag.ROOM_TURN_BEGIN],
        )
        saved_1 = await gtAgentHistoryManager.append_agent_history_message(first)
        assert saved_1.agent_id == alice.id
        assert saved_1.seq == 1
        assert saved_1.message == llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "v1")
        assert saved_1.status == AgentHistoryStatus.INIT
        assert saved_1.error_message is None
        assert saved_1.tags == [AgentHistoryTag.ROOM_TURN_BEGIN]

        duplicate = GtAgentHistory(
            agent_id=alice.id,
            seq=1,
            role=OpenaiApiRole.USER,
            message=llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "v2"),
            tags=[AgentHistoryTag.COMPACT_SUMMARY],
        )
        saved_2 = await gtAgentHistoryManager.append_agent_history_message(duplicate)
        assert saved_2.id == saved_1.id
        assert saved_2.message == llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "v1")
        assert saved_2.tags == [AgentHistoryTag.ROOM_TURN_BEGIN]

    async def test_agent_history_manager_append_and_get_sorted(self):
        await self._reset_tables()

        # 先创建角色模板
        await self._save_role_template("alice", "gpt-4o")
        await self._save_role_template("bob", "gpt-4o")

        team = await gtTeamManager.save_team(GtTeam(name="history_team_2"))
        configs = [
            AgentConfig(name="alice", role_template="alice"),
            AgentConfig(name="bob", role_template="bob"),
        ]
        agents = await ServiceTestCase.convert_to_gt_agents(team.id, configs)
        await gtAgentManager.batch_save_agents(team.id, agents)
        alice = await gtAgentManager.get_agent(team.id, "alice")
        bob = await gtAgentManager.get_agent(team.id, "bob")
        assert alice is not None and bob is not None

        items = [
            GtAgentHistory(
                agent_id=alice.id,
                seq=2,
                role=OpenaiApiRole.USER,
                message=llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "2"),
                tags=[AgentHistoryTag.COMPACT_SUMMARY],
            ),
            GtAgentHistory(
                agent_id=alice.id,
                seq=1,
                role=OpenaiApiRole.USER,
                message=llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "1"),
                tags=[AgentHistoryTag.ROOM_TURN_BEGIN],
            ),
            GtAgentHistory(
                agent_id=bob.id,
                seq=1,
                role=OpenaiApiRole.USER,
                message=llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "b1"),
                tags=[],
            ),
        ]
        for item in items:
            await gtAgentHistoryManager.append_agent_history_message(item)

        alice_history = await gtAgentHistoryManager.get_agent_history(alice.id)
        assert [h.seq for h in alice_history] == [1, 2]
        assert [h.message for h in alice_history] == [
            llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "1"),
            llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "2"),
        ]
        assert [h.tags for h in alice_history] == [
            [AgentHistoryTag.ROOM_TURN_BEGIN],
            [AgentHistoryTag.COMPACT_SUMMARY],
        ]

        bob_history = await gtAgentHistoryManager.get_agent_history(bob.id)
        assert [h.seq for h in bob_history] == [1]
        assert [h.tags for h in bob_history] == [[]]

    async def test_agent_history_manager_update_status_by_id(self):
        await self._reset_tables()

        await self._save_role_template("alice", "gpt-4o")

        team = await gtTeamManager.save_team(GtTeam(name="history_team_3"))
        configs = [AgentConfig(name="alice", role_template="alice")]
        agents = await ServiceTestCase.convert_to_gt_agents(team.id, configs)
        await gtAgentManager.batch_save_agents(team.id, agents)
        alice = await gtAgentManager.get_agent(team.id, "alice")
        assert alice is not None

        saved = await gtAgentHistoryManager.append_agent_history_message(
            GtAgentHistory(
                agent_id=alice.id,
                seq=1,
                role=OpenaiApiRole.USER,
                message=llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "v1"),
                tags=[],
            )
        )
        assert saved.id is not None
        assert saved.status == AgentHistoryStatus.INIT

        updated = await gtAgentHistoryManager.update_agent_history_by_id(
            history_id=saved.id,
            role=OpenaiApiRole.TOOL,
            tool_call_id="call_1",
            message=llmApiUtil.OpenAIMessage.tool_result("call_1", '{"success": true}'),
            status=AgentHistoryStatus.FAILED,
            error_message="tool failed",
            tags=[AgentHistoryTag.ROOM_TURN_FINISH],
        )
        assert updated.id == saved.id
        assert updated.role == OpenaiApiRole.TOOL
        assert updated.tool_call_id == "call_1"
        assert updated.message == llmApiUtil.OpenAIMessage.tool_result("call_1", '{"success": true}')
        assert updated.status == AgentHistoryStatus.FAILED
        assert updated.error_message == "tool failed"
        assert updated.tags == [AgentHistoryTag.ROOM_TURN_FINISH]

    # ------------------------------------------------------------------
    # gtAgentTaskManager
    # ------------------------------------------------------------------
    async def test_agent_task_manager_get_first_unfinish_task_returns_earliest_pending(self):
        """没有 failed 任务时，返回最早的 pending 任务。"""
        await self._reset_tables()

        await self._save_role_template("alice", "gpt-4o")
        team = await gtTeamManager.save_team(GtTeam(name="task_team"))
        configs = [AgentConfig(name="alice", role_template="alice")]
        agents = await ServiceTestCase.convert_to_gt_agents(team.id, configs)
        await gtAgentManager.batch_save_agents(team.id, agents)
        alice = await gtAgentManager.get_agent(team.id, "alice")
        assert alice is not None

        # 创建多个 pending 任务
        task1 = await gtAgentTaskManager.create_task(alice.id, AgentTaskType.ROOM_MESSAGE, {"room_id": 1})
        task2 = await gtAgentTaskManager.create_task(alice.id, AgentTaskType.ROOM_MESSAGE, {"room_id": 2})

        first = await gtAgentTaskManager.get_first_unfinish_task(alice.id)
        assert first is not None
        assert first.id == task1.id

    async def test_agent_task_manager_get_first_unfinish_task_returns_failed(self):
        """有 failed 任务时，应返回最早的 failed 任务本身。"""
        await self._reset_tables()

        await self._save_role_template("alice", "gpt-4o")
        team = await gtTeamManager.save_team(GtTeam(name="task_team_2"))
        configs = [AgentConfig(name="alice", role_template="alice")]
        agents = await ServiceTestCase.convert_to_gt_agents(team.id, configs)
        await gtAgentManager.batch_save_agents(team.id, agents)
        alice = await gtAgentManager.get_agent(team.id, "alice")
        assert alice is not None

        # 创建一个 failed 任务
        task1 = await gtAgentTaskManager.create_task(alice.id, AgentTaskType.ROOM_MESSAGE, {"room_id": 1})
        await gtAgentTaskManager.update_task_status(task1.id, AgentTaskStatus.FAILED, "something went wrong")

        # 创建更多 pending 任务
        await gtAgentTaskManager.create_task(alice.id, AgentTaskType.ROOM_MESSAGE, {"room_id": 2})
        await gtAgentTaskManager.create_task(alice.id, AgentTaskType.ROOM_MESSAGE, {"room_id": 3})

        first = await gtAgentTaskManager.get_first_unfinish_task(alice.id)
        assert first is not None
        assert first.id == task1.id
        assert first.status == AgentTaskStatus.FAILED

    async def test_agent_task_manager_get_first_unfinish_task_returns_running(self):
        """恢复中的 running 任务，也应被视为最早未完成任务。"""
        await self._reset_tables()

        await self._save_role_template("alice", "gpt-4o")
        team = await gtTeamManager.save_team(GtTeam(name="task_team_running"))
        configs = [AgentConfig(name="alice", role_template="alice")]
        agents = await ServiceTestCase.convert_to_gt_agents(team.id, configs)
        await gtAgentManager.batch_save_agents(team.id, agents)
        alice = await gtAgentManager.get_agent(team.id, "alice")
        assert alice is not None

        task1 = await gtAgentTaskManager.create_task(alice.id, AgentTaskType.ROOM_MESSAGE, {"room_id": 1})
        await gtAgentTaskManager.update_task_status(task1.id, AgentTaskStatus.RUNNING)
        await gtAgentTaskManager.create_task(alice.id, AgentTaskType.ROOM_MESSAGE, {"room_id": 2})

        first = await gtAgentTaskManager.get_first_unfinish_task(alice.id)
        assert first is not None
        assert first.id == task1.id
        assert first.status == AgentTaskStatus.RUNNING

    async def test_agent_task_manager_get_first_unfinish_task_no_unfinish(self):
        """没有未完成任务时返回 None。"""
        await self._reset_tables()

        await self._save_role_template("alice", "gpt-4o")
        team = await gtTeamManager.save_team(GtTeam(name="task_team_3"))
        configs = [AgentConfig(name="alice", role_template="alice")]
        agents = await ServiceTestCase.convert_to_gt_agents(team.id, configs)
        await gtAgentManager.batch_save_agents(team.id, agents)
        alice = await gtAgentManager.get_agent(team.id, "alice")
        assert alice is not None

        # 没有 pending 任务
        first = await gtAgentTaskManager.get_first_unfinish_task(alice.id)
        assert first is None

    async def test_agent_task_manager_get_running_tasks_returns_running_only(self):
        """仅返回 RUNNING 任务，不包含 pending。"""
        await self._reset_tables()

        await self._save_role_template("alice", "gpt-4o")
        team = await gtTeamManager.save_team(GtTeam(name="task_team_4"))
        configs = [AgentConfig(name="alice", role_template="alice")]
        agents = await ServiceTestCase.convert_to_gt_agents(team.id, configs)
        await gtAgentManager.batch_save_agents(team.id, agents)
        alice = await gtAgentManager.get_agent(team.id, "alice")
        assert alice is not None

        pending_task = await gtAgentTaskManager.create_task(alice.id, AgentTaskType.ROOM_MESSAGE, {"room_id": 1})
        running_task = await gtAgentTaskManager.create_task(alice.id, AgentTaskType.ROOM_MESSAGE, {"room_id": 2})
        await gtAgentTaskManager.update_task_status(running_task.id, AgentTaskStatus.RUNNING)

        tasks = await gtAgentTaskManager.get_running_tasks(alice.id)
        assert [task.id for task in tasks] == [running_task.id]
        assert all(task.status == AgentTaskStatus.RUNNING for task in tasks)
        assert pending_task.id not in [task.id for task in tasks]

    async def test_agent_task_manager_transition_task_status_switches_failed_to_running(self):
        """transition_task_status 应原子地将 FAILED 任务切为 RUNNING。"""
        await self._reset_tables()

        await self._save_role_template("alice", "gpt-4o")
        team = await gtTeamManager.save_team(GtTeam(name="task_team_5"))
        configs = [AgentConfig(name="alice", role_template="alice")]
        agents = await ServiceTestCase.convert_to_gt_agents(team.id, configs)
        await gtAgentManager.batch_save_agents(team.id, agents)
        alice = await gtAgentManager.get_agent(team.id, "alice")
        assert alice is not None

        failed_task = await gtAgentTaskManager.create_task(alice.id, AgentTaskType.ROOM_MESSAGE, {"room_id": 1})
        await gtAgentTaskManager.update_task_status(failed_task.id, AgentTaskStatus.FAILED, "boom")

        resumed_task = await gtAgentTaskManager.transition_task_status(
            failed_task.id,
            AgentTaskStatus.FAILED,
            AgentTaskStatus.RUNNING,
        )

        assert resumed_task is not None
        assert resumed_task.id == failed_task.id
        assert resumed_task.status == AgentTaskStatus.RUNNING

    async def test_agent_task_manager_delete_tasks_by_team_uses_team_filter(self):
        """按 team_id 删除任务时，应直接基于 Agent 表条件删除对应团队任务。"""
        await self._reset_tables()

        await self._save_role_template("alice", "gpt-4o")
        team_a = await gtTeamManager.save_team(GtTeam(name="task_team_delete_a"))
        team_b = await gtTeamManager.save_team(GtTeam(name="task_team_delete_b"))

        agents_a = await ServiceTestCase.convert_to_gt_agents(team_a.id, [AgentConfig(name="alice", role_template="alice")])
        agents_b = await ServiceTestCase.convert_to_gt_agents(team_b.id, [AgentConfig(name="alice", role_template="alice")])
        await gtAgentManager.batch_save_agents(team_a.id, agents_a)
        await gtAgentManager.batch_save_agents(team_b.id, agents_b)

        alice_a = await gtAgentManager.get_agent(team_a.id, "alice")
        alice_b = await gtAgentManager.get_agent(team_b.id, "alice")
        assert alice_a is not None
        assert alice_b is not None

        task_a = await gtAgentTaskManager.create_task(alice_a.id, AgentTaskType.ROOM_MESSAGE, {"room_id": 1})
        task_b = await gtAgentTaskManager.create_task(alice_b.id, AgentTaskType.ROOM_MESSAGE, {"room_id": 2})

        deleted_count = await gtAgentTaskManager.delete_tasks_by_team(team_a.id)

        assert deleted_count == 1
        assert await GtAgentTask.aio_get_or_none(GtAgentTask.id == task_a.id) is None
        assert await GtAgentTask.aio_get_or_none(GtAgentTask.id == task_b.id) is not None

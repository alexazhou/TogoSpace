import os
import sys

import pytest

import service.ormService as ormService
from constants import RoomType
from dal.db import (
    gtAgentManager,
    gtAgentHistoryManager,
    gtRoomManager,
    gtRoomMemberManager,
    gtRoomMessageManager,
    gtTeamManager,
    gtTeamMemberManager,
)
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtAgentHistory import GtAgentHistory
from model.dbModel.gtRoom import GtRoom
from model.dbModel.gtRoomMember import GtRoomMember
from model.dbModel.gtRoomMessage import GtRoomMessage
from model.dbModel.gtTeam import GtTeam
from model.dbModel.gtTeamMember import GtTeamMember
from util.configTypes import TeamConfig, TeamMemberConfig, TeamRoomConfig
from tests.base import ServiceTestCase


if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


@pytest.mark.forked
class TestDalManagers(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        db_path = cls.TEST_DB_PATH
        await ormService.startup(db_path)

    @classmethod
    async def async_teardown_class(cls):
        await ormService.shutdown()

    async def _reset_tables(self):
        await GtAgent.delete().aio_execute()
        await GtTeamMember.delete().aio_execute()
        await GtRoomMember.delete().aio_execute()
        await GtRoomMessage.delete().aio_execute()
        await GtAgentHistory.delete().aio_execute()
        await GtRoom.delete().aio_execute()
        await GtTeam.delete().aio_execute()

    # ------------------------------------------------------------------
    # gtAgentManager
    # ------------------------------------------------------------------
    async def test_agent_manager_upsert_and_query_with_model(self):
        await self._reset_tables()

        team = await gtTeamManager.upsert_team(TeamConfig(name="agent_team"))

        saved_1 = await gtAgentManager.upsert_agent(team.id, "alice_1", "glm-4.7", "alice")
        assert saved_1.team_id == team.id
        assert saved_1.name == "alice_1"
        assert saved_1.model == "glm-4.7"
        assert saved_1.template_name == "alice"

        saved_2 = await gtAgentManager.upsert_agent(team.id, "alice_1", "gpt-4o", "assistant")
        assert saved_2.id == saved_1.id
        assert saved_2.model == "gpt-4o"
        assert saved_2.template_name == "assistant"

        row = await gtAgentManager.get_agent(team.id, "alice_1")
        assert row is not None
        assert row.model == "gpt-4o"
        assert row.template_name == "assistant"

        rows = await gtAgentManager.get_agents_by_team(team.id)
        assert [(r.name, r.model, r.template_name) for r in rows] == [("alice_1", "gpt-4o", "assistant")]

    async def test_agent_table_has_model_column(self):
        await self._reset_tables()

        cols = await GtAgent.raw("PRAGMA table_info('agents')").aio_execute()
        col_names = {c.name for c in cols}
        assert "model" in col_names
        assert "template_name" in col_names

    # ------------------------------------------------------------------
    # gtTeamManager
    # ------------------------------------------------------------------
    async def test_team_manager_get_upsert_delete_and_exists(self):
        await self._reset_tables()

        created = await gtTeamManager.upsert_team(TeamConfig(
            name="team_a",
            working_directory="/workspace/team_a",
            config={"slogan": "alpha"},
            max_function_calls=3,
        ))
        assert created.name == "team_a"
        assert created.working_directory == "/workspace/team_a"
        assert created.get_config() == {"slogan": "alpha"}
        assert created.max_function_calls == 3
        assert await gtTeamManager.team_exists("team_a") is True

        by_name = await gtTeamManager.get_team("team_a")
        by_id = await gtTeamManager.get_team_by_id(created.id)
        assert by_name is not None and by_name.id == created.id
        assert by_id is not None and by_id.name == "team_a"

        updated = await gtTeamManager.upsert_team(TeamConfig(
            name="team_a",
            working_directory="/workspace/team_a_v2",
            config={"slogan": "beta", "rules": "sync first"},
            max_function_calls=7,
        ))
        assert updated.id == created.id
        assert updated.working_directory == "/workspace/team_a_v2"
        assert updated.get_config() == {"rules": "sync first", "slogan": "beta"}
        assert updated.max_function_calls == 7

        await gtTeamManager.delete_team("team_a")
        assert await gtTeamManager.team_exists("team_a") is False
        deleted_row = await gtTeamManager.get_team("team_a")
        assert deleted_row is not None
        assert deleted_row.enabled == 0

    async def test_team_manager_get_all_teams_returns_only_enabled_sorted(self):
        await self._reset_tables()

        await gtTeamManager.upsert_team(TeamConfig(name="team_c"))
        await gtTeamManager.upsert_team(TeamConfig(name="team_a"))
        await gtTeamManager.upsert_team(TeamConfig(name="team_b"))
        await gtTeamManager.delete_team("team_b")

        teams = await gtTeamManager.get_all_teams()
        assert [t.name for t in teams] == ["team_a", "team_c"]

    async def test_team_manager_get_team_config_and_get_all_team_configs(self):
        await self._reset_tables()

        team_a = await gtTeamManager.upsert_team(TeamConfig(
            name="team_a",
            working_directory="/workspace/team_a",
            config={"slogan": "ship fast"},
        ))
        team_b = await gtTeamManager.upsert_team(TeamConfig(name="team_b"))
        await gtTeamMemberManager.upsert_team_members(team_a.id, [
            TeamMemberConfig(name="alice_1", agent="alice"),
            TeamMemberConfig(name="bob_1", agent="bob"),
        ])

        await gtRoomManager.upsert_rooms(team_a.id, [TeamRoomConfig(
            name="general",
            initial_topic="hello",
            max_turns=6,
            members=["alice_1", "bob_1"],
        )])
        room = await gtRoomManager.get_room_config(team_a.id, "general")
        assert room is not None
        await gtRoomMemberManager.upsert_room_members(room.id, ["bob_1", "alice_1"])

        cfg_a = await gtTeamManager.get_team_config("team_a")
        assert cfg_a is not None
        assert cfg_a.name == "team_a"
        assert cfg_a.working_directory == "/workspace/team_a"
        assert cfg_a.config == {"slogan": "ship fast"}
        assert [(m.name, m.agent) for m in cfg_a.members] == [
            ("alice_1", "alice"),
            ("bob_1", "bob"),
        ]
        assert len(cfg_a.preset_rooms) == 1
        assert cfg_a.preset_rooms[0].name == "general"
        assert cfg_a.preset_rooms[0].initial_topic == "hello"
        assert cfg_a.preset_rooms[0].max_turns == 6
        assert cfg_a.preset_rooms[0].members == ["alice_1", "bob_1"]

        cfg_none = await gtTeamManager.get_team_config("missing")
        assert cfg_none is None

        all_configs = await gtTeamManager.get_all_team_configs()
        assert [c.name for c in all_configs] == ["team_a", "team_b"]
        assert all_configs[1].preset_rooms == []

    async def test_team_manager_import_team_from_json_imports_and_skips_existing(self):
        await self._reset_tables()

        payload = TeamConfig(
            name="imported",
            members=[
                TeamMemberConfig(name="alice_1", agent="alice"),
                TeamMemberConfig(name="bob_1", agent="bob"),
            ],
            preset_rooms=[TeamRoomConfig(
                name="r1",
                initial_topic="topic 1",
                max_turns=8,
                members=["alice_1", "bob_1"],
            )],
        )
        await gtTeamManager.import_team_from_json(payload)

        imported = await gtTeamManager.get_team("imported")
        assert imported is not None
        room = await gtRoomManager.get_room_config(imported.id, "r1")
        assert room is not None
        assert room.max_turns == 8
        assert await gtRoomMemberManager.get_members_by_room(room.id) == ["alice_1", "bob_1"]

        # 已存在时应跳过导入，不覆盖已有记录
        await gtTeamManager.import_team_from_json(TeamConfig(
            name="imported",
            members=[TeamMemberConfig(name="charlie", agent="charlie")],
            preset_rooms=[TeamRoomConfig(name="r2", members=["Operator", "charlie"])],
        ))
        imported_after = await gtTeamManager.get_team("imported")
        assert imported_after is not None
        assert imported_after.max_function_calls == 5
        assert await gtRoomManager.get_room_config(imported_after.id, "r2") is None

    # ------------------------------------------------------------------
    # gtRoomManager
    # ------------------------------------------------------------------
    async def test_room_manager_get_rooms_and_get_room_config(self):
        await self._reset_tables()

        team = await gtTeamManager.upsert_team(TeamConfig(name="room_team"))
        await gtRoomManager.upsert_rooms(team.id, [
            TeamRoomConfig(name="z_room", max_turns=2, members=["alice", "bob"]),
            TeamRoomConfig(name="a_room", max_turns=3, members=["Operator", "alice"]),
        ])

        rooms = await gtRoomManager.get_rooms_by_team(team.id)
        assert [r.name for r in rooms] == ["a_room", "z_room"]

        a_room = await gtRoomManager.get_room_config(team.id, "a_room")
        assert a_room is not None
        assert a_room.type == RoomType.PRIVATE
        assert await gtRoomManager.get_room_config(team.id, "missing") is None

    async def test_room_manager_ensure_room_by_key_create_and_update(self):
        await self._reset_tables()

        team = await gtTeamManager.upsert_team(TeamConfig(name="ensure_team"))
        first = await gtRoomManager.ensure_room_by_key(
            team_id=team.id,
            room_name="stable",
            room_type=RoomType.GROUP,
            initial_topic="t1",
            max_turns=4,
        )
        second = await gtRoomManager.ensure_room_by_key(
            team_id=team.id,
            room_name="stable",
            room_type=RoomType.PRIVATE,
            initial_topic="t2",
            max_turns=9,
        )

        assert second.id == first.id
        assert second.type == RoomType.PRIVATE
        assert second.initial_topic == "t2"
        assert second.max_turns == 9

    async def test_room_manager_upsert_rooms_delete_replace_and_defaults(self):
        await self._reset_tables()

        team = await gtTeamManager.upsert_team(TeamConfig(name="upsert_team"))
        await gtRoomManager.upsert_rooms(team.id, [
            TeamRoomConfig(name="old_room", max_turns=2, members=["alice"]),
        ])
        await gtRoomManager.upsert_rooms(team.id, [
            TeamRoomConfig(name="new_room_1", members=["alice"]),
            TeamRoomConfig(name="new_room_2", initial_topic="x", members=["bob"]),
        ])

        rooms = await gtRoomManager.get_rooms_by_team(team.id)
        assert [r.name for r in rooms] == ["new_room_1", "new_room_2"]
        assert all(r.type == RoomType.GROUP for r in rooms)
        assert all(r.max_turns == 10 for r in rooms)

    async def test_room_manager_delete_room_and_delete_rooms_by_team(self):
        await self._reset_tables()

        team = await gtTeamManager.upsert_team(TeamConfig(name="delete_team"))
        await gtRoomManager.upsert_rooms(team.id, [
            TeamRoomConfig(name="r1", members=["alice"]),
            TeamRoomConfig(name="r2", members=["bob"]),
        ])
        r1 = await gtRoomManager.get_room_config(team.id, "r1")
        assert r1 is not None

        await gtRoomManager.delete_room(r1.id)
        names_after_one_delete = [r.name for r in await gtRoomManager.get_rooms_by_team(team.id)]
        assert names_after_one_delete == ["r2"]

        await gtRoomManager.delete_rooms_by_team(team.id)
        assert await gtRoomManager.get_rooms_by_team(team.id) == []

    async def test_room_manager_save_and_get_room_state(self):
        await self._reset_tables()

        team = await gtTeamManager.upsert_team(TeamConfig(name="state_team"))
        room = await gtRoomManager.ensure_room_by_key(
            team_id=team.id,
            room_name="state_room",
            room_type=RoomType.GROUP,
            initial_topic="",
            max_turns=5,
        )

        assert await gtRoomManager.get_room_state(room.id) is None

        state = {"alice": 1, "bob": 3}
        await gtRoomManager.save_room_state(room.id, state)
        assert await gtRoomManager.get_room_state(room.id) == state
        assert await gtRoomManager.get_room_state(999999) is None

    # ------------------------------------------------------------------
    # gtRoomMemberManager
    # ------------------------------------------------------------------
    async def test_room_member_manager_get_upsert_delete(self):
        await self._reset_tables()

        team = await gtTeamManager.upsert_team(TeamConfig(name="member_team"))
        room = await gtRoomManager.ensure_room_by_key(
            team_id=team.id,
            room_name="member_room",
            room_type=RoomType.GROUP,
            initial_topic="",
            max_turns=5,
        )

        assert await gtRoomMemberManager.get_members_by_room(room.id) == []

        await gtRoomMemberManager.upsert_room_members(room.id, ["charlie", "alice"])
        assert await gtRoomMemberManager.get_members_by_room(room.id) == ["alice", "charlie"]

        # upsert 会覆盖旧成员
        await gtRoomMemberManager.upsert_room_members(room.id, ["bob"])
        assert await gtRoomMemberManager.get_members_by_room(room.id) == ["bob"]

        await gtRoomMemberManager.delete_members_by_room(room.id)
        assert await gtRoomMemberManager.get_members_by_room(room.id) == []

    # ------------------------------------------------------------------
    # gtRoomMessageManager
    # ------------------------------------------------------------------
    async def test_room_message_manager_append_and_query(self):
        await self._reset_tables()

        team = await gtTeamManager.upsert_team(TeamConfig(name="msg_team"))
        room = await gtRoomManager.ensure_room_by_key(
            team_id=team.id,
            room_name="msg_room",
            room_type=RoomType.GROUP,
            initial_topic="",
            max_turns=5,
        )

        m1 = await gtRoomMessageManager.append_room_message(room.id, "alice", "hello", "2026-03-23T10:00:00")
        m2 = await gtRoomMessageManager.append_room_message(room.id, "bob", "world", "2026-03-23T10:01:00")
        m3 = await gtRoomMessageManager.append_room_message(room.id, "alice", "again", "2026-03-23T10:02:00")

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

        team = await gtTeamManager.upsert_team(TeamConfig(name="history_team"))
        first = GtAgentHistory(
            team_id=team.id,
            agent_name="alice",
            seq=1,
            message_json='{"content":"v1"}',
        )
        saved_1 = await gtAgentHistoryManager.append_agent_history_message(first)
        assert saved_1.agent_name == "alice"
        assert saved_1.seq == 1
        assert saved_1.message_json == '{"content":"v1"}'

        duplicate = GtAgentHistory(
            team_id=team.id,
            agent_name="alice",
            seq=1,
            message_json='{"content":"v2"}',
        )
        saved_2 = await gtAgentHistoryManager.append_agent_history_message(duplicate)
        assert saved_2.id == saved_1.id
        assert saved_2.message_json == '{"content":"v1"}'

    async def test_agent_history_manager_append_and_get_sorted(self):
        await self._reset_tables()

        team = await gtTeamManager.upsert_team(TeamConfig(name="history_team_2"))

        items = [
            GtAgentHistory(team_id=team.id, agent_name="alice", seq=2, message_json='{"content":"2"}'),
            GtAgentHistory(team_id=team.id, agent_name="alice", seq=1, message_json='{"content":"1"}'),
            GtAgentHistory(team_id=team.id, agent_name="bob", seq=1, message_json='{"content":"b1"}'),
        ]
        for item in items:
            await gtAgentHistoryManager.append_agent_history_message(item)

        alice_history = await gtAgentHistoryManager.get_agent_history(team.id, "alice")
        assert [h.seq for h in alice_history] == [1, 2]
        assert [h.message_json for h in alice_history] == ['{"content":"1"}', '{"content":"2"}']

        bob_history = await gtAgentHistoryManager.get_agent_history(team.id, "bob")
        assert [h.seq for h in bob_history] == [1]

"""#85：房间成员不变量 —— 四处服务层入口 + 工具层入口。

缺陷面（网关开发 #81 立档、小马哥复核后扩到 5 处）：
`agent_ids=[7, 7]` 这种「同一人写两遍」的入参能一路建出房间——
`len(agent_ids) == 2` 于是类型判成 PRIVATE、话题写成「alice 和 alice」，
随后调度器把「同一人连续发言」判为跳过，房间说完一句就永久 IDLE。

本文件守住的入口：
  1. `upsert_room`：此前**零校验**（无 <2、无去重），与同模块 `update_room_agents`
     的 `<2` 拒绝不一致；现在补齐 `ROOM_AGENTS_TOO_FEW` + 去重 + 无 SYSTEM。
  2. `create_room`：`<2` 判在**成员数**上，`[7,7]` 的 len 是 2 直接放行。
  3. `update_room_agents`：原本只有 `<2`，重复成员照样落地。
  4. `batch_create_rooms` / `overwrite_team_rooms`：preset 导入与团队保存两条路，
     此前对成员零校验逐个透传给 DAL。
  5. 工具层 `save_room` 见 `tests/integration/test_funcToolService/test_save_room_members.py`。

不变量口径（与 #85 裁定一致）：
  · **拒绝重复成员**、**拒绝系统 Agent** —— 这两条在所有入口都成立。
  · **`<2` 只在 `create_room` / `upsert_room` / `update_room_agents` 拒绝**：
    preset 两条路与 DAL `save_room` 一直允许单成员房（多个既有夹具刻意建单成员房），
    「房间至少 2 人」在本仓不是全局不变量，故 `_assert_room_agents_valid` 不含 `<2`。
"""

import os
import sys

import pytest

from constants import EmployStatus, RoomType, SpecialAgent
from dal.db import gtAgentManager, gtRoleTemplateManager, gtRoomManager, gtTeamManager
from exception import TogoException
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtRoleTemplate import GtRoleTemplate
from model.dbModel.gtRoom import GtRoom
from model.dbModel.gtRoomMessage import GtRoomMessage
from model.dbModel.gtTeam import GtTeam
import service.ormService as ormService
import service.roomService as roomService
from tests.base import ServiceTestCase

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

SYSTEM_ID = int(SpecialAgent.SYSTEM.value)


class TestRoomAgentsInvariants(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        await ormService.startup(cls._get_test_db_path())

    @classmethod
    async def async_teardown_class(cls):
        await ormService.shutdown()

    async def _reset_tables(self):
        await GtRoom.delete().aio_execute()
        await GtRoomMessage.delete().aio_execute()
        await GtAgent.delete().aio_execute()
        await GtTeam.delete().aio_execute()
        await GtRoleTemplate.delete().aio_execute()

    async def _setup_team(self, team_name: str, agent_names: list[str]) -> GtTeam:
        team = await gtTeamManager.save_team(GtTeam(name=team_name))
        template = await gtRoleTemplateManager.save_role_template(
            GtRoleTemplate(name=f"tpl_{team_name}", model="gpt-4o")
        )
        await gtAgentManager.batch_save_agents(
            team.id,
            [
                GtAgent(
                    team_id=team.id,
                    name=n,
                    role_template_id=template.id,
                    model="",
                    employ_status=EmployStatus.ON_BOARD,
                )
                for n in agent_names
            ],
        )
        return team

    async def _ids(self, team_id: int, *names: str) -> list[int]:
        result = []
        for name in names:
            agent = await gtAgentManager.get_agent(team_id, name, status=None)
            assert agent is not None, f"agent '{name}' 不存在"
            result.append(agent.id)
        return result

    # ------------------------------------------------------------------ #
    # upsert_room：<2 必须拒绝（此前零校验）
    # ------------------------------------------------------------------ #

    async def test_upsert_room_rejects_single_member(self):
        """`upsert_room(agent_ids=[alice])` 应报 ROOM_AGENTS_TOO_FEW，且不落库。"""
        await self._reset_tables()
        team = await self._setup_team("t_inv_single", ["alice", "bob"])
        alice_id = (await self._ids(team.id, "alice"))[0]

        with pytest.raises(TogoException) as exc_info:
            await roomService.upsert_room(team_id=team.id, name="solo", agent_ids=[alice_id])

        assert exc_info.value.error_code == "ROOM_AGENTS_TOO_FEW"
        assert await gtRoomManager.get_rooms_by_team(team.id) == []

    async def test_upsert_room_rejects_duplicate_members(self):
        """`upsert_room(agent_ids=[alice, alice])` 应报 duplicate_agent_ids（而不是建出 1 人 PRIVATE）。"""
        await self._reset_tables()
        team = await self._setup_team("t_inv_dup", ["alice", "bob"])
        alice_id = (await self._ids(team.id, "alice"))[0]

        with pytest.raises(TogoException) as exc_info:
            await roomService.upsert_room(team_id=team.id, name="dup", agent_ids=[alice_id, alice_id])

        assert exc_info.value.error_code == "duplicate_agent_ids"
        assert await gtRoomManager.get_rooms_by_team(team.id) == []

    async def test_upsert_room_rejects_duplicate_after_valid_members(self):
        """`[alice, bob, bob]` 同样拒绝 —— 不许静默去重成 2 人 PRIVATE。"""
        await self._reset_tables()
        team = await self._setup_team("t_inv_dup_tail", ["alice", "bob", "charlie"])
        alice_id, bob_id = await self._ids(team.id, "alice", "bob")

        with pytest.raises(TogoException) as exc_info:
            await roomService.upsert_room(
                team_id=team.id, name="dup_tail", agent_ids=[alice_id, bob_id, bob_id],
            )

        assert exc_info.value.error_code == "duplicate_agent_ids"
        assert await gtRoomManager.get_rooms_by_team(team.id) == []

    async def test_upsert_room_rejects_system_agent(self):
        """成员含 SYSTEM 应报 system_agent_not_allowed。"""
        await self._reset_tables()
        team = await self._setup_team("t_inv_sys", ["alice", "bob"])
        alice_id = (await self._ids(team.id, "alice"))[0]

        with pytest.raises(TogoException) as exc_info:
            await roomService.upsert_room(
                team_id=team.id, name="sys_room", agent_ids=[SYSTEM_ID, alice_id],
            )

        assert exc_info.value.error_code == "system_agent_not_allowed"

    async def test_upsert_room_two_distinct_members_still_creates_private(self):
        """回归护栏：合法 2 人（互不相同）仍应建出 PRIVATE 且话题是两个人的名字。"""
        await self._reset_tables()
        team = await self._setup_team("t_inv_ok", ["alice", "bob"])
        alice_id, bob_id = await self._ids(team.id, "alice", "bob")

        saved = await roomService.upsert_room(
            team_id=team.id, name="ok_room", agent_ids=[alice_id, bob_id],
        )

        assert saved.type == RoomType.PRIVATE
        assert saved.agent_ids == [alice_id, bob_id]
        assert "alice" in saved.initial_topic and "bob" in saved.initial_topic
        assert "alice和alice" not in saved.initial_topic

    # ------------------------------------------------------------------ #
    # create_room：`<2` 判在成员数上，[7,7] 的 len==2 能过
    # ------------------------------------------------------------------ #

    async def test_create_room_rejects_duplicate_members(self):
        """`create_room(agent_ids=[alice, alice])` 应报 duplicate_agent_ids。"""
        await self._reset_tables()
        team = await self._setup_team("t_inv_create_dup", ["alice", "bob"])
        alice_id = (await self._ids(team.id, "alice"))[0]

        with pytest.raises(TogoException) as exc_info:
            await roomService.create_room(
                team_id=team.id, name="create_dup", agent_ids=[alice_id, alice_id],
            )

        assert exc_info.value.error_code == "duplicate_agent_ids"
        assert await gtRoomManager.get_rooms_by_team(team.id) == []

    # ------------------------------------------------------------------ #
    # update_room_agents：原本只有 <2，重复成员照样落地
    # ------------------------------------------------------------------ #

    async def test_update_room_agents_rejects_duplicate_members(self):
        """`update_room_agents(room_id, [alice, bob, bob])` 应报 duplicate_agent_ids。"""
        await self._reset_tables()
        team = await self._setup_team("t_inv_update_dup", ["alice", "bob", "charlie"])
        alice_id, bob_id = await self._ids(team.id, "alice", "bob")
        room = await gtRoomManager.save_room(GtRoom(
            team_id=team.id, name="upd_room", type=RoomType.GROUP,
            initial_topic="", max_rounds=10, agent_ids=[alice_id, bob_id], tags=[],
        ))

        with pytest.raises(TogoException) as exc_info:
            await roomService.update_room_agents(room.id, [alice_id, bob_id, bob_id])

        assert exc_info.value.error_code == "duplicate_agent_ids"
        reloaded = await gtRoomManager.get_room_by_id(room.id)
        assert reloaded is not None and reloaded.agent_ids == [alice_id, bob_id]

    async def test_update_room_agents_rejects_system_agent(self):
        """`update_room_agents` 不得把 SYSTEM 塞进成员。"""
        await self._reset_tables()
        team = await self._setup_team("t_inv_update_sys", ["alice", "bob"])
        alice_id, bob_id = await self._ids(team.id, "alice", "bob")
        room = await gtRoomManager.save_room(GtRoom(
            team_id=team.id, name="upd_sys_room", type=RoomType.GROUP,
            initial_topic="", max_rounds=10, agent_ids=[alice_id, bob_id], tags=[],
        ))

        with pytest.raises(TogoException) as exc_info:
            await roomService.update_room_agents(room.id, [SYSTEM_ID, alice_id])

        assert exc_info.value.error_code == "system_agent_not_allowed"

    # ------------------------------------------------------------------ #
    # batch_create_rooms（preset 导入）：此前零校验
    # ------------------------------------------------------------------ #

    async def test_batch_create_rooms_rejects_duplicate_members(self):
        """preset 导入含重复成员应被拒绝，且一个房间都不落库。"""
        await self._reset_tables()
        team = await self._setup_team("t_inv_batch_dup", ["alice", "bob"])
        alice_id, bob_id = await self._ids(team.id, "alice", "bob")

        with pytest.raises(TogoException) as exc_info:
            await roomService.batch_create_rooms(team.id, [
                GtRoom(
                    team_id=team.id, name="bad_dup", type=RoomType.PRIVATE,
                    initial_topic="", max_rounds=None,
                    agent_ids=[alice_id, alice_id], biz_id=None, tags=[],
                ),
            ])

        assert exc_info.value.error_code == "duplicate_agent_ids"
        assert await gtRoomManager.get_rooms_by_team(team.id) == []

    async def test_batch_create_rooms_rejects_system_agent(self):
        """preset 导入含 SYSTEM 成员应被拒绝。"""
        await self._reset_tables()
        team = await self._setup_team("t_inv_batch_sys", ["alice", "bob"])
        alice_id = (await self._ids(team.id, "alice"))[0]

        with pytest.raises(TogoException) as exc_info:
            await roomService.batch_create_rooms(team.id, [
                GtRoom(
                    team_id=team.id, name="bad_sys", type=RoomType.GROUP,
                    initial_topic="", max_rounds=None,
                    agent_ids=[SYSTEM_ID, alice_id], biz_id=None, tags=[],
                ),
            ])

        assert exc_info.value.error_code == "system_agent_not_allowed"

    async def test_batch_create_rooms_still_allows_single_member(self):
        """preset 路径**不**拒绝单成员房（本仓既有夹具与生产预设都依赖它）。"""
        await self._reset_tables()
        team = await self._setup_team("t_inv_batch_single", ["alice", "bob"])
        alice_id = (await self._ids(team.id, "alice"))[0]

        await roomService.batch_create_rooms(team.id, [
            GtRoom(
                team_id=team.id, name="single_ok", type=RoomType.GROUP,
                initial_topic="", max_rounds=None,
                agent_ids=[alice_id], biz_id=None, tags=[],
            ),
        ])

        rooms = await gtRoomManager.get_rooms_by_team(team.id)
        assert [r.name for r in rooms] == ["single_ok"]
        assert rooms[0].agent_ids == [alice_id]

    # ------------------------------------------------------------------ #
    # overwrite_team_rooms（团队保存）：校验必须在删房间之前
    # ------------------------------------------------------------------ #

    async def test_overwrite_team_rooms_rejects_duplicate_members(self):
        """含重复成员的房间集应被拒绝。"""
        await self._reset_tables()
        team = await self._setup_team("t_inv_overwrite_dup", ["alice", "bob"])
        alice_id, bob_id = await self._ids(team.id, "alice", "bob")

        with pytest.raises(TogoException) as exc_info:
            await roomService.overwrite_team_rooms(team.id, [
                GtRoom(
                    team_id=team.id, name="bad_overwrite", type=RoomType.PRIVATE,
                    initial_topic="", max_rounds=None,
                    agent_ids=[bob_id, bob_id], biz_id=None, tags=[],
                ),
            ])

        assert exc_info.value.error_code == "duplicate_agent_ids"

    async def test_overwrite_team_rooms_failure_has_no_side_effect(self):
        """校验失败时**零副作用**：既有房间不得被当成「已移除」清掉。

        `overwrite_team_rooms` 开头会删掉不在目标集中的房间，所以成员校验必须提到
        删除之前；否则一个非法房间就能让团队房间集停在「删了一半」的中间态。
        """
        await self._reset_tables()
        team = await self._setup_team("t_inv_overwrite_side", ["alice", "bob"])
        alice_id = (await self._ids(team.id, "alice"))[0]
        await gtRoomManager.save_room(GtRoom(
            team_id=team.id, name="keep_me", type=RoomType.GROUP,
            initial_topic="", max_rounds=None, agent_ids=[alice_id], biz_id=None, tags=[],
        ))

        with pytest.raises(TogoException) as exc_info:
            await roomService.overwrite_team_rooms(team.id, [
                GtRoom(
                    team_id=team.id, name="bad_overwrite_side", type=RoomType.PRIVATE,
                    initial_topic="", max_rounds=None,
                    agent_ids=[alice_id, alice_id], biz_id=None, tags=[],
                ),
            ])

        assert exc_info.value.error_code == "duplicate_agent_ids"
        rooms = await gtRoomManager.get_rooms_by_team(team.id)
        assert [r.name for r in rooms] == ["keep_me"]

    async def test_overwrite_team_rooms_still_allows_single_member(self):
        """preset 路径**不**拒绝单成员房（生产预设全是 2 人，既有夹具刻意用 1 人）。"""
        await self._reset_tables()
        team = await self._setup_team("t_inv_overwrite_single", ["alice", "bob"])
        alice_id, bob_id = await self._ids(team.id, "alice", "bob")

        await roomService.overwrite_team_rooms(team.id, [
            GtRoom(
                team_id=team.id, name="single_a", type=RoomType.GROUP,
                initial_topic="", max_rounds=None,
                agent_ids=[alice_id], biz_id=None, tags=[],
            ),
            GtRoom(
                team_id=team.id, name="pair_b", type=RoomType.PRIVATE,
                initial_topic="", max_rounds=None,
                agent_ids=[alice_id, bob_id], biz_id=None, tags=[],
            ),
        ])

        names = sorted(r.name for r in await gtRoomManager.get_rooms_by_team(team.id))
        assert names == ["pair_b", "single_a"]

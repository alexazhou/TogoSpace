"""集成测试：清空团队数据后重新调度的行为验证。

验证关键修复：clear_team_data 删除消息后必须同时重置 agent_read_index，
否则 agent 的读取指针仍指向旧位置，导致新发出的初始系统消息（seq=0）被跳过。
"""
import os
import sys

import pytest

import service.ormService as ormService
import service.persistenceService as persistenceService
import service.roomService as roomService
import service.agentService as agentService
from constants import RoomState, RoomType, EmployStatus
from dal.db import gtTeamManager, gtAgentManager, gtRoomManager, gtRoomMessageManager
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtRoom import GtRoom
from model.dbModel.gtTeam import GtTeam
from ...base import ServiceTestCase

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

TEAM = "test_clear_data_team"


class TestClearTeamDataSchedulingRestart(ServiceTestCase):
    """验证 clear_team_data 重置 agent_read_index 后，房间激活时 agent 能读到初始消息。"""

    @classmethod
    async def async_setup_class(cls):
        db_path = cls._get_test_db_path()
        await ormService.startup(db_path)
        await persistenceService.startup()
        await agentService.startup()  # 确保 SpecialAgent 记录存在（SYSTEM_MEMBER_ID）
        await roomService.startup()

        team = await gtTeamManager.save_team(GtTeam(name=TEAM))
        await gtAgentManager.batch_save_agents(
            team.id,
            [
                GtAgent(team_id=team.id, name="alice", role_template_id=0, employ_status=EmployStatus.ON_BOARD),
                GtAgent(team_id=team.id, name="bob", role_template_id=0, employ_status=EmployStatus.ON_BOARD),
            ],
        )
        cls.team_id = team.id

    @classmethod
    async def async_teardown_class(cls):
        roomService.shutdown()
        await agentService.shutdown()
        await persistenceService.shutdown()
        await ormService.shutdown()

    async def _get_agent_id(self, name: str) -> int:
        agent = await gtAgentManager.get_agent(self.team_id, name)
        assert agent is not None
        return agent.id

    async def _create_dept_room(self, room_name: str) -> GtRoom:
        alice_id = await self._get_agent_id("alice")
        bob_id = await self._get_agent_id("bob")
        gt_room = GtRoom(
            team_id=self.team_id,
            name=room_name,
            type=RoomType.GROUP,
            initial_topic="集成测试话题",
            max_rounds=-1,
            agent_ids=[alice_id, bob_id],
            biz_id=None,
            tags=["DEPT"],
        )
        return await gtRoomManager.save_room(gt_room)

    async def test_agents_see_initial_message_after_clear_and_reload(self):
        """清空消息并重置 read_index 后，reload 房间再激活，agent 应能看到初始系统消息。"""
        room_name = "dept_room_clear"
        gt_room = await self._create_dept_room(room_name)
        room_id = gt_room.id
        alice_id = await self._get_agent_id("alice")
        bob_id = await self._get_agent_id("bob")

        # 直接写入过时的 read_index，模拟历史对话后持久化的状态（read_index > 0）
        await gtRoomManager.update_room_state(
            room_id=room_id,
            agent_read_index={str(alice_id): 3, str(bob_id): 3},
            speaker_index=1,
        )

        # --- 模拟 clear_team_data：删除消息 + 重置运行时状态 ---
        await gtRoomMessageManager.delete_messages_by_team(self.team_id)
        await gtRoomManager.reset_room_runtime_state(self.team_id)

        # 验证 DB 中 read_index 已重置为 None
        reset_index, reset_speaker = await gtRoomManager.get_room_state(room_id)
        assert reset_index is None or reset_index == {}, f"重置后 read_index 应为 None，实际: {reset_index}"
        assert reset_speaker is None, f"重置后 speaker_index 应为 None，实际: {reset_speaker}"

        # --- 模拟 hot_reload：reload 房间并激活 ---
        await roomService.close_team_rooms(self.team_id)
        await roomService.load_team_rooms(self.team_id)
        await roomService.restore_team_rooms_runtime_state(self.team_id)
        room = roomService.get_room_by_key(f"{room_name}@{TEAM}")
        assert room is not None

        await room.activate_scheduling()

        # 激活后，房间应处于 SCHEDULING 状态
        assert room.state == RoomState.SCHEDULING, f"激活后应为 SCHEDULING，实际: {room.state}"

        # alice 和 bob 的 read_index 均从 0 开始，应能看到初始系统消息
        assert room.has_unread_messages(alice_id), "alice 应能看到初始系统消息"
        assert room.has_unread_messages(bob_id), "bob 应能看到初始系统消息"

    async def test_stale_read_index_causes_agents_to_miss_initial_message(self):
        """回归测试：若不重置 read_index，agent 读取位置错误，初始消息会被跳过。

        此测试证明修复前的问题确实存在（用于对比验证）。
        """
        room_name = "dept_room_stale"
        gt_room = await self._create_dept_room(room_name)
        room_id = gt_room.id
        alice_id = await self._get_agent_id("alice")

        # 直接在 DB 写入一个过时的 read_index（模拟 bug 复现前的状态）
        await gtRoomManager.update_room_state(
            room_id=room_id,
            agent_read_index={str(alice_id): 5},  # alice 的读取位置超前于消息数量
            speaker_index=None,
        )

        # 删除所有消息但不重置 read_index
        await roomService.close_team_rooms(self.team_id)
        await gtRoomMessageManager.delete_messages_by_team(self.team_id)
        # 故意不调用 reset_room_runtime_state，模拟修复前的行为

        # reload 房间并恢复运行时状态（读取位置从 DB 恢复：stale alice_id -> 5）
        await roomService.load_team_rooms(self.team_id)
        await roomService.restore_team_rooms_runtime_state(self.team_id)
        room = roomService.get_room_by_key(f"{room_name}@{TEAM}")
        assert room is not None

        await room.activate_scheduling()

        # 由于 alice 的 read_index=5，而初始消息 seq=0，has_unread 返回 False（bug）
        assert not room.has_unread_messages(alice_id), (
            "验证 bug 行为：stale read_index 应导致 alice 看不到初始消息"
        )

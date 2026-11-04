"""集成测试：Agent 间跨房间安全边界注入（立即注入）。

对应设计文档 `docs/design/2026-11-05-agent-immediate-message/step2_technical.md` §6 第二组：
Agent B 在跑群聊 turn 期间，Agent A 在 `alice_bob` 单聊房间发 `insert_immediately=True`
消息，B 在安全边界按序收取该房间的全部未读消息并推进游标；后续该房间的
ROOM_MESSAGE 调度任务因 `synced_count == 0` 自动跳过收尾。

与 `test_ctrl_room_inject.py` 的分工：那边测「Operator 控制房」，这里测「AI 与 AI 的单聊房间」。
"""
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from constants import AgentHistoryTag, OpenaiApiRole, RoomState, RoomType, TurnStepResult
from dal.db import gtAgentManager, gtRoomManager, gtTeamManager
from model.dbModel.agentMessage import AgentMessage
from model.dbModel.gtAgentHistory import GtAgentHistory
from model.dbModel.gtRoom import GtRoom
from service import agentService, ormService, persistenceService, presetService, roomService
from service.roomService import ChatRoom
from util import configUtil, llmApiUtil
from tests.base import ServiceTestCase

TEAM = "test_team"
_CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config")

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


class TestPrivateRoomInjectionDuringGroupTurn(ServiceTestCase):
    """alice 在群聊 turn 期间，bob 发来的单聊加急消息应在安全边界被收取。"""

    @classmethod
    async def async_setup_class(cls):
        db_path = cls._get_test_db_path()
        await ormService.startup(db_path)
        await persistenceService.startup()
        await roomService.startup()
        await presetService._import_role_templates_from_app_config()
        cfg = configUtil.load(_CONFIG_DIR, preset_dir=_CONFIG_DIR, force_reload=True)
        team_cfg = cfg.teams_preset[0]
        await presetService._import_team_from_config(team_cfg)
        await agentService.startup()
        await agentService.load_all_team_agents()
        await roomService.load_all_rooms()

        team = await gtTeamManager.get_team(TEAM)
        cls.team_id = team.id

    @classmethod
    async def async_teardown_class(cls):
        await agentService.shutdown()
        roomService.shutdown()
        await persistenceService.shutdown()
        await ormService.shutdown()

    async def _make_alice_bob_private_room(self, room_name: str = "alice_bob"):
        alice = await gtAgentManager.get_agent(self.team_id, "alice")
        bob = await gtAgentManager.get_agent(self.team_id, "bob")
        existing = await gtRoomManager.get_room_by_team_and_name(self.team_id, room_name)
        if existing is not None:
            # 同一测试类共用一个 DB，房间名唯一约束要求先清掉上一用例留下的房间
            await gtRoomManager.delete_room(existing.id)
        gt_room = await gtRoomManager.save_room(GtRoom(
            team_id=self.team_id,
            name="alice_bob",
            type=RoomType.PRIVATE,
            initial_topic="",
            max_rounds=-1,
            agent_ids=[alice.id, bob.id],
        ))
        await roomService.load_all_rooms()
        room = roomService.get_room(gt_room.id)
        assert room is not None
        with patch("service.messageBus.publish"):
            await room.activate_scheduling()
            # 推到 IDLE，模拟「两人当前都没话说」
            await room.handle_finish_request(alice.id)
            await room.handle_finish_request(bob.id)
        assert room.state == RoomState.IDLE
        await room.get_unread_messages(alice.id)  # 消费历史未读，避免干扰断言
        return room, alice, bob

    async def _run_loop_once(self, agent_id: int):
        agent = agentService.get_agent(agent_id)
        turn_runner = agent.job_consumer._turn_runner

        # history 末尾为 USER 消息 ⇒ is_safe_for_immediate_insert() 为 True
        await turn_runner._history.append_history_message(GtAgentHistory.build(
            AgentMessage.from_openai(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "群聊消息")),
            tags=[AgentHistoryTag.ROOM_TURN_BEGIN],
        ))
        turn_runner.driver = MagicMock()
        turn_runner.driver.turn_setup = SimpleNamespace(max_retries=1, hint_prompt="")
        turn_runner._advance_step = AsyncMock(return_value=TurnStepResult.TURN_DONE)

        group_room = MagicMock(spec=ChatRoom)
        with patch("service.agentService.agentTurnRunner.agentActivityService.add_activity", new=AsyncMock()):
            await turn_runner._run_turn_loop(group_room)

    async def test_private_immediate_collected_at_safe_boundary_in_order(self):
        """单聊里 A 发的 immediate 触发后，该房间全部未读（含前后普通消息）按序被收走。"""
        room, alice, bob = await self._make_alice_bob_private_room()

        with patch("service.messageBus.publish"):
            await room.add_message(bob.id, "M1-普通")
            await room.add_message(bob.id, "M2-立即注入", insert_immediately=True)
            await room.add_message(bob.id, "M3-普通")

        assert room.has_unread_immediate_messages(alice.id) is True

        alice_agent = agentService.get_agent(alice.id)
        history = alice_agent.job_consumer._turn_runner._history
        before = len(history)

        await self._run_loop_once(alice.id)

        assert room.has_unread_messages(alice.id) is False, (
            "单聊房间的 immediate 触发后应连带收取全部未读，不能漏到下一轮"
        )
        injected = "\n".join(item.content or "" for item in history[before:])
        positions = [injected.find(tag) for tag in ("M1-普通", "M2-立即注入", "M3-普通")]
        assert all(p >= 0 for p in positions), f"三条消息都应被收取，实际内容：{injected}"
        assert positions == sorted(positions), f"收取顺序必须等于发送顺序，实际偏移 {positions}"

    async def test_regular_private_message_not_read_during_turn(self):
        """普通单聊消息不提前读取：仍留到下一轮，且不进安全边界注入。"""
        room, alice, bob = await self._make_alice_bob_private_room()

        with patch("service.messageBus.publish"):
            await room.add_message(bob.id, "普通单聊消息")
        assert room.has_unread_messages(alice.id) is True
        assert room.has_unread_immediate_messages(alice.id) is False

        await self._run_loop_once(alice.id)

        assert room.has_unread_messages(alice.id) is True, (
            "普通单聊消息不应在 turn 的安全边界被提前读取"
        )

    async def test_private_room_message_skipped_when_nothing_to_sync(self):
        """安全边界收完之后，该房间再来的 ROOM_MESSAGE 调度任务因 synced_count==0 跳过收尾。"""
        room, alice, bob = await self._make_alice_bob_private_room()

        with patch("service.messageBus.publish"):
            await room.add_message(bob.id, "紧急纠偏", insert_immediately=True)

        await self._run_loop_once(alice.id)
        assert room.has_unread_messages(alice.id) is False

        alice_agent = agentService.get_agent(alice.id)
        turn_runner = alice_agent.job_consumer._turn_runner
        synced = await turn_runner.collect_room_messages(room, as_update=False)

        assert synced == 0, "已全部收取后再收一次应返回 0（调度任务据此跳过收尾）"

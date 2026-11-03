"""集成测试：Agent 控制房间的消息注入行为。

覆盖产品文档 §2.1 / §2.4 的两条规则：

1. 普通消息不提前读取：Agent 在群聊 turn 期间，控制房间的普通消息保留未读，
   留到本轮结束后的下一轮再读；
2. 空闲时立即注入必须唤醒：Agent 空闲（IDLE）时发 insert_immediately 消息，
   房间被唤醒并触发调度，不会停在「即将注入」无人处理的死状态。
"""
import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from constants import AgentHistoryTag, AgentStatus, MessageBusTopic, OpenaiApiRole, RoomState, TurnStepResult
from dal.db import gtAgentManager, gtTeamManager
from model.dbModel.agentMessage import AgentMessage
from model.dbModel.gtAgentHistory import GtAgentHistory
from service import agentService, ormService, persistenceService, presetService, roomService
from service.roomService import ChatRoom
from util import configUtil, llmApiUtil
from ...base import ServiceTestCase

TEAM = "test_team"
_CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config")

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


class TestCtrlRoomMessageInjectionDuringGroupTurn(ServiceTestCase):
    """agent 处理群聊 turn 期间，控制房间消息按类型走不同路径。"""

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

    async def _ctrl_room_idle_for(self, alice_id: int):
        """取控制房间并推入 IDLE（alice 无内容结束 → 全部 AI skipped）。"""
        ctrl_room, _ = await roomService.get_or_create_control_room(self.team_id, alice_id)
        with patch("service.messageBus.publish"):
            await ctrl_room.handle_finish_request(alice_id)
        assert ctrl_room.state == RoomState.IDLE
        await ctrl_room.get_unread_messages(alice_id)  # 消费历史未读，避免干扰断言
        return ctrl_room

    async def _run_loop_once(self, alice_id: int):
        alice_agent = agentService.get_agent(alice_id)
        turn_runner = alice_agent.job_consumer._turn_runner

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

    async def test_regular_message_not_read_during_group_turn(self):
        """普通消息不提前读取：本轮 loop 期间保持未读，等本轮结束后的下一轮再读。"""
        alice = await gtAgentManager.get_agent(self.team_id, "alice")
        ctrl_room = await self._ctrl_room_idle_for(alice.id)

        with patch("service.messageBus.publish"):
            await ctrl_room.add_message(ctrl_room.OPERATOR_MEMBER_ID, "普通排队消息")
        assert ctrl_room.has_unread_messages(alice.id) is True
        assert ctrl_room.has_unread_immediate_messages(alice.id) is False

        await self._run_loop_once(alice.id)

        assert ctrl_room.has_unread_messages(alice.id) is True, (
            "普通消息不应在 turn 的安全边界被提前读取（产品文档 §2.1）"
        )

    async def test_immediate_message_read_at_safe_boundary(self):
        """立即注入消息在安全边界被提前读取，且连带收取排在前面的普通消息。"""
        alice = await gtAgentManager.get_agent(self.team_id, "alice")
        ctrl_room = await self._ctrl_room_idle_for(alice.id)

        with patch("service.messageBus.publish"):
            await ctrl_room.add_message(ctrl_room.OPERATOR_MEMBER_ID, "先发的普通消息 M1")
            await ctrl_room.add_message(ctrl_room.OPERATOR_MEMBER_ID, "后发的立即注入 M2",
                                        insert_immediately=True)
        assert ctrl_room.has_unread_immediate_messages(alice.id) is True

        await self._run_loop_once(alice.id)

        assert ctrl_room.has_unread_messages(alice.id) is False, (
            "immediate 触发后应连带收取全部未读（含前面的普通消息）"
        )

    async def test_insert_immediately_message_wakes_idle_agent(self):
        """回归：Agent 空闲时发 insert_immediately 消息必须唤醒房间并触发调度。

        旧实现的 bug 表现：消息进入 pending 队列，但 agent 的消费协程没被唤醒，
        消息永远停在「将尽快注入」。
        """
        alice = await gtAgentManager.get_agent(self.team_id, "alice")
        alice_agent = agentService.get_agent(alice.id)
        consumer = alice_agent.job_consumer

        consumer.stop()
        await asyncio.sleep(0)
        assert consumer.status in (AgentStatus.IDLE, None), f"agent 应为 IDLE，实际: {consumer.status}"

        ctrl_room = await self._ctrl_room_idle_for(alice.id)

        status_events: list[tuple] = []

        def capture_publish(topic, **kwargs):
            status_events.append((topic, kwargs))

        with patch("service.messageBus.publish", side_effect=capture_publish):
            await ctrl_room.add_message(
                ctrl_room.OPERATOR_MEMBER_ID, "紧急消息", insert_immediately=True,
            )

        # 消息写入即分配 seq、立即成为未读的 immediate 消息（不再有 pending 队列中间态）
        assert ctrl_room.has_unread_immediate_messages(alice.id) is True

        scheduling_events = [
            e for e in status_events
            if e[0] == MessageBusTopic.ROOM_STATUS_CHANGED and e[1].get("need_scheduling") is True
        ]
        assert scheduling_events, "IDLE 房间收到 immediate 消息后应发布 need_scheduling=True 状态事件"
        assert scheduling_events[-1][1]["current_turn_agent_id"] == alice.id
        assert scheduling_events[-1][1]["state"] == RoomState.SCHEDULING

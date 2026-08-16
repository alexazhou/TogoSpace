"""集成测试：Agent 控制房间的消息注入行为。

测试场景：
1. Agent 处理群聊 turn 期间，控制房间收到 OPERATOR 普通消息应被及时注入
2. Agent IDLE 状态下发送 insert_immediately 消息，消息卡在 pending 无人唤醒（bug 复现）
"""
import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from constants import AgentHistoryTag, AgentStatus, OpenaiApiRole, TurnStepResult
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
    """agent 处理群聊 turn 期间，控制房间普通消息应被 loop 在安全边界注入。"""

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

    async def test_ctrl_room_regular_message_injected_during_group_turn(self):
        """OPERATOR 在控制房间（IDLE 状态）发普通消息，agent 在 turn loop 的安全边界应检测并注入。

        验证：_run_turn_loop 结束后 ctrl_room.has_unread_messages 为 False，
        说明消息已被消费（注入到 agent history），而不是等到控制房间下次被调度。
        """
        alice = await gtAgentManager.get_agent(self.team_id, "alice")
        alice_agent = agentService.get_agent(alice.id)
        turn_runner = alice_agent.task_consumer._turn_runner

        # 获取控制房间，让 alice 完成一轮使其进入 IDLE
        ctrl_room, _ = await roomService.get_or_create_control_room(self.team_id, alice.id)
        with patch("service.messageBus.publish"):
            await ctrl_room.handle_finish_request(alice.id)

        # 消费掉 ctrl_room 里 alice 的所有已有未读（系统初始消息等）
        await ctrl_room.get_unread_messages(alice.id)
        assert not ctrl_room.has_unread_messages(alice.id)

        # OPERATOR 向 IDLE 状态的控制房间发普通消息（触发 IDLE→SCHEDULING，消息有 seq）
        with patch("service.messageBus.publish"):
            await ctrl_room.add_message(ctrl_room.OPERATOR_MEMBER_ID, "operator 在 agent 处理群聊时发的消息")
        assert ctrl_room.has_unread_messages(alice.id), "消息应为 ctrl_room 中 alice 的未读消息"

        # 设置 turn_runner history 末尾为 USER 消息，使 is_safe_for_immediate_insert() 返回 True
        await turn_runner._history.append_history_message(GtAgentHistory.build(
            AgentMessage.from_openai(llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "群聊消息")),
            tags=[AgentHistoryTag.ROOM_TURN_BEGIN],
        ))

        # mock driver（测试目标是注入逻辑，不测 driver 行为）
        turn_runner.driver = MagicMock()
        turn_runner.driver.turn_setup = SimpleNamespace(max_retries=1, hint_prompt="")

        # mock _advance_step 避免真实 LLM 调用
        turn_runner._advance_step = AsyncMock(return_value=TurnStepResult.TURN_DONE)

        # 模拟 alice 正在处理的群聊房间
        group_room = MagicMock(spec=ChatRoom)

        with patch("service.agentService.agentTurnRunner.agentActivityService.add_activity", new=AsyncMock()):
            await turn_runner._run_turn_loop(group_room)

        # 验证：ctrl_room 的未读消息已被消费（注入后游标前进）
        assert not ctrl_room.has_unread_messages(alice.id), (
            "turn loop 应检测到 ctrl_room 的未读消息并注入，注入后 has_unread_messages 应为 False"
        )

    async def test_insert_immediately_message_stuck_when_agent_idle(self):
        """复现：Agent IDLE 时 send insert_immediately 消息，消息卡在 pending 无人唤醒。

        验证：消息入了 pending 队列，但 Agent 的消费协程未被启动，
        导致消息永远停留在"将尽快注入"状态。
        """
        alice = await gtAgentManager.get_agent(self.team_id, "alice")
        alice_agent = agentService.get_agent(alice.id)
        consumer = alice_agent.task_consumer

        # 确保 consumer 处于 IDLE 状态（没有运行中的消费协程）
        consumer.stop()
        await asyncio.sleep(0)
        assert consumer.status in (AgentStatus.IDLE, None), f"agent 应为 IDLE，实际: {consumer.status}"

        # 获取控制房间（处于 SCHEDULING 状态）
        ctrl_room, _ = await roomService.get_or_create_control_room(self.team_id, alice.id)

        # 发送 insert_immediately=True 消息
        with patch("service.messageBus.publish"):
            await ctrl_room.add_message(
                ctrl_room.OPERATOR_MEMBER_ID, "紧急消息", insert_immediately=True,
            )

        # 验证消息已进入 pending 队列
        assert ctrl_room.has_pending_immediate_messages(alice.id), (
            "insert_immediately 消息应进入 pending 队列"
        )

        # 验证消费协程未被唤醒（bug 的核心表现）
        consumer_task = consumer._aio_consumer_task
        consumer_not_running = consumer_task is None or consumer_task.done()
        assert consumer_not_running, (
            f"BUG: IDLE agent 的 consumer 未被唤醒。"
            f" consumer_task={consumer_task}, done={consumer_task.done() if consumer_task else 'N/A'}"
        )
        assert consumer.status == AgentStatus.IDLE, (
            f"BUG: agent 仍为 IDLE，未被唤醒。status={consumer.status}"
        )

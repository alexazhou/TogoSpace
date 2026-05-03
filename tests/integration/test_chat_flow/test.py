"""integration tests — 验证多 Agent 完整对话流程（mock LLM，真实 service 层）"""
import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import service.roomService as roomService
import service.agentService as agentService
import service.funcToolService as funcToolService
import service.schedulerService as scheduler
import service.ormService as ormService
import service.persistenceService as persistenceService
import service.presetService as presetService
from model.dbModel.gtAgentHistory import GtAgentHistory
from model.dbModel.gtAgentTask import GtAgentTask
from util import configUtil
from util.llmApiUtil import OpenAIMessage, OpenAIToolCall
from constants import AgentHistoryTag, AgentHistoryStatus, AgentStatus, AgentTaskType, OpenaiApiRole, RoomState, ScheduleState
from service import messageBus
from ...base import ServiceTestCase

TEAM = "test_team"
_CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config")

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")



class TestIntegrationMultiAgentChat(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        # 按真实启动顺序拉起 service，并加载 integration 专用配置。
        cfg = configUtil.load(_CONFIG_DIR, preset_dir=_CONFIG_DIR, force_reload=True)
        team_config = cfg.teams[0]
        db_path = cls._get_test_db_path()
        await ormService.startup(db_path)
        await persistenceService.startup()
        await agentService.startup()
        await roomService.startup()
        await presetService._import_role_templates_from_app_config()
        await presetService._import_team_from_config(team_config)
        await roomService.load_all_rooms()
        await funcToolService.startup()
        await agentService.load_all_team_agents()
        await scheduler.startup()
        scheduler._schedule_state = ScheduleState.RUNNING

    @classmethod
    async def async_teardown_class(cls):
        scheduler.shutdown()
        await agentService.shutdown()
        funcToolService.shutdown()
        roomService.shutdown()
        await persistenceService.shutdown()
        await ormService.shutdown()
        await messageBus.shutdown()

    async def test_two_agents_exchange_messages(self):
        """alice 和 bob 各发一轮消息，general 房间应有消息。"""
        room_key = f"general@{TEAM}"

        call_seq = {
            "alice": [{"tool_calls": [{"name": "send_chat_msg", "arguments": {"room_name": "general", "msg": "你好，bob！"}}]}, {"tool_calls": [{"name": "finish_chat_turn", "arguments": {}}]}],
            "bob":   [{"tool_calls": [{"name": "send_chat_msg", "arguments": {"room_name": "general", "msg": "你好，alice！"}}]}, {"tool_calls": [{"name": "finish_chat_turn", "arguments": {}}]}],
        }

        async def fake_infer(model, ctx):
            name = next((n for n in call_seq if f"你当前的名字：{n}" in ctx.system_prompt), None)
            res = call_seq[name].pop(0) if name and call_seq[name] else {"tool_calls": [{"name": "send_chat_msg", "arguments": {"room_name": "general", "msg": "..."}}]}
            return self.normalize_to_mock(res)

        with self.patch_infer(handler=fake_infer):
            # 重新创建 max_turns=1 的同名房间，快速触发"每人一轮"场景。
            await self.create_room(TEAM, "general", ["alice", "bob"], max_turns=1)
            room = roomService.get_room_by_key(room_key)
            await room.activate_scheduling()
            await self.wait_until(
                lambda: len([m for m in room.messages if m.sender_id != room.SYSTEM_MEMBER_ID]) >= 2,
                timeout=2.0,
                message="alice 和 bob 未在限时内完成一轮对话",
            )

        agent_messages = [m for m in room.messages if m.sender_id != room.SYSTEM_MEMBER_ID]
        assert len(agent_messages) >= 2

    async def test_tool_call_result_appended_to_history(self):
        """验证 tool_call 结果被正确追加到 agent history。"""
        await self.create_room(TEAM, "manual_turn", ["alice", "bob"])
        room = roomService.get_room_by_key(f"manual_turn@{TEAM}")
        await room.activate_scheduling()

        alice = agentService.get_agent(agentService.get_agent_id_by_stable_name(room.team_id, "alice"))
        item = GtAgentHistory.build(
            OpenAIMessage.text(OpenaiApiRole.SYSTEM, "reset test turn state"),
        )
        item.agent_id = alice.gt_agent.id
        item.seq = 0
        alice.inject_history_messages([item])
        call_seq = {
            "alice": [
                {"tool_calls": [{"name": "send_chat_msg", "arguments": {"room_name": "manual_turn", "msg": "hello"}}]},
                {"tool_calls": [{"name": "finish_chat_turn", "arguments": {}}]},
            ],
            "bob": [],
        }

        async def fake_infer(model, ctx):
            name = "alice" if "你当前的名字：alice" in ctx.system_prompt else "bob"
            if call_seq[name]:
                return self.normalize_to_mock(call_seq[name].pop(0))
            # 兜底返回 finish，避免并发调度时 side_effect 耗尽导致 StopIteration。
            return self.normalize_to_mock({"tool_calls": [{"name": "finish_chat_turn", "arguments": {}}]})

        task = GtAgentTask(
            id=1,
            agent_id=alice.gt_agent.id,
            task_type=AgentTaskType.ROOM_MESSAGE,
            task_data={"room_id": room.room_id},
        )
        with self.patch_infer(handler=fake_infer):
            await alice.task_consumer._turn_runner.run_chat_turn(task)

        tool_results = [m for m in alice.task_consumer._turn_runner._history if m.role == OpenaiApiRole.TOOL]
        assert len(tool_results) >= 1
        assert json.loads(tool_results[0].content)["success"]
        assert tool_results[0].status == AgentHistoryStatus.SUCCESS
        assert tool_results[0].error_message is None
        assert any(AgentHistoryTag.ROOM_TURN_FINISH in msg.tags for msg in tool_results)

    async def test_turn_checker_forces_send_chat_msg(self):
        """直接输出文字时 turn_checker 应注入 hint，迫使 agent 改用工具。"""
        await self.create_room(TEAM, "turn_checker_room", ["alice", "bob"])
        room = roomService.get_room_by_key(f"turn_checker_room@{TEAM}")
        await room.activate_scheduling()

        alice = agentService.get_agent(agentService.get_agent_id_by_stable_name(room.team_id, "alice"))
        item = GtAgentHistory.build(
            OpenAIMessage.text(OpenaiApiRole.SYSTEM, "reset turn checker history"),
        )
        item.agent_id = alice.gt_agent.id
        item.seq = 0
        alice.inject_history_messages([item])
        resps = [
            {"content": "我直接回复"},
            {"tool_calls": [{"name": "send_chat_msg", "arguments": {"room_name": "turn_checker_room", "msg": "最终消息"}}]},
            {"tool_calls": [{"name": "finish_chat_turn", "arguments": {}}]},
        ]
        task = GtAgentTask(
            id=2,
            agent_id=alice.gt_agent.id,
            task_type=AgentTaskType.ROOM_MESSAGE,
            task_data={"room_id": room.room_id},
        )
        with self.patch_infer(responses=resps):
            await alice.task_consumer._turn_runner.run_chat_turn(task)

        assert any(m.content == "最终消息" for m in room.messages)

    async def test_scheduler_terminates_after_max_turns(self):
        """max_turns 用尽后，通过观察 Room 状态并停止调度器。"""
        scheduler.shutdown()
        await scheduler.startup()
        scheduler._schedule_state = ScheduleState.RUNNING
        room_key = f"general@{TEAM}"
        room = roomService.get_room_by_key(room_key)
        for agent_name in ["alice", "bob"]:
            agent = agentService.get_agent(agentService.get_agent_id_by_stable_name(room.team_id, agent_name))
            agent.task_consumer.status = AgentStatus.IDLE
            agent.task_consumer.current_db_task = None
            agent.inject_history_messages([])

        # 预定义每个 agent 的调用序列
        call_seq = {
            "alice": [
                {"tool_calls": [{"name": "send_chat_msg", "arguments": {"room_name": "general", "msg": "a message"}}]},
                {"tool_calls": [{"name": "finish_chat_turn", "arguments": {}}]},
                {"tool_calls": [{"name": "send_chat_msg", "arguments": {"room_name": "general", "msg": "a message"}}]},
                {"tool_calls": [{"name": "finish_chat_turn", "arguments": {}}]},
            ],
            "bob": [
                {"tool_calls": [{"name": "send_chat_msg", "arguments": {"room_name": "general", "msg": "a message"}}]},
                {"tool_calls": [{"name": "finish_chat_turn", "arguments": {}}]},
                {"tool_calls": [{"name": "send_chat_msg", "arguments": {"room_name": "general", "msg": "a message"}}]},
                {"tool_calls": [{"name": "finish_chat_turn", "arguments": {}}]},
            ],
        }

        async def fake_infer(model, ctx):
            name = "alice" if "你当前的名字：alice" in ctx.system_prompt else "bob"
            if call_seq[name]:
                res = call_seq[name].pop(0)
            else:
                res = {"tool_calls": [{"name": "finish_chat_turn", "arguments": {}}]}
            return self.normalize_to_mock(res)

        with self.patch_infer(handler=fake_infer):
            await self.create_room(TEAM, "general", ["alice", "bob"], max_turns=2)
            room = roomService.get_room_by_key(room_key)
            await room.activate_scheduling()
            await self.wait_until(
                lambda: room.state == RoomState.IDLE,
                timeout=3.0,
                message="房间未在限时内进入 IDLE 状态",
            )

        # 1 条公告 + 2轮×2人 = 5 条消息
        assert len(room.messages) == 5

    async def test_cancelled_tool_result_is_skipped(self):
        """CANCELLED 状态的 TOOL 记录应被跳过，而不是抛出 RuntimeError。

        场景：agent 执行 tool_call_1 时被用户手动取消，TOOL 记录状态为 CANCELLED。
        当恢复执行时，应跳过已取消的 tool，继续执行下一个 pending tool_call_2。
        """
        await self.create_room(TEAM, "cancelled_tool_room", ["alice", "bob"])
        room = roomService.get_room_by_key(f"cancelled_tool_room@{TEAM}")
        await room.activate_scheduling()

        alice = agentService.get_agent(agentService.get_agent_id_by_stable_name(room.team_id, "alice"))

        # 构造历史：USER -> ASSISTANT(tool_call_1, tool_call_2) -> TOOL(call_1, CANCELLED)
        # 模拟：call_1 执行中被取消，call_2 还未执行
        tool_call_1 = OpenAIToolCall(id="call_cancelled", function={"name": "send_chat_msg", "arguments": '{"room_name": "cancelled_tool_room", "msg": "cancelled msg"}'})
        tool_call_2 = OpenAIToolCall(id="call_pending", function={"name": "send_chat_msg", "arguments": '{"room_name": "cancelled_tool_room", "msg": "pending msg"}'})

        assistant_msg = OpenAIMessage(
            role=OpenaiApiRole.ASSISTANT,
            content="",
            tool_calls=[tool_call_1, tool_call_2],
        )

        # USER 消息
        user_item = GtAgentHistory.build(
            OpenAIMessage.text(OpenaiApiRole.USER, "请发送两条消息"),
            tags=[AgentHistoryTag.ROOM_TURN_BEGIN],
        )
        user_item.agent_id = alice.gt_agent.id
        user_item.seq = 0

        # ASSISTANT 消息（两个 tool_calls）
        assistant_item = GtAgentHistory.build(
            assistant_msg,
            status=AgentHistoryStatus.SUCCESS,
        )
        assistant_item.agent_id = alice.gt_agent.id
        assistant_item.seq = 1

        # TOOL 记录（call_1 已取消）
        tool_cancelled_item = GtAgentHistory.build(
            OpenAIMessage.tool_result("call_cancelled", "cancelled by user"),
            status=AgentHistoryStatus.CANCELLED,
            error_message="cancelled by user",
        )
        tool_cancelled_item.agent_id = alice.gt_agent.id
        tool_cancelled_item.seq = 2

        alice.inject_history_messages([user_item, assistant_item, tool_cancelled_item])

        # 验证：get_first_pending_tool_call 应返回 call_2
        pending = alice.task_consumer._turn_runner._history.get_first_pending_tool_call()
        assert pending is not None
        assert pending.id == "call_pending"

        # 后续推理：返回 finish_chat_turn 结束 turn
        task = GtAgentTask(
            id=3,
            agent_id=alice.gt_agent.id,
            task_type=AgentTaskType.ROOM_MESSAGE,
            task_data={"room_id": room.room_id},
        )

        responses = [
            {"tool_calls": [{"name": "send_chat_msg", "arguments": {"room_name": "cancelled_tool_room", "msg": "pending msg"}}]},
            {"tool_calls": [{"name": "finish_chat_turn", "arguments": {}}]},
        ]

        with self.patch_infer(responses=responses):
            # 旧代码：CANCELLED 状态的 TOOL 会抛出 RuntimeError
            # 新代码：CANCELLED 状态的 TOOL 会跳过并继续推进
            await alice.task_consumer._turn_runner.run_chat_turn(task)

        # 验证：turn 正常完成，没有抛出异常
        assert any(AgentHistoryTag.ROOM_TURN_FINISH in msg.tags for msg in alice.task_consumer._turn_runner._history)

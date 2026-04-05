"""AgentTurnRunner: Turn 内部逻辑 — 消息同步、host loop、推理、工具调用编排。"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Awaitable, Callable, List, Optional

from constants import (
    AgentHistoryStage, AgentHistoryStatus, AgentHistoryTag,
    RoomState,
)
from model.coreModel.gtCoreChatModel import GtCoreChatMessage
from model.dbModel.gtAgentHistory import GtAgentHistory
from model.dbModel.gtAgentTask import GtAgentTask
from model.coreModel.gtCoreChatModel import GtCoreAgentDialogContext
from service import llmService, roomService
from service.agentService.driver import AgentTurnSetup
from service.agentService.promptBuilder import build_turn_context_prompt, format_room_message
from service.agentService.toolRegistry import ToolExecutionResult
from service.roomService import ChatRoom, ToolCallContext
from util import llmApiUtil

if TYPE_CHECKING:
    from service.agentService.agent import Agent

logger = logging.getLogger(__name__)


class AgentTurnRunner:
    """负责 Turn 内部逻辑：消息同步、host loop 执行、推理、工具调用编排。"""

    def __init__(self, agent: Agent):
        self._agent = agent

    # ─── Turn 运行方法 ──────────────────────────────────────

    async def run_chat_turn(self, task: GtAgentTask, resumed: bool = False) -> None:
        """执行一个完整 chat turn：同步房间消息 → 推理 → 工具调用循环。
        若 resumed=True 且存在未完成 turn，则走续跑路径。"""
        room_id = task.task_data.get("room_id")
        if room_id is None:
            logger.warning(f"run_chat_turn 跳过：task 缺少 room_id, agent_id={self._agent.gt_agent.id}, task_id={task.id}")
            return

        room: ChatRoom | None = roomService.get_room(room_id)
        if room is None:
            logger.warning(f"run_chat_turn 跳过：room_id={room_id} 不存在, agent_id={self._agent.gt_agent.id}")
            return

        if self._agent.driver.host_managed_turn_loop:
            if resumed and self._agent._history.has_unfinished_turn():
                await self._resume_chat_turn_with_host_loop(room)
                return
            synced_count = await self.pull_room_messages_to_history(room)
            assert self._agent.driver.started is True, f"driver 尚未启动: agent_id={self._agent.gt_agent.id}"
            await self._run_chat_turn_with_host_loop(room)
        else:
            synced_count = await self.pull_room_messages_to_history(room)
            await self._agent.driver.run_chat_turn(task, synced_count)

    async def pull_room_messages_to_history(self, room: ChatRoom) -> int:
        """从房间拉取未读消息并追加到 history。返回追加的消息条目数（0 或 1）。"""
        new_msgs: List[GtCoreChatMessage] = await room.get_unread_messages(self._agent.gt_agent.id)
        logger.info(f"同步房间消息: agent_id={self._agent.gt_agent.id}, room={room.name}, count={len(new_msgs)}")

        message_blocks: list[str] = []
        for msg in new_msgs:
            if msg.sender_id == self._agent.gt_agent.id:
                continue
            sender_name = room._get_agent_name(msg.sender_id)
            message_blocks.append(format_room_message(room.name, sender_name, msg.content))

        if len(message_blocks) == 0:
            return 0

        turn_context_message = llmApiUtil.OpenAIMessage.text(
            llmApiUtil.OpenaiLLMApiRole.USER,
            content=build_turn_context_prompt(room.name, message_blocks),
        )
        await self._agent._history.append_history_message(turn_context_message,
            stage=AgentHistoryStage.INPUT,
            tags=[AgentHistoryTag.ROOM_TURN_BEGIN],
        )
        return 1

    async def _run_chat_turn_with_host_loop(self, room: ChatRoom) -> None:
        """Host-managed turn loop：循环推理+工具调用，直到 turn 完成或达到最大重试次数。"""
        turn_setup: AgentTurnSetup = self._agent.driver.turn_setup
        tools: list[llmApiUtil.OpenAITool] = self._agent.tool_registry.export_openai_tools()
        max_retries = max(1, turn_setup.max_retries)
        for _ in range(max_retries):
            turn_done = await self._run_until_reply(room, tools=tools)
            if turn_done:
                return
            if len(turn_setup.hint_prompt) > 0:
                await self._agent._history.append_history_message(
                    llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiLLMApiRole.USER, turn_setup.hint_prompt),
                    stage=AgentHistoryStage.INPUT,
                )

    async def _resume_chat_turn_with_host_loop(self, room: ChatRoom) -> None:
        """续跑 host-managed turn loop：根据最后一条 history item 的阶段和状态，从断点处恢复执行。"""
        tools: list[llmApiUtil.OpenAITool] = self._agent.tool_registry.export_openai_tools()
        turn_start_idx = self._agent._history.get_unfinished_turn_start_index()
        if turn_start_idx is None:
            await self._run_chat_turn_with_host_loop(room)
            return
        last_item = self._agent._history.last()
        if last_item is None:
            await self._run_chat_turn_with_host_loop(room)
            return

        if last_item.stage == AgentHistoryStage.INFER and last_item.status in (AgentHistoryStatus.INIT, AgentHistoryStatus.FAILED):
            assistant_message = await self._infer(tools, resume_item=last_item)
            tool_calls = assistant_message.tool_calls or []
            if tool_calls:
                turn_done = await self._dispatch_tool_calls(room, tool_calls)
                if turn_done:
                    return

        elif last_item.stage == AgentHistoryStage.TOOL_RESULT and last_item.status == AgentHistoryStatus.INIT:
            tool_call_id = str(last_item.tool_call_id or "")
            tool_call = self._agent._history.find_tool_call_by_id(tool_call_id, start_idx=turn_start_idx)
            if tool_call is None:
                raise RuntimeError(f"resume tool call not found: agent_id={self._agent.gt_agent.id}, tool_call_id={tool_call_id}")
            turn_done = await self._dispatch_tool_calls(
                room,
                [tool_call],
                reuse_history_items={tool_call_id: last_item},
            )
            if turn_done:
                return

        else:
            last_assistant = self._agent._history.get_last_assistant_message(start_idx=turn_start_idx)
            if last_assistant is not None and last_assistant.tool_calls:
                turn_done = await self._dispatch_tool_calls(
                    room,
                    last_assistant.tool_calls,
                    execute_only_missing=True,
                )
                if turn_done:
                    return

        await self._run_chat_turn_with_host_loop(room)

    async def _run_until_reply(self, room: ChatRoom, tools: Optional[list[llmApiUtil.OpenAITool]]) -> bool:
        """在 max_function_calls 次内循环：推理 → 工具调用。返回 True 表示 turn 结束（agent 调用了 finish 工具）。"""
        max_function_calls = self._agent.max_function_calls
        for _ in range(max_function_calls):
            assistant_message: llmApiUtil.OpenAIMessage = await self._infer(tools)
            tool_calls = assistant_message.tool_calls or []
            if len(tool_calls) == 0:
                return False

            turn_done = await self._dispatch_tool_calls(room, tool_calls)
            if turn_done:
                return True

        logger.warning(f"达到最大函数调用次数: agent_id={self._agent.gt_agent.id}, max={max_function_calls}")
        return False

    async def _infer(
        self,
        tools: Optional[list[llmApiUtil.OpenAITool]],
        *,
        resume_item: GtAgentHistory | None = None,
    ) -> llmApiUtil.OpenAIMessage:
        """执行一次 LLM 推理。若 resume_item 不为 None，则为续跑（复用已有 history item，跳过最后一条消息）。"""
        history = self._agent._history
        if resume_item is None:
            history.assert_infer_ready(f"agent_id={self._agent.gt_agent.id}")

        ctx_tools: list[llmApiUtil.OpenAITool] | None = None
        if tools is not None and len(tools) > 0:
            ctx_tools = tools

        messages = history.export_openai_message_list()
        if resume_item is not None:
            messages = messages[:-1]

        ctx = GtCoreAgentDialogContext(
            system_prompt=self._agent.system_prompt,
            messages=messages,
            tools=ctx_tools,
        )
        history_item = resume_item or await history.append_stage_init(stage=AgentHistoryStage.INFER)
        infer_result: llmService.InferResult = await llmService.infer(self._agent.gt_agent.model, ctx)
        if infer_result.ok is False or infer_result.response is None:
            error_message = infer_result.error_message or "unknown inference error"
            await history.finalize_history_item(
                history_id=history_item.id,
                message=None,
                status=AgentHistoryStatus.FAILED,
                error_message=error_message,
            )
            raise RuntimeError(f"LLM 推理失败: agent_id={self._agent.gt_agent.id}, error={error_message}") from infer_result.error

        assistant_message = infer_result.response.choices[0].message
        await history.finalize_history_item(
            history_id=history_item.id,
            message=assistant_message,
            status=AgentHistoryStatus.SUCCESS,
        )
        return assistant_message

    async def _execute_tool(self) -> None:
        """执行最后一条 assistant 消息中的所有 tool calls（AgentDriverHost 协议方法）。
        通过 tool_registry 统一分发，所有 driver 共享同一条执行路径。"""
        current_db_task = self._agent.current_db_task
        assert current_db_task is not None, "current_db_task should not be None while executing tool"
        room_id = current_db_task.task_data.get("room_id")
        assert room_id is not None, "current_db_task should have room_id"
        room = roomService.get_room(room_id)
        assert room is not None, f"room should exist: room_id={room_id}"

        last_msg: llmApiUtil.OpenAIMessage | None = self._agent._history.get_last_assistant_message()
        if last_msg is None or last_msg.tool_calls is None or len(last_msg.tool_calls) == 0:
            return

        await self._dispatch_tool_calls(room, last_msg.tool_calls)

    async def _dispatch_tool_calls(
        self,
        room: ChatRoom,
        tool_calls: list[llmApiUtil.OpenAIToolCall],
        *,
        reuse_history_items: dict[str, GtAgentHistory] | None = None,
        execute_only_missing: bool = False,
    ) -> bool:
        """批量执行 tool calls 并记录到 history。
        reuse_history_items: 续跑时复用已有 history item。
        execute_only_missing: 仅执行尚未有结果的 tool call（跳过已完成的）。
        返回 True 表示 turn 已完成。"""
        logger.info(f"检测到工具调用: agent_id={self._agent.gt_agent.id}, count={len(tool_calls)}")
        context: ToolCallContext = ToolCallContext(
            agent_name=self._agent.gt_agent.name,
            team_id=room.team_id,
            chat_room=room,
        )
        turn_done = False
        for tool_call in tool_calls:
            tool_call_id = str(tool_call.id or "")
            history_item = None
            existing_result = self._agent._history.find_tool_result_by_call_id(tool_call_id)
            if reuse_history_items is not None:
                history_item = reuse_history_items.get(tool_call_id)
            elif execute_only_missing and existing_result is not None:
                if existing_result.status == AgentHistoryStatus.INIT:
                    history_item = existing_result
                else:
                    if AgentHistoryTag.ROOM_TURN_FINISH in existing_result.tags and (
                        existing_result.status == AgentHistoryStatus.SUCCESS
                        or room.state == RoomState.INIT
                    ):
                        turn_done = True
                    continue

            exec_result = await self._execute_and_record_tool_call(
                tool_call,
                lambda: self._agent.tool_registry.execute_tool_call(tool_call, context),
                existing_item=history_item,
            )
            if exec_result.turn_finished and (
                exec_result.status == AgentHistoryStatus.SUCCESS
                or room.state == RoomState.INIT
            ):
                turn_done = True
        return turn_done

    async def _execute_and_record_tool_call(
        self,
        tool_call: llmApiUtil.OpenAIToolCall,
        executor: Callable[[], Awaitable[ToolExecutionResult]],
        *,
        existing_item: GtAgentHistory | None = None,
    ) -> ToolExecutionResult:
        """执行单个 tool call 并记录到 history。若 existing_item 不为 None，则复用已有 history item（续跑场景）。"""
        assert tool_call.id, "tool_call.id should not be empty"
        history_item = existing_item or await self._agent._history.append_stage_init(
            stage=AgentHistoryStage.TOOL_RESULT,
            tool_call_id=str(tool_call.id),
        )
        assert history_item.id is not None, "history_item.id should not be None after append"
        exec_result = await executor()
        final_message = llmApiUtil.OpenAIMessage.tool_result(exec_result.tool_call_id, exec_result.result_json)
        await self._agent._history.finalize_history_item(
            history_id=history_item.id,
            message=final_message,
            status=exec_result.status,
            error_message=exec_result.error_message,
            tags=exec_result.tags,
        )
        return exec_result

"""AgentTurnRunner: Turn 内部逻辑 — 消息同步、host loop、推理、工具调用编排。

同时实现 AgentDriverHost 协议，作为 Driver 的宿主。
"""
from __future__ import annotations

import json
import logging
from typing import Awaitable, Callable, List, Optional

from constants import (
    AgentHistoryStage, AgentHistoryStatus, AgentHistoryTag,
    DriverType, RoomState,
)
from model.coreModel.gtCoreChatModel import GtCoreAgentDialogContext, GtCoreRoomMessage
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtAgentHistory import GtAgentHistory
from model.dbModel.gtAgentTask import GtAgentTask
from service import llmService, roomService
from service.agentService.agentHistoryStore import AgentHistoryStore
from service.agentService import compactPolicy, promptBuilder
from service.agentService.driver import AgentDriverConfig, AgentTurnSetup
from service.agentService.driver.factory import build_agent_driver
from service.agentService.toolRegistry import AgentToolRegistry, ToolExecutionResult
from service.roomService import ChatRoom, ToolCallContext
from util import configUtil, llmApiUtil
from util.assertUtil import assertNotNull

logger = logging.getLogger(__name__)


class AgentTurnRunner:
    """负责 Turn 内部逻辑：消息同步、host loop 执行、推理、工具调用编排。

    同时实现 AgentDriverHost 协议，是 Driver 的宿主（host）。
    自行构建 driver / tool_registry / history，不持有 Agent 引用。
    """

    def __init__(
        self,
        *,
        gt_agent: GtAgent,
        system_prompt: str,
        agent_workdir: str = "",
        max_function_calls: int = 5,
        driver_config: AgentDriverConfig | None = None,
    ):
        self.gt_agent: GtAgent = gt_agent
        self.system_prompt: str = system_prompt
        self.agent_workdir: str = agent_workdir
        self.max_function_calls: int = max(1, max_function_calls)
        self._history: AgentHistoryStore = AgentHistoryStore(gt_agent.id or 0)
        self.tool_registry: AgentToolRegistry = AgentToolRegistry()
        self.driver = build_agent_driver(self, driver_config or AgentDriverConfig(driver_type=DriverType.NATIVE))
        self._current_room: ChatRoom | None = None

    # ─── Turn 运行方法 ──────────────────────────────────────

    async def run_chat_turn(self, task: GtAgentTask) -> None:
        """执行一个完整 chat turn：同步房间消息 → 推理 → 工具调用循环。
        若存在未完成 turn，则走续跑路径。"""
        room_id = task.task_data.get("room_id")
        assertNotNull(room_id, error_message=f"task 缺少 room_id, agent_id={self.gt_agent.id}, task_id={task.id}")

        room = roomService.get_room(room_id)
        assertNotNull(room, error_message=f"room_id={room_id} 不存在, agent_id={self.gt_agent.id}")

        self._current_room = room
        try:
            if self.driver.host_managed_turn_loop:
                assert self.driver.started is True, f"driver 尚未启动: agent_id={self.gt_agent.id}"
                if not self._history.has_unfinished_turn():
                    synced_count = await self.pull_room_messages_to_history(room)
                    if synced_count == 0 and room.state != RoomState.INIT:
                        logger.info(f"无新消息，自动跳过本轮: {self.gt_agent.name}(agent_id={self.gt_agent.id}), room={room.name}")
                        await room.finish_turn(self.gt_agent.id)
                        return

                await self._run_turn_loop(room)

            else:
                synced_count = await self.pull_room_messages_to_history(room)
                await self.driver.run_chat_turn(task, synced_count)
        finally:
            self._current_room = None

    async def pull_room_messages_to_history(self, room: ChatRoom) -> int:
        """从房间拉取未读消息并追加到 history。返回追加的消息条目数（0 或 1）。"""
        new_msgs: List[GtCoreRoomMessage] = await room.get_unread_messages(self.gt_agent.id)

        own_count = sum(1 for msg in new_msgs if msg.sender_id == self.gt_agent.id)
        logger.info(f"同步房间消息: agent={self.gt_agent.name}(agent_id={self.gt_agent.id}), room={room.name}, raw={len(new_msgs)}, own={own_count}, others={len(new_msgs) - own_count}")

        if len(new_msgs) == own_count:
            return 0

        turn_prompt = promptBuilder.build_turn_begin_prompt_from_messages(
            room.name, new_msgs, self.gt_agent.id
        )
        await self._history.append_history_message(
            llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, turn_prompt),
            stage=AgentHistoryStage.INPUT,
            tags=[AgentHistoryTag.ROOM_TURN_BEGIN],
        )
        return 1

    async def _run_turn_loop(self, room: ChatRoom) -> None:
        """基于 history 状态推进的统一循环。"""
        tools = self.tool_registry.export_openai_tools()
        turn_setup: AgentTurnSetup = self.driver.turn_setup
        call_count = 0

        while call_count < self.max_function_calls:
            result = await self._advance_step(room, tools)

            # 调用了 finish，turn 结束
            if result == "turn_done":
                return

            # 无工具调用
            if result == "no_tool_calls":
                # 有 hint_prompt，追加提示继续推理
                if len(turn_setup.hint_prompt) > 0:
                    await self._history.append_history_message(
                        llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, turn_setup.hint_prompt),
                        stage=AgentHistoryStage.INPUT,
                    )
                    continue
                # 无 hint_prompt，turn 结束
                return

            # 有工具调用继续，计数
            call_count += 1

        logger.warning(f"达到最大函数调用次数: agent_id={self.gt_agent.id}, max={self.max_function_calls}")

    async def _advance_step(self, room: ChatRoom, tools: list[llmApiUtil.OpenAITool]) -> str:
        """根据当前 history 状态推进一步。

        返回:
            "turn_done": turn 已结束（调用了 finish）
            "no_tool_calls": 推理无工具调用
            "continue": 继续循环
        """
        last_item = self._history.last()
        if last_item is None:
            raise RuntimeError(f"history 为空，无法推进: agent_id={self.gt_agent.id}")

        # TOOL_RESULT.SUCCESS 特殊处理：检查是否有未完成的工具
        if last_item.stage == AgentHistoryStage.TOOL_RESULT and last_item.status == AgentHistoryStatus.SUCCESS:
            last_assistant = self._history.get_last_assistant_message_in_unfinished_turn()
            if last_assistant is not None and last_assistant.tool_calls:
                for tc in last_assistant.tool_calls:
                    result = self._history.find_tool_result_by_call_id(tc.id)
                    if result is None or result.status == AgentHistoryStatus.INIT:
                        # 有未完成的工具，转去执行
                        turn_done = await self._dispatch_tool_calls(
                            room, last_assistant.tool_calls, execute_only_missing=True
                        )
                        return "turn_done" if turn_done else "continue"
            # 没有未完成的工具，继续推理
            return await self._step_infer(tools)

        match (last_item.stage, last_item.status):
            case (AgentHistoryStage.INPUT, _) | \
                 (AgentHistoryStage.INFER, AgentHistoryStatus.INIT | AgentHistoryStatus.FAILED):
                return await self._step_infer(tools)

            case (AgentHistoryStage.INFER, AgentHistoryStatus.SUCCESS):
                return await self._step_execute_tools(room, tools)

            case (AgentHistoryStage.TOOL_RESULT, AgentHistoryStatus.INIT):
                return await self._step_resume_tool(room, last_item)

            case _:
                raise RuntimeError(f"无法推进: agent_id={self.gt_agent.id}, stage={last_item.stage}, status={last_item.status}")

    async def _step_infer(self, tools: list[llmApiUtil.OpenAITool]) -> str:
        """执行推理步骤。返回 'no_tool_calls' 或 'continue'。"""
        assistant_message = await self._infer(tools)
        tool_calls = assistant_message.tool_calls or []
        return "no_tool_calls" if len(tool_calls) == 0 else "continue"

    async def _step_execute_tools(self, room: ChatRoom, tools: list[llmApiUtil.OpenAITool]) -> str:
        """执行推理产生的工具调用。返回 'turn_done' 或 'continue'。"""
        last_assistant = self._history.get_last_assistant_message()
        if last_assistant is None or not last_assistant.tool_calls:
            return "no_tool_calls"

        turn_done = await self._dispatch_tool_calls(room, last_assistant.tool_calls)
        return "turn_done" if turn_done else "continue"

    async def _step_resume_tool(self, room: ChatRoom, pending_item: GtAgentHistory) -> str:
        """恢复执行单个待处理的工具。返回 'turn_done' 或 'continue'。"""
        tool_call_id = pending_item.tool_call_id
        tool_call = self._history.find_tool_call_by_id_in_unfinished_turn(tool_call_id)

        if tool_call is None:
            raise RuntimeError(f"工具调用不存在: agent_id={self.gt_agent.id}, tool_call_id={tool_call_id}")

        turn_done = await self._dispatch_tool_calls(
            room,
            [tool_call],
            reuse_history_items={tool_call_id: pending_item},
        )
        return "turn_done" if turn_done else "continue"

    # ─── AgentDriverHost 协议方法 ──────────────────────────

    def _resolve_compact_config(self) -> tuple[str, configUtil.LlmServiceConfig, int, int]:
        """获取 compact 相关配置：(resolved_model, llm_config, trigger_tokens, hard_limit_tokens)。"""
        llm_config = configUtil.get_app_config().setting.current_llm_service
        resolved_model = self.gt_agent.model or llm_config.model
        trigger_tokens = compactPolicy.calc_compact_trigger_tokens(resolved_model, llm_config)
        hard_limit_tokens = compactPolicy.calc_hard_limit_tokens(resolved_model, llm_config)
        return resolved_model, llm_config, trigger_tokens, hard_limit_tokens

    @staticmethod
    def _build_usage_json(
        *,
        estimated_prompt_tokens: int | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
        pre_check_triggered: bool = False,
        overflow_retry: bool = False,
    ) -> str:
        payload = {
            "estimated_prompt_tokens": estimated_prompt_tokens,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "pre_check_triggered": pre_check_triggered,
            "overflow_retry": overflow_retry,
        }
        return json.dumps(payload, ensure_ascii=False)

    async def _infer(
        self,
        tools: Optional[list[llmApiUtil.OpenAITool]],
        *,
        _skip_compact: bool = False,
    ) -> llmApiUtil.OpenAIMessage:
        """执行一次 LLM 推理，集成 token 预算 pre-check / overflow retry。

        若 _skip_compact 为 True，跳过所有 compact 检查（避免 compact 推理时递归）。
        """
        history = self._history
        history.assert_infer_ready(f"agent_id={self.gt_agent.id}")

        resolved_model, _, trigger_tokens, hard_limit_tokens = self._resolve_compact_config()
        estimated_tokens = 0
        pre_check_triggered = False
        overflow_retry = False
        usage: llmApiUtil.OpenAIUsage | None = None
        history_item: GtAgentHistory | None = None

        try:
            # ── 构造消息 + Pre-check ──
            messages = history.build_infer_messages()
            estimated_tokens = compactPolicy.estimate_tokens(resolved_model, messages, self.system_prompt)

            if not _skip_compact:
                messages, estimated_tokens, pre_check_triggered = await self._pre_check_compact(
                    messages, estimated_tokens,
                )

            pending_infer = history.get_pending_infer_item()
            if pending_infer is not None:
                history_item = pending_infer
            else:
                history_item = await history.append_history_init_item(stage=AgentHistoryStage.INFER)

            # ── 发起 LLM 请求 ──
            ctx = GtCoreAgentDialogContext(system_prompt=self.system_prompt, messages=messages, tools=tools)
            infer_result: llmService.InferResult = await llmService.infer(self.gt_agent.model, ctx)

            # ── overflow retry ──
            if infer_result.ok is False or infer_result.response is None:
                error = infer_result.error
                if (
                    not _skip_compact
                    and error is not None
                    and compactPolicy.is_context_overflow_error(error)
                    and not pre_check_triggered
                ):
                    logger.info(f"overflow retry 触发: {self.gt_agent.name}(agent_id={self.gt_agent.id}), error={infer_result.error_message}")
                    overflow_retry = True
                    await self._execute_compact()
                    messages = history.build_infer_messages()
                    estimated_tokens = compactPolicy.estimate_tokens(resolved_model, messages, self.system_prompt)
                    if estimated_tokens >= hard_limit_tokens:
                        raise RuntimeError(f"overflow compact 后仍超限: agent_id={self.gt_agent.id}") from error

                    ctx = GtCoreAgentDialogContext(
                        system_prompt=self.system_prompt, messages=messages, tools=tools,
                    )
                    infer_result = await llmService.infer(self.gt_agent.model, ctx)

                if infer_result.ok is False or infer_result.response is None:
                    error_message = infer_result.error_message or "unknown inference error"
                    if overflow_retry:
                        raise RuntimeError(f"LLM 推理失败(overflow retry): agent_id={self.gt_agent.id}, error={error_message}") from infer_result.error
                    raise RuntimeError(f"LLM 推理失败: agent_id={self.gt_agent.id}, error={error_message}") from infer_result.error

            usage = infer_result.usage
            assistant_message = infer_result.response.choices[0].message

            # ── 记录 usage 并 finalize ──
            usage_json = self._build_usage_json(
                estimated_prompt_tokens=estimated_tokens,
                prompt_tokens=usage.prompt_tokens if usage else None,
                completion_tokens=usage.completion_tokens if usage else None,
                total_tokens=usage.total_tokens if usage else None,
                pre_check_triggered=pre_check_triggered,
                overflow_retry=overflow_retry,
            )
            await history.finalize_history_item(
                history_id=history_item.id,
                message=assistant_message,
                status=AgentHistoryStatus.SUCCESS,
                usage_json=usage_json,
            )
            return assistant_message
        except Exception as e:
            if history_item is None:
                raise
            usage_json = self._build_usage_json(
                estimated_prompt_tokens=estimated_tokens or None,
                prompt_tokens=usage.prompt_tokens if usage else None,
                completion_tokens=usage.completion_tokens if usage else None,
                total_tokens=usage.total_tokens if usage else None,
                pre_check_triggered=pre_check_triggered,
                overflow_retry=overflow_retry,
            )
            await history.finalize_history_item(
                history_id=history_item.id,
                message=None,
                status=AgentHistoryStatus.FAILED,
                error_message=str(e),
                usage_json=usage_json,
            )
            raise

    async def _pre_check_compact(
        self,
        messages: list[llmApiUtil.OpenAIMessage],
        estimated_tokens: int,
    ) -> tuple[list[llmApiUtil.OpenAIMessage], int, bool]:
        """Pre-check：若估算 token 超阈值则执行 compact。

        Returns: (messages, estimated_tokens, pre_check_triggered)
        """
        resolved_model, _, trigger_tokens, hard_limit_tokens = self._resolve_compact_config()
        if estimated_tokens < trigger_tokens:
            return messages, estimated_tokens, False

        logger.info(f"pre-check compact 触发: {self.gt_agent.name}(agent_id={self.gt_agent.id}), estimated={estimated_tokens}, trigger={trigger_tokens}")
        await self._execute_compact()
        messages = self._history.build_infer_messages()
        estimated_tokens = compactPolicy.estimate_tokens(resolved_model, messages, self.system_prompt)
        if estimated_tokens >= hard_limit_tokens:
            raise RuntimeError(f"compact 后仍超限: agent_id={self.gt_agent.id}, estimated={estimated_tokens}, hard_limit={hard_limit_tokens}")
        return messages, estimated_tokens, True

    async def _execute_compact(self) -> None:
        """执行一次 compact：写入 COMPACT_CMD → 生成原始摘要 → 写入 user 摘要上下文 → 内存裁剪。

        流程：
        1. 追加压缩指令（user 消息，直接带 COMPACT_CMD tag）
        2. 直接调用 llmService.infer 生成原始 assistant 摘要
        3. 追加一条新的 user 摘要上下文消息，供后续推理使用
        4. 内存裁剪
        """
        _, llm_config, _, _ = self._resolve_compact_config()
        compact_plan = self._history.build_compact_plan()
        if not compact_plan.source_messages or compact_plan.insert_seq is None:
            logger.warning("compact 跳过：无可压缩消息, agent_id=%d", self.gt_agent.id)
            return

        compact_instruction = promptBuilder.build_compact_instruction(max_tokens=llm_config.compact_summary_max_tokens)
        instruction_msg = llmApiUtil.OpenAIMessage.text(
            llmApiUtil.OpenaiApiRole.USER, compact_instruction,
        )
        await self._history.append_history_message(
            instruction_msg,
            seq=compact_plan.insert_seq,
            stage=AgentHistoryStage.INPUT,
            status=AgentHistoryStatus.SUCCESS,
            tags=[AgentHistoryTag.COMPACT_CMD],
        )

        ctx = GtCoreAgentDialogContext(system_prompt=self.system_prompt, messages=compact_plan.source_messages + [instruction_msg], tools=None)
        infer_result: llmService.InferResult = await llmService.infer(self.gt_agent.model, ctx)
        if infer_result.ok is False or infer_result.response is None:
            error_message = infer_result.error_message or "compact inference failed"
            raise RuntimeError(f"LLM 推理失败(compact): agent_id={self.gt_agent.id}, error={error_message}") from infer_result.error

        summary_message = infer_result.response.choices[0].message
        await self._history.append_history_message(
            summary_message,
            seq=compact_plan.insert_seq + 1,
            stage=AgentHistoryStage.INFER,
            status=AgentHistoryStatus.SUCCESS,
        )

        compact_context = promptBuilder.build_compact_resume_prompt(summary_message.content or "")
        context_msg = llmApiUtil.OpenAIMessage.text(
            llmApiUtil.OpenaiApiRole.USER, compact_context,
        )
        await self._history.append_history_message(
            context_msg,
            seq=compact_plan.insert_seq + 2,
            stage=AgentHistoryStage.INPUT,
            status=AgentHistoryStatus.SUCCESS,
        )

        # 内存裁剪：只保留恢复 compact 视图所需的最小消息窗口
        self._history.trim_to_compact_window()
        logger.info(f"compact 完成: {self.gt_agent.name}(agent_id={self.gt_agent.id})")

    async def _execute_tool(self) -> None:
        """执行最后一条 assistant 消息中的所有 tool calls（AgentDriverHost 协议方法）。
        通过 _current_room 获取房间上下文，由 run_chat_turn 在调用前设置。"""
        room = self._current_room
        assert room is not None, "no current room context while executing tool"

        last_msg: llmApiUtil.OpenAIMessage | None = self._history.get_last_assistant_message()
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
        tool_names = [tc.function_name for tc in tool_calls]
        logger.info(f"检测到工具调用: {self.gt_agent.name}(agent_id={self.gt_agent.id}), tools={tool_names}")
        context: ToolCallContext = ToolCallContext(
            agent_name=self.gt_agent.name,
            team_id=room.team_id,
            chat_room=room,
        )
        turn_done = False
        for tool_call in tool_calls:
            tool_call_id = tool_call.id
            history_item = None
            existing_result = self._history.find_tool_result_by_call_id(tool_call_id)
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
                lambda: self.tool_registry.execute_tool_call(tool_call, context),
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
        history_item = existing_item or await self._history.append_history_init_item(
            stage=AgentHistoryStage.TOOL_RESULT,
            tool_call_id=tool_call.id,
        )
        assert history_item.id is not None, "history_item.id should not be None after append"
        exec_result = await executor()
        final_message = llmApiUtil.OpenAIMessage.tool_result(exec_result.tool_call_id, exec_result.result_json)
        await self._history.finalize_history_item(
            history_id=history_item.id,
            message=final_message,
            status=exec_result.status,
            error_message=exec_result.error_message,
            tags=exec_result.tags,
        )
        return exec_result

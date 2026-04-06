"""AgentTurnRunner: Turn 内部逻辑 — 消息同步、host loop、推理、工具调用编排。

同时实现 AgentDriverHost 协议，作为 Driver 的宿主。
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable, List, Optional

from constants import (
    AgentHistoryStage, AgentHistoryStatus, AgentHistoryTag,
    DriverType, RoomState,
)
from model.coreModel.gtCoreChatModel import GtCoreChatMessage
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtAgentHistory import GtAgentHistory
from model.dbModel.gtAgentTask import GtAgentTask
from model.coreModel.gtCoreChatModel import GtCoreAgentDialogContext
from service import llmService, roomService
from service.agentService.agentHistoryStore import AgentHistoryStore
from service.agentService import compactPolicy
from service.agentService.driver import AgentDriverConfig, AgentTurnSetup
from service.agentService.driver.factory import build_agent_driver
from service.agentService.promptBuilder import build_turn_context_prompt, format_room_message
from service.agentService.toolRegistry import AgentToolRegistry, ToolExecutionResult
from service.roomService import ChatRoom, ToolCallContext
from util import configUtil, llmApiUtil

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

    async def run_chat_turn(self, task: GtAgentTask, resumed: bool = False) -> None:
        """执行一个完整 chat turn：同步房间消息 → 推理 → 工具调用循环。
        若 resumed=True 且存在未完成 turn，则走续跑路径。"""
        room_id = task.task_data.get("room_id")
        if room_id is None:
            logger.warning(f"run_chat_turn 跳过：task 缺少 room_id, agent_id={self.gt_agent.id}, task_id={task.id}")
            return

        room: ChatRoom | None = roomService.get_room(room_id)
        if room is None:
            logger.warning(f"run_chat_turn 跳过：room_id={room_id} 不存在, agent_id={self.gt_agent.id}")
            return

        self._current_room = room
        try:
            if self.driver.host_managed_turn_loop:
                if resumed and self._history.has_unfinished_turn():
                    await self._resume_chat_turn_with_host_loop(room)
                    return
                synced_count = await self.pull_room_messages_to_history(room)
                if synced_count == 0 and room.state != RoomState.INIT:
                    logger.info(
                        "无新消息，自动跳过本轮: %s(agent_id=%d), room=%s",
                        self.gt_agent.name, self.gt_agent.id, room.name,
                    )
                    await room.finish_turn(self.gt_agent.id)
                    return
                assert self.driver.started is True, f"driver 尚未启动: agent_id={self.gt_agent.id}"
                await self._run_chat_turn_with_host_loop(room)
            else:
                synced_count = await self.pull_room_messages_to_history(room)
                await self.driver.run_chat_turn(task, synced_count)
        finally:
            self._current_room = None

    async def pull_room_messages_to_history(self, room: ChatRoom) -> int:
        """从房间拉取未读消息并追加到 history。返回追加的消息条目数（0 或 1）。"""
        new_msgs: List[GtCoreChatMessage] = await room.get_unread_messages(self.gt_agent.id)

        message_blocks: list[str] = []
        own_count = 0
        for msg in new_msgs:
            if msg.sender_id == self.gt_agent.id:
                own_count += 1
                continue
            sender_name = room._get_agent_name(msg.sender_id)
            message_blocks.append(format_room_message(room.name, sender_name, msg.content))

        logger.info(
            "同步房间消息: agent=%s(agent_id=%d), room=%s, raw=%d, own=%d, others=%d",
            self.gt_agent.name, self.gt_agent.id, room.name,
            len(new_msgs), own_count, len(message_blocks),
        )

        if len(message_blocks) == 0:
            return 0

        turn_context_message = llmApiUtil.OpenAIMessage.text(
            llmApiUtil.OpenaiLLMApiRole.USER,
            content=build_turn_context_prompt(room.name, message_blocks),
        )
        await self._history.append_history_message(turn_context_message,
            stage=AgentHistoryStage.INPUT,
            tags=[AgentHistoryTag.ROOM_TURN_BEGIN],
        )
        return 1

    async def _run_chat_turn_with_host_loop(self, room: ChatRoom) -> None:
        """Host-managed turn loop：循环推理+工具调用，直到 turn 完成或达到最大重试次数。"""
        turn_setup: AgentTurnSetup = self.driver.turn_setup
        tools: list[llmApiUtil.OpenAITool] = self.tool_registry.export_openai_tools()
        max_retries = max(1, turn_setup.max_retries)
        for _ in range(max_retries):
            turn_done = await self._run_until_reply(room, tools=tools)
            if turn_done:
                return
            if len(turn_setup.hint_prompt) > 0:
                await self._history.append_history_message(
                    llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiLLMApiRole.USER, turn_setup.hint_prompt),
                    stage=AgentHistoryStage.INPUT,
                )

    async def _resume_chat_turn_with_host_loop(self, room: ChatRoom) -> None:
        """续跑 host-managed turn loop：根据最后一条 history item 的阶段和状态，从断点处恢复执行。"""
        tools: list[llmApiUtil.OpenAITool] = self.tool_registry.export_openai_tools()
        turn_start_idx = self._history.get_unfinished_turn_start_index()
        if turn_start_idx is None:
            await self._run_chat_turn_with_host_loop(room)
            return
        last_item = self._history.last()
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
            tool_call = self._history.find_tool_call_by_id(tool_call_id, start_idx=turn_start_idx)
            if tool_call is None:
                raise RuntimeError(f"resume tool call not found: agent_id={self.gt_agent.id}, tool_call_id={tool_call_id}")
            turn_done = await self._dispatch_tool_calls(
                room,
                [tool_call],
                reuse_history_items={tool_call_id: last_item},
            )
            if turn_done:
                return

        else:
            last_assistant = self._history.get_last_assistant_message(start_idx=turn_start_idx)
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
        for _ in range(self.max_function_calls):
            assistant_message: llmApiUtil.OpenAIMessage = await self._infer(tools)
            tool_calls = assistant_message.tool_calls or []
            if len(tool_calls) == 0:
                return False

            turn_done = await self._dispatch_tool_calls(room, tool_calls)
            if turn_done:
                return True

        logger.warning(f"达到最大函数调用次数: agent_id={self.gt_agent.id}, max={self.max_function_calls}")
        return False

    # ─── AgentDriverHost 协议方法 ──────────────────────────

    def _resolve_compact_config(self) -> tuple[str, configUtil.LlmServiceConfig, int]:
        """获取 compact 相关配置：(resolved_model, llm_config, threshold)。"""
        llm_config = configUtil.get_app_config().setting.current_llm_service
        resolved_model = self.gt_agent.model or llm_config.model
        threshold = compactPolicy.calc_compact_trigger_tokens(resolved_model, llm_config)
        return resolved_model, llm_config, threshold

    async def _infer(
        self,
        tools: Optional[list[llmApiUtil.OpenAITool]],
        *,
        resume_item: GtAgentHistory | None = None,
        _skip_compact: bool = False,
    ) -> llmApiUtil.OpenAIMessage:
        """执行一次 LLM 推理，集成 token 预算 pre-check / post-check / overflow retry。

        若 resume_item 不为 None，则为续跑（复用已有 history item，跳过最后一条消息）。
        若 _skip_compact 为 True，跳过所有 compact 检查（避免 compact 推理时递归）。
        """
        history = self._history
        if resume_item is None:
            history.assert_infer_ready(f"agent_id={self.gt_agent.id}")

        ctx_tools: list[llmApiUtil.OpenAITool] | None = None
        if tools is not None and len(tools) > 0:
            ctx_tools = tools

        # ── 构造消息 + Pre-check ──
        messages = history.build_infer_messages()
        if resume_item is not None:
            messages = messages[:-1]
        resolved_model, _, _ = self._resolve_compact_config()
        estimated_tokens = compactPolicy.estimate_tokens(resolved_model, messages, self.system_prompt)

        pre_check_triggered = False
        if not _skip_compact:
            messages, estimated_tokens, pre_check_triggered = await self._pre_check_compact(
                resume_item, messages, estimated_tokens,
            )

        # ── 发起 LLM 请求 ──
        ctx = GtCoreAgentDialogContext(
            system_prompt=self.system_prompt, messages=messages, tools=ctx_tools,
        )
        history_item = resume_item or await history.append_stage_init(stage=AgentHistoryStage.INFER)
        infer_result: llmService.InferResult = await llmService.infer(self.gt_agent.model, ctx)

        # ── 处理失败 ──
        if infer_result.ok is False or infer_result.response is None:
            infer_result = await self._handle_infer_failure(
                infer_result, history_item, estimated_tokens, resume_item, ctx_tools,
                _skip_compact=_skip_compact, pre_check_triggered=pre_check_triggered,
            )

        # ── Post-check ──
        if not _skip_compact:
            assistant_message, usage, post_check_triggered = await self._post_check_compact(
                history_item, estimated_tokens, infer_result, ctx_tools,
            )
        else:
            usage = infer_result.usage
            assistant_message = infer_result.response.choices[0].message
            post_check_triggered = False

        # ── 记录 usage 并 finalize ──
        usage_json = compactPolicy.build_usage_payload(
            estimated_prompt_tokens=estimated_tokens,
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
            total_tokens=usage.total_tokens if usage else None,
            pre_check_triggered=pre_check_triggered,
            post_check_triggered=post_check_triggered,
        )
        await history.finalize_history_item(
            history_id=history_item.id,
            message=assistant_message,
            status=AgentHistoryStatus.SUCCESS,
            usage_json=usage_json,
        )
        return assistant_message

    async def _pre_check_compact(
        self,
        resume_item: GtAgentHistory | None,
        messages: list[llmApiUtil.OpenAIMessage],
        estimated_tokens: int,
    ) -> tuple[list[llmApiUtil.OpenAIMessage], int, bool]:
        """Pre-check：若估算 token 超阈值则执行 compact。

        Returns: (messages, estimated_tokens, pre_check_triggered)
        """
        resolved_model, _, threshold = self._resolve_compact_config()
        if not compactPolicy.should_trigger_pre_check(estimated_tokens, threshold):
            return messages, estimated_tokens, False

        logger.info(
            "pre-check compact 触发: %s(agent_id=%d), estimated=%d, trigger=%d",
            self.gt_agent.name, self.gt_agent.id, estimated_tokens, threshold,
        )
        await self._execute_compact()
        messages = self._history.build_infer_messages()
        if resume_item is not None:
            messages = messages[:-1]
        estimated_tokens = compactPolicy.estimate_tokens(resolved_model, messages, self.system_prompt)
        if compactPolicy.should_fail_after_compact(estimated_tokens, threshold):
            raise RuntimeError(
                f"compact 后仍超限: agent_id={self.gt_agent.id}, "
                f"estimated={estimated_tokens}, trigger={threshold}"
            )
        return messages, estimated_tokens, True

    async def _handle_infer_failure(
        self,
        infer_result: llmService.InferResult,
        history_item: GtAgentHistory,
        estimated_tokens: int,
        resume_item: GtAgentHistory | None,
        ctx_tools: list[llmApiUtil.OpenAITool] | None,
        *,
        _skip_compact: bool,
        pre_check_triggered: bool,
    ) -> llmService.InferResult:
        """处理推理失败：若为 overflow 且可重试则 compact + retry，否则 finalize 并 raise。"""
        history = self._history
        error = infer_result.error
        resolved_model, _, threshold = self._resolve_compact_config()

        # 可以尝试 overflow compact + retry?
        if (
            not _skip_compact
            and error is not None
            and compactPolicy.is_context_overflow_error(error)
            and not pre_check_triggered
        ):
            logger.info(
                "overflow retry 触发: %s(agent_id=%d), error=%s",
                self.gt_agent.name, self.gt_agent.id, infer_result.error_message,
            )
            await self._execute_compact()
            messages = history.build_infer_messages()
            if resume_item is not None:
                messages = messages[:-1]
            retry_estimated = compactPolicy.estimate_tokens(resolved_model, messages, self.system_prompt)
            if compactPolicy.should_fail_after_compact(retry_estimated, threshold):
                usage_json = compactPolicy.build_usage_payload(
                    estimated_prompt_tokens=retry_estimated, overflow_retry=True,
                )
                await history.finalize_history_item(
                    history_id=history_item.id, message=None,
                    status=AgentHistoryStatus.FAILED,
                    error_message="compact 后仍超限",
                    usage_json=usage_json,
                )
                raise RuntimeError(
                    f"overflow compact 后仍超限: agent_id={self.gt_agent.id}"
                ) from error

            ctx = GtCoreAgentDialogContext(
                system_prompt=self.system_prompt, messages=messages, tools=ctx_tools,
            )
            infer_result = await llmService.infer(self.gt_agent.model, ctx)
            if infer_result.ok is False or infer_result.response is None:
                error_message = infer_result.error_message or "overflow retry failed"
                usage_json = compactPolicy.build_usage_payload(
                    estimated_prompt_tokens=retry_estimated, overflow_retry=True,
                )
                await history.finalize_history_item(
                    history_id=history_item.id, message=None,
                    status=AgentHistoryStatus.FAILED,
                    error_message=error_message,
                    usage_json=usage_json,
                )
                raise RuntimeError(
                    f"LLM 推理失败(overflow retry): agent_id={self.gt_agent.id}, error={error_message}"
                ) from infer_result.error
            return infer_result

        # 不可重试的失败
        error_message = infer_result.error_message or "unknown inference error"
        usage_json = compactPolicy.build_usage_payload(estimated_prompt_tokens=estimated_tokens)
        await history.finalize_history_item(
            history_id=history_item.id, message=None,
            status=AgentHistoryStatus.FAILED,
            error_message=error_message,
            usage_json=usage_json,
        )
        raise RuntimeError(
            f"LLM 推理失败: agent_id={self.gt_agent.id}, error={error_message}"
        ) from infer_result.error

    async def _post_check_compact(
        self,
        history_item: GtAgentHistory,
        estimated_tokens: int,
        infer_result: llmService.InferResult,
        ctx_tools: list[llmApiUtil.OpenAITool] | None,
    ) -> tuple[llmApiUtil.OpenAIMessage, llmApiUtil.OpenAIUsage | None, bool]:
        """Post-check：若实际 prompt token 超阈值且有 tool_calls，compact + re-infer。

        Returns: (assistant_message, usage, post_check_triggered)
        """
        resolved_model, _, threshold = self._resolve_compact_config()
        usage = infer_result.usage
        actual_prompt_tokens = usage.prompt_tokens if usage else None
        assistant_message = infer_result.response.choices[0].message
        has_tool_calls = bool(assistant_message.tool_calls)

        if (
            actual_prompt_tokens is None
            or not compactPolicy.should_trigger_post_check(actual_prompt_tokens, threshold, has_tool_calls)
        ):
            return assistant_message, usage, False

        logger.info(
            "post-check compact 触发: %s(agent_id=%d), actual_prompt=%d, trigger=%d",
            self.gt_agent.name, self.gt_agent.id, actual_prompt_tokens, threshold,
        )
        await self._execute_compact()
        messages = self._history.build_infer_messages()
        re_estimated = compactPolicy.estimate_tokens(resolved_model, messages, self.system_prompt)
        if compactPolicy.should_fail_after_compact(re_estimated, threshold):
            usage_json = compactPolicy.build_usage_payload(
                estimated_prompt_tokens=re_estimated,
                prompt_tokens=actual_prompt_tokens,
                completion_tokens=usage.completion_tokens if usage else None,
                total_tokens=usage.total_tokens if usage else None,
                post_check_triggered=True,
            )
            await self._history.finalize_history_item(
                history_id=history_item.id, message=None,
                status=AgentHistoryStatus.FAILED,
                error_message="post-check compact 后仍超限",
                usage_json=usage_json,
            )
            raise RuntimeError(
                f"post-check compact 后仍超限: agent_id={self.gt_agent.id}"
            )

        # re-infer
        ctx = GtCoreAgentDialogContext(
            system_prompt=self.system_prompt, messages=messages, tools=ctx_tools,
        )
        infer_result = await llmService.infer(self.gt_agent.model, ctx)
        if infer_result.ok is False or infer_result.response is None:
            error_message = infer_result.error_message or "post-check re-infer failed"
            await self._history.finalize_history_item(
                history_id=history_item.id, message=None,
                status=AgentHistoryStatus.FAILED,
                error_message=error_message,
            )
            raise RuntimeError(
                f"LLM 推理失败(post-check re-infer): agent_id={self.gt_agent.id}, error={error_message}"
            ) from infer_result.error
        return infer_result.response.choices[0].message, infer_result.usage, True

    async def _execute_compact(self) -> None:
        """执行一次 compact：插入带 COMPACT_CMD 的压缩指令 → _infer 推理 → 内存裁剪。

        流程：
        1. 追加压缩指令（user 消息，直接带 COMPACT_CMD tag）
        2. _infer(_skip_compact=True) — build_infer_messages 检测到 compact 进行中，
           返回完整历史（含指令），LLM 生成摘要
        3. 内存裁剪
        """
        _, llm_config, _ = self._resolve_compact_config()
        source_messages = self._history.build_compact_source_messages()
        if not source_messages:
            logger.warning("compact 跳过：无可压缩消息, agent_id=%d", self.gt_agent.id)
            return

        compact_instruction = compactPolicy.build_compact_instruction(
            max_tokens=llm_config.compact_summary_max_tokens,
        )
        instruction_msg = llmApiUtil.OpenAIMessage.text(
            llmApiUtil.OpenaiLLMApiRole.USER, compact_instruction,
        )
        await self._history.append_history_message(
            instruction_msg,
            stage=AgentHistoryStage.INPUT,
            status=AgentHistoryStatus.SUCCESS,
            tags=[AgentHistoryTag.COMPACT_CMD],
        )

        # 通过 _infer 走正常推理路径（不带工具，跳过 compact 检查）
        await self._infer(tools=None, _skip_compact=True)

        # 内存裁剪：只保留 COMPACT_CMD 及之后的消息
        self._history.drop_messages_before_latest_compact()
        logger.info(
            "compact 完成: %s(agent_id=%d)",
            self.gt_agent.name, self.gt_agent.id,
        )

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
        logger.info(
            "检测到工具调用: %s(agent_id=%d), tools=%s",
            self.gt_agent.name, self.gt_agent.id, tool_names,
        )
        context: ToolCallContext = ToolCallContext(
            agent_name=self.gt_agent.name,
            team_id=room.team_id,
            chat_room=room,
        )
        turn_done = False
        for tool_call in tool_calls:
            tool_call_id = str(tool_call.id or "")
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
        assert tool_call.id, "tool_call.id should not be empty"
        history_item = existing_item or await self._history.append_stage_init(
            stage=AgentHistoryStage.TOOL_RESULT,
            tool_call_id=str(tool_call.id),
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

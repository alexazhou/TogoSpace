"""AgentTurnRunner: Turn 内部逻辑 — 消息同步、host loop、推理、工具调用编排。

同时实现 AgentDriverHost 协议，作为 Driver 的宿主。
"""
from __future__ import annotations

import logging
from typing import List

from constants import (
    AgentHistoryStatus, AgentHistoryTag,
    DriverType, OpenaiApiRole, RoomState,
)
from model.coreModel.gtCoreChatModel import GtCoreAgentDialogContext, GtCoreRoomMessage
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtAgentHistory import GtAgentHistory
from model.dbModel.gtAgentTask import GtAgentTask
from model.dbModel.historyUsage import CompactStage, HistoryUsage
from service import llmService, roomService
from service.agentService.agentHistoryStore import AgentHistoryStore
from service.agentService import compact, promptBuilder
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
                if not self._history.has_active_turn():
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
        await self._history.append_history_message(GtAgentHistory.build(
            llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, turn_prompt),
            tags=[AgentHistoryTag.ROOM_TURN_BEGIN],
        ))
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
                    await self._history.append_history_message(GtAgentHistory.build(
                        llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, turn_setup.hint_prompt),
                    ))
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

        role, status = last_item.role, last_item.status

        # TOOL 成功且有待处理工具 → 插入 INIT record，下一轮执行
        if role == OpenaiApiRole.TOOL and status == AgentHistoryStatus.SUCCESS:
            pending_tc = self._history.get_first_pending_tool_call()
            if pending_tc is not None:
                await self._history.append_history_init_item(
                    role=OpenaiApiRole.TOOL,
                    tool_call_id=pending_tc.id,
                )
                return "continue"
            output_item = await self._history.append_history_init_item(role=OpenaiApiRole.ASSISTANT)
            assistant_message = await self._infer_to_item(output_item, tools)
            tool_calls = assistant_message.tool_calls or []
            return "no_tool_calls" if len(tool_calls) == 0 else "continue"

        # USER 或 SYSTEM → 推理
        if role in (OpenaiApiRole.USER, OpenaiApiRole.SYSTEM):
            output_item = await self._history.append_history_init_item(role=OpenaiApiRole.ASSISTANT)
            assistant_message = await self._infer_to_item(output_item, tools)
            tool_calls = assistant_message.tool_calls or []
            return "no_tool_calls" if len(tool_calls) == 0 else "continue"
        if role == OpenaiApiRole.ASSISTANT and status in (AgentHistoryStatus.INIT, AgentHistoryStatus.FAILED):
            assistant_message = await self._infer_to_item(last_item, tools)
            tool_calls = assistant_message.tool_calls or []
            return "no_tool_calls" if len(tool_calls) == 0 else "continue"

        # ASSISTANT 成功 → 执行工具
        if role == OpenaiApiRole.ASSISTANT and status == AgentHistoryStatus.SUCCESS:
            first_tc = (last_item.tool_calls or [None])[0]
            if first_tc is None:
                return "no_tool_calls"
            output_item = await self._history.append_history_init_item(
                role=OpenaiApiRole.TOOL,
                tool_call_id=first_tc.id,
            )
            return await self._run_tool_to_item(first_tc, output_item, room)

        # TOOL 待处理 → 恢复执行
        if role == OpenaiApiRole.TOOL and status == AgentHistoryStatus.INIT:
            tool_call = self._history.find_tool_call_by_id(last_item.tool_call_id)
            if tool_call is None:
                raise RuntimeError(f"工具调用不存在: agent_id={self.gt_agent.id}, tool_call_id={last_item.tool_call_id}")
            return await self._run_tool_to_item(tool_call, last_item, room)

        raise RuntimeError(f"无法推进: agent_id={self.gt_agent.id}, role={role}, status={status}")

    async def _infer_to_item(
        self,
        output_item: GtAgentHistory,
        tools: list[llmApiUtil.OpenAITool],
    ) -> llmApiUtil.OpenAIMessage:
        """执行推理，结果写入 output_item。"""
        history = self._history
        assert history.is_infer_ready(), (
            f"[agent_id={self.gt_agent.id}] infer 前历史状态非法，"
            f"末尾角色: {history._last_role() or 'empty'}"
        )

        resolved_model, _, trigger_tokens, hard_limit_tokens = self._resolve_compact_config()
        estimated_tokens = 0
        compact_stage: CompactStage = "none"
        overflow_retry = False
        usage: llmApiUtil.OpenAIUsage | None = None
        assistant_committed = False

        try:
            messages = history.build_infer_messages()
            estimated_tokens = compact.estimate_tokens(resolved_model, messages, self.system_prompt)

            messages, estimated_tokens, pre_compact_triggered = await self._check_compact(
                messages,
                trigger_prompt_tokens=estimated_tokens,
                estimated_tokens=estimated_tokens,
                check_stage="pre-check",
            )
            if pre_compact_triggered:
                compact_stage = "pre"

            ctx = GtCoreAgentDialogContext(system_prompt=self.system_prompt, messages=messages, tools=tools)
            infer_result: llmService.InferResult = await llmService.infer(self.gt_agent.model, ctx)

            # overflow retry
            if infer_result.ok is False or infer_result.response is None:
                error = infer_result.error
                if (
                    error is not None
                    and compact.is_context_overflow_error(error)
                    and compact_stage != "pre"
                ):
                    logger.info(f"overflow retry 触发: {self.gt_agent.name}(agent_id={self.gt_agent.id}), error={infer_result.error_message}")
                    overflow_retry = True
                    compact_ok = await self._execute_compact()
                    if not compact_ok:
                        raise RuntimeError(f"overflow compact 失败: agent_id={self.gt_agent.id}") from error
                    messages = history.build_infer_messages()
                    estimated_tokens = compact.estimate_tokens(resolved_model, messages, self.system_prompt)
                    if estimated_tokens >= hard_limit_tokens:
                        raise RuntimeError(f"overflow compact 后仍超限: agent_id={self.gt_agent.id}") from error

                    ctx = GtCoreAgentDialogContext(system_prompt=self.system_prompt, messages=messages, tools=tools)
                    infer_result = await llmService.infer(self.gt_agent.model, ctx)

                if infer_result.ok is False or infer_result.response is None:
                    error_message = infer_result.error_message or "unknown inference error"
                    if overflow_retry:
                        raise RuntimeError(f"LLM 推理失败(overflow retry): agent_id={self.gt_agent.id}, error={error_message}") from infer_result.error
                    raise RuntimeError(f"LLM 推理失败: agent_id={self.gt_agent.id}, error={error_message}") from infer_result.error

            usage = infer_result.usage
            assistant_message = infer_result.response.choices[0].message
            usage_data = self._build_usage(
                estimated_prompt_tokens=estimated_tokens,
                prompt_tokens=usage.prompt_tokens if usage else None,
                completion_tokens=usage.completion_tokens if usage else None,
                total_tokens=usage.total_tokens if usage else None,
                compact_stage=compact_stage,
                overflow_retry=overflow_retry,
            )
            await history.finalize_history_item(
                history_id=output_item.id,
                message=assistant_message,
                status=AgentHistoryStatus.SUCCESS,
                usage=usage_data,
            )
            assistant_committed = True

            post_check_messages = history.build_infer_messages()
            _, _, post_check_triggered = await self._check_compact(
                post_check_messages,
                trigger_prompt_tokens=usage.prompt_tokens if usage and usage.prompt_tokens is not None else estimated_tokens,
                estimated_tokens=estimated_tokens,
                check_stage="post-check",
            )
            if post_check_triggered and compact_stage == "none":
                compact_stage = "post"
                await history.finalize_history_item(
                    history_id=output_item.id,
                    message=None,
                    status=AgentHistoryStatus.SUCCESS,
                    usage=self._build_usage(
                        estimated_prompt_tokens=estimated_tokens,
                        prompt_tokens=usage.prompt_tokens if usage else None,
                        completion_tokens=usage.completion_tokens if usage else None,
                        total_tokens=usage.total_tokens if usage else None,
                        compact_stage=compact_stage,
                        overflow_retry=overflow_retry,
                    ),
                )
            return assistant_message
        except Exception as e:
            if assistant_committed:
                if compact_stage == "none" and usage and usage.prompt_tokens is not None and usage.prompt_tokens >= trigger_tokens:
                    compact_stage = "post"
                    await history.finalize_history_item(
                        history_id=output_item.id,
                        message=None,
                        status=AgentHistoryStatus.SUCCESS,
                        usage=self._build_usage(
                            estimated_prompt_tokens=estimated_tokens,
                            prompt_tokens=usage.prompt_tokens,
                            completion_tokens=usage.completion_tokens,
                            total_tokens=usage.total_tokens,
                            compact_stage=compact_stage,
                            overflow_retry=overflow_retry,
                        ),
                    )
                raise

            usage_data = self._build_usage(
                estimated_prompt_tokens=estimated_tokens or None,
                prompt_tokens=usage.prompt_tokens if usage else None,
                completion_tokens=usage.completion_tokens if usage else None,
                total_tokens=usage.total_tokens if usage else None,
                compact_stage=compact_stage,
                overflow_retry=overflow_retry,
            )
            await history.finalize_history_item(
                history_id=output_item.id,
                message=None,
                status=AgentHistoryStatus.FAILED,
                error_message=str(e),
                usage=usage_data,
            )
            raise

    async def _run_tool_to_item(self, tool_call: llmApiUtil.OpenAIToolCall, output_item: GtAgentHistory, room: ChatRoom) -> str:
        """执行单个工具调用，结果写入 output_item。返回 'turn_done' 或 'continue'。"""
        context = ToolCallContext(
            agent_name=self.gt_agent.name,
            team_id=room.team_id,
            chat_room=room,
        )
        exec_result = await self.tool_registry.execute_tool_call(tool_call, context)
        final_message = llmApiUtil.OpenAIMessage.tool_result(exec_result.tool_call_id, exec_result.result_json)
        await self._history.finalize_history_item(
            history_id=output_item.id,
            message=final_message,
            status=exec_result.status,
            error_message=exec_result.error_message,
            tags=exec_result.tags,
        )
        turn_done = exec_result.turn_finished and (
            exec_result.status == AgentHistoryStatus.SUCCESS or room.state == RoomState.INIT
        )
        return "turn_done" if turn_done else "continue"

    # ─── AgentDriverHost 协议方法 ──────────────────────────

    async def execute_pending_tools(self) -> None:
        """执行最后一条 assistant 消息中的所有 tool calls。

        AgentDriverHost 协议方法，通过 _current_room 获取房间上下文，
        由 run_chat_turn 在调用前设置。
        """
        room = self._current_room
        assert room is not None, "no current room context while executing tool"

        last_msg: llmApiUtil.OpenAIMessage | None = self._history.get_last_assistant_message()
        if last_msg is None or last_msg.tool_calls is None or len(last_msg.tool_calls) == 0:
            return

        for tool_call in last_msg.tool_calls:
            output_item = await self._history.append_history_init_item(
                role=OpenaiApiRole.TOOL,
                tool_call_id=tool_call.id,
            )
            await self._run_tool_to_item(tool_call, output_item, room)

    # ─── 内部辅助方法 ─────────────────────────────

    def _resolve_compact_config(self) -> tuple[str, configUtil.LlmServiceConfig, int, int]:
        """获取 compact 相关配置：(resolved_model, llm_config, trigger_tokens, hard_limit_tokens)。"""
        llm_config = configUtil.get_app_config().setting.current_llm_service
        resolved_model = self.gt_agent.model or llm_config.model
        trigger_tokens = compact.calc_compact_trigger_tokens(resolved_model, llm_config)
        hard_limit_tokens = compact.calc_hard_limit_tokens(resolved_model, llm_config)
        return resolved_model, llm_config, trigger_tokens, hard_limit_tokens

    @staticmethod
    def _build_usage(
        *,
        estimated_prompt_tokens: int | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
        compact_stage: CompactStage = "none",
        overflow_retry: bool = False,
    ) -> HistoryUsage:
        return HistoryUsage(
            estimated_prompt_tokens=estimated_prompt_tokens,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            compact_stage=compact_stage,
            overflow_retry=overflow_retry,
        )

    async def _check_compact(
        self,
        messages: list[llmApiUtil.OpenAIMessage],
        *,
        trigger_prompt_tokens: int,
        estimated_tokens: int,
        check_stage: str,
    ) -> tuple[list[llmApiUtil.OpenAIMessage], int, bool]:
        """在指定检查阶段检测 prompt token，必要时执行 compact。

        Returns: (messages, estimated_tokens, compact_triggered)
        """
        resolved_model, _, trigger_tokens, hard_limit_tokens = self._resolve_compact_config()
        if trigger_prompt_tokens < trigger_tokens:
            return messages, estimated_tokens, False

        logger.info(
            f"{check_stage} compact 触发: {self.gt_agent.name}(agent_id={self.gt_agent.id}), "
            f"prompt_tokens={trigger_prompt_tokens}, trigger={trigger_tokens}"
        )
        compact_ok = await self._execute_compact()
        if not compact_ok:
            raise RuntimeError(f"{check_stage} compact 失败: agent_id={self.gt_agent.id}")

        messages = self._history.build_infer_messages()
        estimated_tokens = compact.estimate_tokens(resolved_model, messages, self.system_prompt)
        if estimated_tokens >= hard_limit_tokens:
            raise RuntimeError(
                f"{check_stage} compact 后仍超限: agent_id={self.gt_agent.id}, "
                f"estimated={estimated_tokens}, hard_limit={hard_limit_tokens}"
            )

        return messages, estimated_tokens, True

    async def _execute_compact(self) -> bool:
        """执行一次 compact：生成摘要 → 插入 COMPACT_SUMMARY → 内存裁剪。返回是否成功。"""
        _, llm_config, _, _ = self._resolve_compact_config()
        compact_plan = self._history.build_compact_plan()
        if compact_plan is None:
            logger.warning("compact 跳过：无可压缩消息, agent_id=%d", self.gt_agent.id)
            return False

        summary_text = await compact.compact_messages(
            messages=compact_plan.source_messages,
            system_prompt=self.system_prompt,
            model=self.gt_agent.model,
            max_tokens=llm_config.compact_summary_max_tokens,
        )
        if summary_text is None:
            logger.warning("compact 失败：LLM 返回无效, agent_id=%d", self.gt_agent.id)
            return False

        await self._history.insert_compact_summary(
            llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, summary_text),
            seq=compact_plan.insert_seq,
        )
        return True

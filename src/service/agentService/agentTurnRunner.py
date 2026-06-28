"""AgentTurnRunner: Turn 内部逻辑 — 消息同步、host loop、推理、工具调用编排。

同时实现 AgentDriverHost 协议，作为 Driver 的宿主。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import List

from constants import (
    AgentActivityStatus, AgentActivityType,
    AgentHistoryStatus, AgentHistoryTag,
    AgentTaskType, DriverType, OpenaiApiRole, RoomState, TurnStepResult,
)
from model.coreModel.gtCoreChatModel import GtCoreAgentDialogContext
from model.dbModel.gtRoomMessage import GtRoomMessage
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtAgentHistory import GtAgentHistory
from model.dbModel.gtScheculeTask import GtScheculeTask
from model.dbModel.historyUsage import CompactStage, HistoryUsage
from service import agentActivityService, llmService, roomService
from service.agentActivityService import AgentActivityMeta
from service.agentService.agentHistoryStore import AgentHistoryStore
from service.agentService import compact, promptBuilder
from service.agentService.driver import AgentDriverConfig, AgentTurnSetup
from service.agentService.driver.factory import build_agent_driver
from service.agentService.toolRegistry import AgentToolRegistry, RegisteredTool, ToolExecutionResult
from service.roomService import ChatRoom, ToolCallContext
from util import configUtil, llmApiUtil
from util.configTypes import LlmModelConfig
from util.assertUtil import assertNotNull
from dal.db import gtAgentTaskManager

logger = logging.getLogger(__name__)


def _detect_json_tool_call_in_content(content: str | None) -> bool:
    """检测 LLM 是否将工具调用以 JSON 对象形式写入了 content 字段（而非 tool_calls）。"""
    if not content:
        return False
    stripped = content.strip()
    if not (stripped.startswith("{") and stripped.endswith("}")):
        return False
    try:
        data = json.loads(stripped)
        return isinstance(data, dict)
    except (json.JSONDecodeError, ValueError):
        return False


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
        driver_config: AgentDriverConfig | None = None,
    ):
        self.gt_agent: GtAgent = gt_agent
        self.system_prompt: str = system_prompt
        self.agent_workdir: str = agent_workdir
        self._history: AgentHistoryStore = AgentHistoryStore(gt_agent.id or 0)
        self.tool_registry: AgentToolRegistry = AgentToolRegistry()
        self.driver = build_agent_driver(self, driver_config or AgentDriverConfig(driver_type=DriverType.NATIVE))
        self._current_room: ChatRoom | None = None
        self._current_task: GtScheculeTask | None = None

    def _base_metadata(self, **extra) -> AgentActivityMeta:
        """构建活动记录 metadata，自动附加 task_room_id（本次 turn 所在的任务房间）。"""
        meta = AgentActivityMeta(
            task_room_id=self._current_room.room_id if self._current_room is not None else None,
            **extra,
        )
        return meta

    def _extract_tool_command(self, tool_call: llmApiUtil.OpenAIToolCall) -> str | None:
        if tool_call.function_name != "execute_bash":
            return None
        try:
            parsed_args = json.loads(tool_call.function_args)
        except Exception:
            return None
        command = parsed_args.get("command")
        if not isinstance(command, str):
            return None
        command = command.strip()
        return command if len(command) > 0 else None

    def _extract_tool_arguments(self, tool_call: llmApiUtil.OpenAIToolCall):
        raw_args = tool_call.function_args.strip()
        if not raw_args:
            return None
        try:
            return json.loads(raw_args)
        except Exception:
            return raw_args

    async def _finish_activity(
        self,
        activity_id: int | None,
        *,
        status: AgentActivityStatus,
        detail: str | None = None,
        error_message: str | None = None,
        metadata_patch: AgentActivityMeta | None = None,
    ) -> None:
        """更新 activity 终态。"""
        if activity_id is None:
            return
        await agentActivityService.update_activity_progress(activity_id, status=status, detail=detail, error_message=error_message, metadata_patch=metadata_patch)

    # ─── Turn 运行方法 ──────────────────────────────────────

    async def handle_cancel_turn(self) -> None:
        """人工取消当前 turn 的收尾逻辑：driver 清理 → history 清理。"""
        await self.driver.cancel_turn()
        if self._current_room is not None:
            self._current_room.cancel_current_turn()
        await self._history.finalize_cancel_turn()
        await agentActivityService.fail_started_activities(self.gt_agent.id, error_message="cancelled by user")

    async def run_task_turn(self, task: GtScheculeTask) -> None:
        """执行一个完整 chat turn：同步消息 → 推理 → 工具调用循环。

        支持两种模式：
        - ROOM_MESSAGE：从房间同步消息后运行 turn loop，完成后刷新房间消息队列。
        - TODO_TASK：向 history 注入任务通知 prompt 后运行 turn loop，无房间依赖。
        若存在未完成 turn，则走续跑路径。
        """
        is_todo_task = task.task_type == AgentTaskType.TODO_TASK

        if is_todo_task:
            agent_task_id = task.task_data.get("agent_task_id")
            assertNotNull(agent_task_id, error_message=f"task 缺少 agent_task_id, agent_id={self.gt_agent.id}, task_id={task.id}")
            agent_task = await gtAgentTaskManager.get_task(agent_task_id)
            assertNotNull(agent_task, error_message=f"agent_task_id={agent_task_id} 不存在, agent_id={self.gt_agent.id}")
            logger.info(f"协作任务 turn 开始: {self.gt_agent.name}(agent_id={self.gt_agent.id}), agent_task_id={agent_task_id}, title={agent_task.title!r}")
            room = None
        else:
            room_id = task.task_data.get("room_id")
            assertNotNull(room_id, error_message=f"task 缺少 room_id, agent_id={self.gt_agent.id}, task_id={task.id}")
            room = roomService.get_room(room_id)
            assertNotNull(room, error_message=f"room_id={room_id} 不存在, agent_id={self.gt_agent.id}")
            assert room.state != RoomState.INIT, (
                f"Agent 不应在 INIT 状态下收到任务: agent_id={self.gt_agent.id}, room={room.name}, state={room.state}"
            )

        self._current_room = room
        self._current_task = task
        try:
            if self.driver.host_managed_turn_loop:
                assert self.driver.started is True, f"driver 尚未启动: agent_id={self.gt_agent.id}"

                if not self._history.has_active_turn():
                    if is_todo_task:
                        task_prompt = promptBuilder.build_todo_task_turn_prompt(
                            title=agent_task.title,
                            description=agent_task.description,
                            status_value=agent_task.status.value,
                        )
                        await self._history.append_history_message(GtAgentHistory.build(
                            llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, task_prompt),
                            tags=[AgentHistoryTag.ROOM_TURN_BEGIN],
                        ))
                        await agentActivityService.add_activity(
                            gt_agent=self.gt_agent,
                            activity_type=AgentActivityType.TASK_RECEIVED,
                            status=AgentActivityStatus.SUCCEEDED,
                            detail=agent_task.title,
                            metadata=self._base_metadata(),
                        )
                    else:
                        synced_count = await self.pull_room_messages_to_history(room)
                        if synced_count == 0:
                            logger.info(f"无新消息，自动跳过本轮: {self.gt_agent.name}(agent_id={self.gt_agent.id}), room={room.name}")
                            await room.handle_finish_request(self.gt_agent.id)
                            await room.flush_queued_messages()
                            return

                await self._run_turn_loop(room)
                if room is not None:
                    await room.flush_queued_messages()

            else:
                synced_count = 1 if is_todo_task else await self.pull_room_messages_to_history(room)
                await self.driver.run_task_turn(task, synced_count)

        except asyncio.CancelledError:
            if not is_todo_task and room is not None:
                room.cancel_current_turn()
            raise
        finally:
            self._current_room = None
            self._current_task = None

    async def pull_room_messages_to_history(self, room: ChatRoom) -> int:
        """从房间拉取未读消息并追加到 history。返回追加的消息条目数（0 或 1）。"""
        new_msgs: List[GtRoomMessage] = await room.get_unread_messages(self.gt_agent.id)

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
        other_msgs = [m for m in new_msgs if m.sender_id != self.gt_agent.id]
        meta = self._base_metadata(messages=[{"sender": m.sender_display_name, "content": m.content} for m in other_msgs])
        await agentActivityService.add_activity(
            gt_agent=self.gt_agent,
            activity_type=AgentActivityType.MESSAGE_RECEIVED,
            status=AgentActivityStatus.SUCCEEDED,
            metadata=meta,
        )
        return 1

    async def _inject_immediate_messages(self, room: ChatRoom) -> None:
        """在安全边界将待注入的 immediately 消息移入主消息列表，并通知 Agent。"""
        await room.flush_pending_immediate_messages()

        new_msgs: List[GtRoomMessage] = await room.get_unread_messages(self.gt_agent.id)
        others = [m for m in new_msgs if m.sender_id != self.gt_agent.id]
        if not others:
            logger.debug(
                "即时插入检查：无新消息: agent=%s(agent_id=%d), room=%s",
                self.gt_agent.name, self.gt_agent.id, room.name,
            )
            return

        update_prompt = promptBuilder.build_turn_update_prompt(
            room.name, new_msgs, self.gt_agent.id
        )
        await self._history.append_history_message(GtAgentHistory.build(
            llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, update_prompt),
        ))
        logger.info(
            "即时插入新消息: agent=%s(agent_id=%d), room=%s, msgs=%d",
            self.gt_agent.name, self.gt_agent.id, room.name, len(others),
        )
        meta = self._base_metadata(messages=[{"sender": m.sender_display_name, "content": m.content} for m in others])
        await agentActivityService.add_activity(
            gt_agent=self.gt_agent,
            activity_type=AgentActivityType.MESSAGE_RECEIVED,
            status=AgentActivityStatus.SUCCEEDED,
            metadata=meta,
        )

    async def _run_turn_loop(self, room: ChatRoom | None) -> None:
        """基于 history 状态推进的统一循环。room 为 None 时跳过房间相关操作（协作任务模式）。"""
        tools = self.tool_registry.export_openai_tools()
        turn_setup: AgentTurnSetup = self.driver.turn_setup
        failed_action_count = 0
        next_tool_choice: str | None = None

        while True:
            # 检查 operator 私聊控制房间是否有待即时插入或未读的消息
            if self._history.is_safe_for_immediate_insert():
                ctrl_room = await roomService.get_control_room_for_agent(self.gt_agent.team_id, self.gt_agent.id)
                if ctrl_room is not None and (
                    ctrl_room.has_pending_immediate_messages(self.gt_agent.id) or
                    ctrl_room.has_unread_messages(self.gt_agent.id)
                ):
                    await self._inject_immediate_messages(ctrl_room)

            result = await self._advance_step(room, tools, tool_choice=next_tool_choice)
            next_tool_choice = None

            if result == TurnStepResult.TURN_DONE:
                return

            if result == TurnStepResult.TOOL_EXECUTE_SUCCESS:
                failed_action_count = 0
                continue

            if result == TurnStepResult.LLM_OUTPUT_TOOL_CALLS:
                # 推理成功生成了 tool_calls，但尚未执行，不重置计数器
                continue

            if result == TurnStepResult.LLM_OUTPUT_NO_ACTION:
                failed_action_count += 1
                failure_kind = "no_action"
                if len(turn_setup.hint_prompt) > 0 and failed_action_count <= turn_setup.max_retries:
                    logger.warning(f"检测到失败行动，准备重试: agent_id={self.gt_agent.id}, kind={failure_kind}, retry={failed_action_count}/{turn_setup.max_retries}")
                    await self._history.append_history_message(GtAgentHistory.build(
                        llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, turn_setup.hint_prompt),
                    ))
                    continue
                raise RuntimeError(
                    f"达到失败行动重试上限仍未完成行动: agent_id={self.gt_agent.id}, "
                    f"kind={failure_kind}, failed_actions={failed_action_count}, max_retries={turn_setup.max_retries}"
                )

            if result == TurnStepResult.LLM_OUTPUT_ERROR:
                failed_action_count += 1
                hint = turn_setup.hint_prompt_error_action or turn_setup.hint_prompt
                if len(hint) > 0 and failed_action_count <= turn_setup.max_retries:
                    logger.warning(f"检测到 JSON 写入 content 异常，准备重试: agent_id={self.gt_agent.id}, retry={failed_action_count}/{turn_setup.max_retries}")
                    await self._history.append_history_message(GtAgentHistory.build(
                        llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, hint),
                    ))
                    next_tool_choice = "required"
                    continue
                raise RuntimeError(
                    f"达到 ERROR_ACTION 重试上限: agent_id={self.gt_agent.id}, "
                    f"failed_actions={failed_action_count}, max_retries={turn_setup.max_retries}"
                )

            if result == TurnStepResult.TOOL_EXECUTE_FAILED_FINISH:
                failed_action_count += 1
                if failed_action_count <= turn_setup.max_retries:
                    logger.warning(f"finish 类工具执行失败，准备重试: agent_id={self.gt_agent.id}, retry={failed_action_count}/{turn_setup.max_retries}")
                    # 不注入 hint：tool_result 已包含具体失败原因，LLM 可直接据此调整行为
                    next_tool_choice = "required"
                    continue
                raise RuntimeError(
                    f"达到 finish 失败重试上限: agent_id={self.gt_agent.id}, "
                    f"failed_actions={failed_action_count}, max_retries={turn_setup.max_retries}"
                )

    async def _advance_step(self, room: ChatRoom | None, tools: list[llmApiUtil.OpenAITool], tool_choice: str | None = None) -> TurnStepResult:
        """根据当前 history 状态推进一个 step。

        返回:
            `TURN_DONE`：finish 类工具执行成功，turn 已结束
            `TOOL_EXECUTE_SUCCESS`：非 finish 工具执行成功，turn 继续推进
            `TOOL_EXECUTE_FAILED_FINISH`：finish 类工具执行失败
            `LLM_OUTPUT_NO_ACTION`：模型输出纯文本，无工具调用
            `LLM_OUTPUT_ERROR`：模型输出格式异常（如将 tool call 写入 content 字段）
            `LLM_OUTPUT_TOOL_CALLS`：模型生成了 tool_calls，待执行
        """
        last_item = self._history.last()
        if last_item is None:
            raise RuntimeError(f"history 为空，无法推进: agent_id={self.gt_agent.id}")

        role, status = last_item.role, last_item.status

        if role == OpenaiApiRole.ASSISTANT:
            if status == AgentHistoryStatus.SUCCESS:
                first_tc = (last_item.tool_calls or [None])[0]
                if first_tc is None:
                    return TurnStepResult.LLM_OUTPUT_NO_ACTION
                output_item = await self._history.append_history_init_item(
                    role=OpenaiApiRole.TOOL,
                    tool_call_id=first_tc.id,
                )
                return await self._run_tool_to_item(first_tc, output_item, room)
            elif status in (AgentHistoryStatus.INIT, AgentHistoryStatus.FAILED):
                return await self._infer_and_classify(last_item, tools, tool_choice=tool_choice)
            else:
                raise RuntimeError(f"无法推进: agent_id={self.gt_agent.id}, role={role}, status={status}")

        elif role == OpenaiApiRole.TOOL:
            if status == AgentHistoryStatus.INIT:
                tool_call = self._history.find_tool_call_by_id(last_item.tool_call_id)
                if tool_call is None:
                    raise RuntimeError(f"工具调用不存在: agent_id={self.gt_agent.id}, tool_call_id={last_item.tool_call_id}")
                return await self._run_tool_to_item(tool_call, last_item, room)
            elif status == AgentHistoryStatus.SUCCESS:
                pending_tc = self._history.get_first_pending_tool_call()
                if pending_tc is not None:
                    await self._history.append_history_init_item(
                        role=OpenaiApiRole.TOOL,
                        tool_call_id=pending_tc.id,
                    )
                    return TurnStepResult.TOOL_EXECUTE_SUCCESS
                output_item = await self._history.append_history_init_item(role=OpenaiApiRole.ASSISTANT)
                return await self._infer_and_classify(output_item, tools, tool_choice=tool_choice)
            elif status in (AgentHistoryStatus.FAILED, AgentHistoryStatus.CANCELLED):
                # FAILED/CANCELLED 的 tool 不重试，跳过并检查下一个 pending tool call
                pending_tc = self._history.get_first_pending_tool_call()
                if pending_tc is not None:
                    await self._history.append_history_init_item(
                        role=OpenaiApiRole.TOOL,
                        tool_call_id=pending_tc.id,
                    )
                    return TurnStepResult.TOOL_EXECUTE_SUCCESS
                output_item = await self._history.append_history_init_item(role=OpenaiApiRole.ASSISTANT)
                return await self._infer_and_classify(output_item, tools, tool_choice=tool_choice)
            else:
                raise RuntimeError(f"无法推进: agent_id={self.gt_agent.id}, role={role}, status={status}")

        elif role in (OpenaiApiRole.USER, OpenaiApiRole.SYSTEM):
            output_item = await self._history.append_history_init_item(role=OpenaiApiRole.ASSISTANT)
            return await self._infer_and_classify(output_item, tools, tool_choice=tool_choice)

        else:
            raise RuntimeError(f"无法推进: agent_id={self.gt_agent.id}, role={role}, status={status}")

    async def _infer_and_classify(
        self,
        output_item: GtAgentHistory,
        tools: list[llmApiUtil.OpenAITool],
        tool_choice: str | None = None,
    ) -> TurnStepResult:
        """执行推理并按结果分类返回。"""
        assistant_message = await self._infer_to_item(output_item, tools, tool_choice=tool_choice)
        if _detect_json_tool_call_in_content(assistant_message.content):
            for tc in (assistant_message.tool_calls or []):
                await self._history.append_history_message(GtAgentHistory.build(
                    llmApiUtil.OpenAIMessage.tool_result(tc.id, '{"success": false, "message": "工具调用被跳过：模型输出格式异常"}'),
                    status=AgentHistoryStatus.FAILED,
                    error_message="工具调用被跳过：模型输出格式异常",
                ))
            return TurnStepResult.LLM_OUTPUT_ERROR
        elif assistant_message.tool_calls:
            return TurnStepResult.LLM_OUTPUT_TOOL_CALLS
        else:
            return TurnStepResult.LLM_OUTPUT_NO_ACTION

    async def _infer_to_item(
        self,
        output_item: GtAgentHistory,
        tools: list[llmApiUtil.OpenAITool],
        tool_choice: str | None = None,
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
        activity_id: int | None = None
        request_retry_state: dict[str, bool | int | str | None] = {
            "seen": False,
            "attempt": None,
            "max_attempts": None,
            "last_retry_error": None,
        }

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

            # 活动记录：LLM_INFER STARTED（pre-check compact 已完成，不会与 COMPACT 并行）
            activity = await agentActivityService.add_activity(
                gt_agent=self.gt_agent, activity_type=AgentActivityType.LLM_INFER,
                metadata=self._base_metadata(model=resolved_model, estimated_prompt_tokens=estimated_tokens),
            )
            activity_id = activity.id

            ctx = GtCoreAgentDialogContext(system_prompt=self.system_prompt, messages=messages, tools=tools, tool_choice=tool_choice)

            # 流式推理 + 节流更新
            last_progress_time = time.monotonic()
            chunk_count_since_update = 0
            _THROTTLE_INTERVAL = 0.2  # 200ms
            _THROTTLE_CHUNK_COUNT = 10

            async def _on_progress(progress: llmService.InferStreamProgress) -> None:
                nonlocal last_progress_time, chunk_count_since_update
                chunk_count_since_update += 1
                now = time.monotonic()
                if chunk_count_since_update >= _THROTTLE_CHUNK_COUNT or (now - last_progress_time) >= _THROTTLE_INTERVAL:
                    patch = AgentActivityMeta()
                    patch.apply_progress(progress)
                    await agentActivityService.update_activity_progress(activity_id, metadata_patch=patch)
                    last_progress_time = now
                    chunk_count_since_update = 0

            async def _on_status_event(event: llmService.InferRequestStatusEvent) -> None:
                request_retry_state["seen"] = True
                request_retry_state["attempt"] = event.attempt
                request_retry_state["max_attempts"] = event.max_attempts
                request_retry_state["last_retry_error"] = event.error_message
                patch = AgentActivityMeta()
                patch.apply_request_status_event(event)
                await agentActivityService.update_activity_progress(activity_id, metadata_patch=patch)

            def _build_request_retry_meta_for_terminal() -> AgentActivityMeta | None:
                if request_retry_state["seen"] is not True:
                    return None
                patch = AgentActivityMeta(
                    request_state="",
                    retry_delay_seconds=0,
                    retry_attempt=int(request_retry_state["attempt"]) if request_retry_state["attempt"] is not None else 0,
                    retry_max_attempts=int(request_retry_state["max_attempts"]) if request_retry_state["max_attempts"] is not None else 0,
                    last_retry_error=str(request_retry_state["last_retry_error"]) if request_retry_state["last_retry_error"] is not None else "",
                )
                return patch

            print('DEBUG: BEFORE INFER_STREAM')
            infer_result: llmService.InferResult = await llmService.infer_stream(
                self.gt_agent.model, ctx, on_progress=_on_progress, on_status_event=_on_status_event,
            )

            # overflow retry
            print('DEBUG: AFTER INFER_STREAM')
            if infer_result.ok is False or infer_result.response is None:
                error = infer_result.error
                if (
                    error is not None
                    and compact.is_context_overflow_error(error)
                    and compact_stage != "pre"
                ):
                    logger.info(f"overflow retry 触发: {self.gt_agent.name}(agent_id={self.gt_agent.id}), error={infer_result.error_message}")
                    overflow_retry = True

                    # 标记当前 infer 活动为 FAILED
                    overflow_meta = AgentActivityMeta(error_kind="context_overflow")
                    request_retry_meta = _build_request_retry_meta_for_terminal()
                    if request_retry_meta is not None:
                        overflow_meta.request_state = request_retry_meta.request_state
                        overflow_meta.retry_attempt = request_retry_meta.retry_attempt
                        overflow_meta.retry_max_attempts = request_retry_meta.retry_max_attempts
                        overflow_meta.retry_delay_seconds = request_retry_meta.retry_delay_seconds
                        overflow_meta.last_retry_error = request_retry_meta.last_retry_error
                    await self._finish_activity(activity_id, status=AgentActivityStatus.FAILED, error_message=infer_result.error_message, metadata_patch=overflow_meta)

                    await self._execute_compact()
                    messages = history.build_infer_messages()
                    estimated_tokens = compact.estimate_tokens(resolved_model, messages, self.system_prompt)
                    if estimated_tokens >= hard_limit_tokens:
                        raise RuntimeError(f"overflow compact 后仍超限: agent_id={self.gt_agent.id}") from error

                    # 新建 infer 活动记录
                    retry_metadata = self._base_metadata(
                        model=resolved_model, estimated_prompt_tokens=estimated_tokens, overflow_retry=True,
                    )
                    activity = await agentActivityService.add_activity(
                        gt_agent=self.gt_agent, activity_type=AgentActivityType.LLM_INFER,
                        detail="overflow 重试", metadata=retry_metadata,
                    )
                    activity_id = activity.id
                    request_retry_state = {
                        "seen": False,
                        "attempt": None,
                        "max_attempts": None,
                        "last_retry_error": None,
                    }
                    last_progress_time = time.monotonic()
                    chunk_count_since_update = 0

                    ctx = GtCoreAgentDialogContext(system_prompt=self.system_prompt, messages=messages, tools=tools, tool_choice=tool_choice)
                    infer_result = await llmService.infer_stream(
                        self.gt_agent.model, ctx, on_progress=_on_progress, on_status_event=_on_status_event,
                    )

                print('DEBUG: AFTER INFER_STREAM')
            if infer_result.ok is False or infer_result.response is None:
                    error_message = infer_result.error_message or "unknown inference error"
                    raise RuntimeError(error_message) from infer_result.error

            usage = infer_result.usage
            choice = infer_result.response.choices[0]
            if choice.finish_reason == "length":
                raise RuntimeError(
                    f"LLM 输出被截断（finish_reason=length），max_tokens 不足以完成本次推理: "
                    f"agent_id={self.gt_agent.id}, completion_tokens={usage.completion_tokens if usage else '?'}"
                )
            assistant_message = choice.message
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

            # 活动记录：LLM_INFER SUCCEEDED
            final_meta = AgentActivityMeta()
            final_meta.apply_usage(usage)
            request_retry_meta = _build_request_retry_meta_for_terminal()
            if request_retry_meta is not None:
                final_meta.request_state = request_retry_meta.request_state
                final_meta.retry_attempt = request_retry_meta.retry_attempt
                final_meta.retry_max_attempts = request_retry_meta.retry_max_attempts
                final_meta.retry_delay_seconds = request_retry_meta.retry_delay_seconds
                final_meta.last_retry_error = request_retry_meta.last_retry_error
            await self._finish_activity(activity_id, status=AgentActivityStatus.SUCCEEDED, metadata_patch=final_meta)

            # 活动记录：思考内容和直接发言
            if assistant_message.reasoning_content and assistant_message.reasoning_content.strip():
                await agentActivityService.add_activity(
                    gt_agent=self.gt_agent, activity_type=AgentActivityType.REASONING,
                    status=AgentActivityStatus.SUCCEEDED, detail=assistant_message.reasoning_content,
                    metadata=self._base_metadata(),
                )
            if assistant_message.content and assistant_message.content.strip():
                await agentActivityService.add_activity(
                    gt_agent=self.gt_agent, activity_type=AgentActivityType.CHAT_REPLY,
                    status=AgentActivityStatus.SUCCEEDED, detail=assistant_message.content,
                    metadata=self._base_metadata(),
                )

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
            # 活动记录：LLM_INFER FAILED（pre-check compact 失败时 activity_id 为 None，无需更新）
            if activity_id is not None:
                await self._finish_activity(
                    activity_id,
                    status=AgentActivityStatus.FAILED,
                    error_message=str(e),
                    metadata_patch=_build_request_retry_meta_for_terminal(),
                )
            raise

    async def _run_tool_to_item(self, tool_call: llmApiUtil.OpenAIToolCall, output_item: GtAgentHistory, room: ChatRoom | None) -> TurnStepResult:
        """执行单个工具调用，结果写入 output_item。

        返回：
            `TURN_DONE`：turn 结束类工具（marks_turn_finish）执行成功。
            `ERROR_ACTION`：turn 结束类工具执行失败，触发 failed_action_count 计数（防止死循环）。
            `CONTINUE`：普通工具执行完毕，继续下一步。
        """
        tool_name = tool_call.function_name
        tool_metadata = self._base_metadata(
            tool_name=tool_name,
            tool_arguments=self._extract_tool_arguments(tool_call),
            tool_call_id=tool_call.id,
            command=self._extract_tool_command(tool_call),
        )
        tool_activity = await agentActivityService.add_activity(
            gt_agent=self.gt_agent, activity_type=AgentActivityType.TOOL_CALL,
            detail=tool_name, metadata=tool_metadata,
        )
        registered_tool: RegisteredTool | None = self.tool_registry.get_registered_tool(tool_name)
        if registered_tool is None:
            error_msg = f"工具 '{tool_name}' 未找到，请使用已有工具完成行动。"
            logger.warning("tool not registered: agent_id=%d, tool=%s", self.gt_agent.id, tool_name)

            final_message = llmApiUtil.OpenAIMessage.tool_result(
                output_item.tool_call_id, json.dumps({"success": False, "message": error_msg}, ensure_ascii=False)
            )

            await self._history.finalize_history_item(
                history_id=output_item.id, message=final_message, status=AgentHistoryStatus.FAILED, error_message=error_msg
            )
            await self._finish_activity(tool_activity.id, status=AgentActivityStatus.FAILED, error_message=error_msg)
            return TurnStepResult.TOOL_EXECUTE_FAILED_FINISH

        if registered_tool.self_interrupt:
            if AgentHistoryTag.SELF_INTERRUPT in output_item.tags:
                # 重启后：output_item 已有 SELF_INTERRUPT tag，说明上次已触发过，直接自动成功。
                logger.info(
                    "[self-interrupt] 重启后自动完成自中断工具: agent_id=%d, tool=%s",
                    self.gt_agent.id, tool_name,
                )
                auto_result = {"success": True, "message": f"已完成重启，并恢复原历史任务运行"}
                final_message = llmApiUtil.OpenAIMessage.tool_result(
                    output_item.tool_call_id,
                    json.dumps(auto_result, ensure_ascii=False),
                )
                await self._history.finalize_history_item(
                    history_id=output_item.id,
                    message=final_message,
                    status=AgentHistoryStatus.SUCCESS,
                )
                await self._finish_activity(
                    tool_activity.id,
                    status=AgentActivityStatus.SUCCEEDED,
                    metadata_patch=AgentActivityMeta(tool_result=auto_result),
                )
                return TurnStepResult.TOOL_EXECUTE_SUCCESS
            else:
                # 第一次执行：写入 tag 后继续执行 handler。
                # handler 会触发 agent 中断（CancelledError），item 以 INIT+tag 留在 DB。
                await self._history.mark_self_interrupt_tag(output_item.id)

        team_id = room.team_id if room is not None else self.gt_agent.team_id
        context = ToolCallContext(
            agent_id=self.gt_agent.id,
            team_id=team_id,
            chat_room=room,
            schedule_task=self._current_task,
        )
        exec_result:ToolExecutionResult = await self.tool_registry.execute_tool_call(tool_call, context)
        final_message = llmApiUtil.OpenAIMessage.tool_result(
            exec_result.tool_call_id,
            json.dumps(exec_result.result, ensure_ascii=False),
        )
        await self._history.finalize_history_item(
            history_id=output_item.id,
            message=final_message,
            status=AgentHistoryStatus.SUCCESS if exec_result.success else AgentHistoryStatus.FAILED,
            error_message=exec_result.error_message,
            tags=[AgentHistoryTag.ROOM_TURN_FINISH] if (registered_tool.marks_turn_finish and exec_result.success) else None,
        )

        # 活动记录：TOOL_CALL SUCCEEDED / FAILED
        await self._finish_activity(
            tool_activity.id,
            status=AgentActivityStatus.SUCCEEDED if exec_result.success else AgentActivityStatus.FAILED,
            error_message=exec_result.error_message,
            metadata_patch=AgentActivityMeta(tool_result=exec_result.result),
        )

        if registered_tool.marks_turn_finish:
            if exec_result.success:
                return TurnStepResult.TURN_DONE
            # finish 类工具失败：触发 failed_action_count，防止 LLM 反复重试导致死循环
            return TurnStepResult.TOOL_EXECUTE_FAILED_FINISH
        return TurnStepResult.TOOL_EXECUTE_SUCCESS

    # ─── AgentDriverHost 协议方法 ──────────────────────────

    async def execute_pending_tools(self) -> None:
        """执行最后一条 assistant 消息中的所有 tool calls。

        AgentDriverHost 协议方法，通过 _current_room 获取房间上下文，
        由 run_task_turn 在调用前设置。协作任务模式下 _current_room 为 None。
        """
        room = self._current_room  # may be None for TODO_TASK

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

    def _resolve_compact_config(self) -> tuple[str, "LlmModelConfig", int, int]:
        """获取 compact 相关配置：(resolved_model, model_config, trigger_tokens, hard_limit_tokens)。"""
        from service.llmService.core import resolve_model
        try:
            _, model_config, _, resolved_model = resolve_model(self.gt_agent.model)
        except ValueError as e:
            raise ValueError(f"无法解析代理所使用的模型配置: {e}")
            
        trigger_tokens = compact.calc_compact_trigger_tokens(resolved_model, model_config)
        hard_limit_tokens = compact.calc_hard_limit_tokens(resolved_model, model_config)
        return resolved_model, model_config, trigger_tokens, hard_limit_tokens

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
        await self._execute_compact()

        messages = self._history.build_infer_messages()
        msg_summary = ", ".join(f"{m.role}:{len(m.content or '') if not m.tool_calls else 'TC'}" for m in messages)
        logger.info(
            "[compact-recheck] agent_id=%d, message_count=%d, messages=[%s]",
            self.gt_agent.id, len(messages), msg_summary,
        )
        estimated_tokens = compact.estimate_tokens(resolved_model, messages, self.system_prompt)
        if estimated_tokens >= hard_limit_tokens:
            raise RuntimeError(
                f"{check_stage} compact 后仍超限: agent_id={self.gt_agent.id}, "
                f"estimated={estimated_tokens}, hard_limit={hard_limit_tokens}"
            )

        return messages, estimated_tokens, True

    async def _execute_compact(self) -> None:
        """执行一次 compact：生成摘要 → 插入 COMPACT_SUMMARY → 内存裁剪。失败时 raise。"""
        resolved_model, llm_config, _, _ = self._resolve_compact_config()

        compact_activity = await agentActivityService.add_activity(
            gt_agent=self.gt_agent, activity_type=AgentActivityType.COMPACT, metadata=self._base_metadata(),
        )

        compact_plan = self._history.build_compact_plan()
        if compact_plan is None:
            logger.warning("compact 跳过：无可压缩消息, agent_id=%d", self.gt_agent.id)
            await self._finish_activity(compact_activity.id, status=AgentActivityStatus.FAILED, error_message="无可压缩消息")
            raise RuntimeError("compact 跳过：无可压缩消息")

        # 摘要 token 上限动态设为上下文长度的 10%，随模型配置自动伸缩
        compact_max_tokens = max(1, int(llm_config.context_config.context_window_tokens * 0.1))
        try:
            summary_text = await compact.compact_messages(
                messages=compact_plan.source_messages,
                system_prompt=self.system_prompt,
                model=resolved_model,
                tools=self.tool_registry.export_openai_tools(),
                max_tokens=compact_max_tokens,
            )
        except Exception as e:
            error_detail = str(e)
            logger.warning("compact 失败: %s, agent_id=%d", error_detail, self.gt_agent.id)
            await self._finish_activity(compact_activity.id, status=AgentActivityStatus.FAILED, error_message=error_detail)
            raise

        await self._history.insert_compact_summary(
            llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, summary_text),
            seq=compact_plan.insert_seq,
        )

        # 活动记录：COMPACT SUCCEEDED
        await self._finish_activity(compact_activity.id, status=AgentActivityStatus.SUCCEEDED)

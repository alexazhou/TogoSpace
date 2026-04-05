import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, List, Optional

from constants import AgentHistoryStage, AgentHistoryStatus, AgentHistoryTag, AgentTaskStatus, DriverType, MessageBusTopic, AgentStatus, RoomState
from model.dbModel.gtAgentTask import GtAgentTask
from model.coreModel.gtCoreChatModel import GtCoreAgentDialogContext, GtCoreChatMessage
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtAgentHistory import GtAgentHistory
from dal.db import gtAgentTaskManager
from service import funcToolService, llmService, messageBus, roomService
from service.agentService.agentHistoryStore import AgentHistoryStore
from service.agentService.driver import AgentDriverConfig, AgentTurnSetup, build_agent_driver
from service.agentService.promptBuilder import build_turn_context_prompt, format_room_message
from service.agentService.toolRegistry import AgentToolRegistry, ToolExecutionResult
from service.roomService import ChatRoom, ToolCallContext
from util import asyncUtil, llmApiUtil

logger = logging.getLogger(__name__)


class Agent:
    """AI Team Agent 实例：承载在特定团队中的身份和状态，driver 负责具体驱动实现。"""

    def __init__(
        self,
        gt_agent: GtAgent,
        system_prompt: str,
        driver_config: Optional[AgentDriverConfig] = None,
        agent_workdir: str = "",
        max_function_calls: int = 5,
    ):
        self.gt_agent: GtAgent = gt_agent
        self.system_prompt: str = system_prompt
        self.agent_workdir: str = agent_workdir
        self.max_function_calls: int = max(1, max_function_calls)
        self._history_store: AgentHistoryStore = AgentHistoryStore(self.gt_agent.id or 0)
        self.tool_registry: AgentToolRegistry = AgentToolRegistry()
        self.status: AgentStatus = AgentStatus.IDLE
        self.consumer_task: asyncio.Task | None = None
        self.current_db_task: Optional[GtAgentTask] = None
        self.driver = build_agent_driver(self, driver_config or AgentDriverConfig(driver_type=DriverType.NATIVE))

    @property
    def _history(self) -> AgentHistoryStore:
        return self._history_store

    @property
    def is_active(self) -> bool:
        """检查 Agent 是否活跃（状态为 ACTIVE 或有正在处理的任务）。"""
        return self.status == AgentStatus.ACTIVE or self.current_db_task is not None

    async def startup(self) -> None:
        await self.driver.startup()
        self.driver.mark_started()

    async def close(self) -> None:
        self.stop_consumer_task()
        await self.driver.shutdown()
        self.driver.mark_stopped()
        self.tool_registry.clear()

    def start_consumer_task(self, initial_task: GtAgentTask | None = None) -> None:
        """启动当前 Agent 的消费协程；若已在运行则跳过。若没有待处理 task，协程会自行退出。"""
        if initial_task is None and self.status == AgentStatus.FAILED:
            logger.info("Agent 已处于 FAILED 状态，跳过消费协程启动: agent_id=%s", self.gt_agent.id)
            return

        existing = self.consumer_task
        if existing is not None and existing.done() is False:
            return

        task = asyncio.create_task(self.consume_task(initial_task=initial_task))
        self.consumer_task = task

    def stop_consumer_task(self) -> None:
        """停止当前 Agent 的消费协程。"""
        task = self.consumer_task
        self.consumer_task = None
        asyncUtil.cancel_task_safely(task)

    def _publish_status(self, status: AgentStatus) -> None:
        messageBus.publish(
            MessageBusTopic.AGENT_STATUS_CHANGED,
            gt_agent=self.gt_agent,
            status=status,
        )

    async def resume_failed(self) -> int:
        """恢复最早的 FAILED 任务，并重新启动消费。"""
        failed_task = await gtAgentTaskManager.get_first_unfinish_task(self.gt_agent.id)
        if failed_task is None or failed_task.status != AgentTaskStatus.FAILED:
            raise RuntimeError(f"no failed task to resume: agent_id={self.gt_agent.id}")

        room_id = failed_task.task_data.get("room_id")
        if room_id is None:
            raise RuntimeError(f"failed task missing room_id: agent_id={self.gt_agent.id}, task_id={failed_task.id}")

        resumed_task = await gtAgentTaskManager.transition_task_status(
            failed_task.id,
            AgentTaskStatus.FAILED,
            AgentTaskStatus.RUNNING,
        )
        if resumed_task is None:
            raise RuntimeError(f"failed task resume conflict: agent_id={self.gt_agent.id}, task_id={failed_task.id}")

        self.status = AgentStatus.ACTIVE
        self._publish_status(self.status)
        self.start_consumer_task(initial_task=resumed_task)
        return room_id

    async def consume_task(
        self,
        max_function_calls: int | None = None,
        initial_task: GtAgentTask | None = None,
    ) -> None:
        """从数据库获取并处理任务，直到没有待处理任务为止。"""
        current_consumer = asyncio.current_task()
        if current_consumer is not None and self.consumer_task not in (None, current_consumer):
            existing = self.consumer_task
            if existing.done() is False:
                logger.warning(
                    "检测到重复启动的消费协程: agent_id=%s, existing_task=%s, current_task=%s",
                    self.gt_agent.id,
                    id(existing),
                    id(current_consumer),
                )
        effective_max_fc = self.max_function_calls if max_function_calls is None else max(1, max_function_calls)
        if self.status != AgentStatus.ACTIVE:
            self.status = AgentStatus.ACTIVE
            self._publish_status(self.status)
        try:
            claimed_task = initial_task
            resumed = initial_task is not None
            while True:
                if claimed_task is None:
                    # 从数据库获取最早的未完成任务；FAILED 任务会阻断后续任务继续执行
                    task = await gtAgentTaskManager.get_first_unfinish_task(self.gt_agent.id)
                    if task is None:
                        break  # 没有待处理任务了
                    if task.status != AgentTaskStatus.PENDING:
                        break

                    # 原子地认领任务（乐观锁）
                    claimed_task = await gtAgentTaskManager.transition_task_status(
                        task.id,
                        AgentTaskStatus.PENDING,
                        AgentTaskStatus.RUNNING,
                    )
                    if claimed_task is None:
                        # 任务已被其他消费者认领，继续尝试下一个
                        continue

                completed = await self._execute_claimed_task(
                    claimed_task,
                    effective_max_fc,
                    resumed=resumed,
                )
                if completed is False:
                    return
                claimed_task = None
                resumed = False
        finally:
            if self.status != AgentStatus.FAILED:
                self.status = AgentStatus.IDLE
                self._publish_status(self.status)

            if self.consumer_task is current_consumer:
                self.consumer_task = None
                if self.status == AgentStatus.FAILED:
                    return
                has_pending = await gtAgentTaskManager.has_consumable_task(self.gt_agent.id)
                if has_pending:
                    logger.info("Agent 任务收尾时检测到待处理任务，自动续起消费: agent_id=%s", self.gt_agent.id)
                    self.start_consumer_task()

    async def _execute_claimed_task(
        self,
        claimed_task: GtAgentTask,
        max_function_calls: int,
        *,
        resumed: bool,
    ) -> bool:
        """执行一条已处于 RUNNING 状态的任务。

        返回 True 表示任务完成，可继续后续任务；返回 False 表示任务失败，消费流程应立即停止。
        """
        self.current_db_task = claimed_task
        try:
            await self.run_chat_turn(claimed_task, max_function_calls, resumed=resumed)
        except Exception as e:
            room_id = claimed_task.task_data.get("room_id")
            room = roomService.get_room(room_id) if room_id is not None else None
            room_key = room.key if room is not None else f"room_id={room_id}"
            logger.error(
                "Agent 任务执行失败并标记为 FAILED: agent_id=%s, room=%s, task=%s, error=%s",
                self.gt_agent.id,
                room_key,
                claimed_task.id,
                e,
            )
            await gtAgentTaskManager.update_task_status(
                claimed_task.id,
                AgentTaskStatus.FAILED,
                error_message=str(e),
            )
            self.status = AgentStatus.FAILED
            self.current_db_task = None
            self._publish_status(self.status)
            return False

        await gtAgentTaskManager.update_task_status(claimed_task.id, AgentTaskStatus.COMPLETED)
        self.current_db_task = None
        return True

    async def pull_room_messages_to_history(self, room: ChatRoom) -> int:
        new_msgs: List[GtCoreChatMessage] = await room.get_unread_messages(self.gt_agent.id)
        logger.info(f"同步房间消息: agent_id={self.gt_agent.id}, room={room.name}, count={len(new_msgs)}")

        message_blocks: list[str] = []
        for msg in new_msgs:
            if msg.sender_id == self.gt_agent.id:
                continue
            sender_name = room._get_agent_name(msg.sender_id)
            message_blocks.append(format_room_message(room.name, sender_name, msg.content))

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

    async def run_chat_turn(self, task: GtAgentTask, max_function_calls: int = 5, resumed: bool = False) -> None:
        room_id = task.task_data.get("room_id")
        if room_id is None:
            logger.warning(f"run_chat_turn 跳过：task 缺少 room_id, agent_id={self.gt_agent.id}, task_id={task.id}")
            return

        room: ChatRoom | None = roomService.get_room(room_id)
        if room is None:
            logger.warning(f"run_chat_turn 跳过：room_id={room_id} 不存在, agent_id={self.gt_agent.id}")
            return

        if self.driver.host_managed_turn_loop:
            if resumed and self._has_unfinished_turn():
                await self._resume_chat_turn_with_host_loop(room, max_function_calls)
                return
            synced_count = await self.pull_room_messages_to_history(room)
            assert self.driver.started is True, f"driver 尚未启动: agent_id={self.gt_agent.id}"
            await self._run_chat_turn_with_host_loop(room, max_function_calls)
        else:
            synced_count = await self.pull_room_messages_to_history(room)
            await self.driver.run_chat_turn(task, synced_count, max_function_calls)

    async def _run_chat_turn_with_host_loop(self, room: ChatRoom, max_function_calls: int) -> None:
        turn_setup: AgentTurnSetup = self.driver.turn_setup
        tools: list[llmApiUtil.OpenAITool] = self.tool_registry.export_openai_tools()
        max_retries = max(1, turn_setup.max_retries)
        for _ in range(max_retries):
            turn_done = await self._run_until_reply(room, tools=tools, max_function_calls=max_function_calls)
            if turn_done:
                return
            if len(turn_setup.hint_prompt) > 0:
                await self._history.append_history_message(
                    llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiLLMApiRole.USER, turn_setup.hint_prompt),
                    stage=AgentHistoryStage.INPUT,
                )

    async def _run_until_reply(self, room: ChatRoom, tools: Optional[list[llmApiUtil.OpenAITool]], max_function_calls: int) -> bool:
        for _ in range(max_function_calls):
            assistant_message: llmApiUtil.OpenAIMessage = await self._infer(tools)
            tool_calls = assistant_message.tool_calls or []
            if len(tool_calls) == 0:
                return False

            turn_done = await self._execute_tool_calls(room, tool_calls)
            if turn_done:
                return True

        logger.warning(f"达到最大函数调用次数: agent_id={self.gt_agent.id}, max={max_function_calls}")
        return False

    def _get_unfinished_turn_start_index(self) -> int | None:
        for idx in range(len(self._history) - 1, -1, -1):
            item = self._history[idx]
            if AgentHistoryTag.ROOM_TURN_FINISH in item.tags:
                return None
            if AgentHistoryTag.ROOM_TURN_BEGIN in item.tags:
                return idx
        return None

    def _has_unfinished_turn(self) -> bool:
        return self._get_unfinished_turn_start_index() is not None

    async def _resume_chat_turn_with_host_loop(self, room: ChatRoom, max_function_calls: int) -> None:
        tools: list[llmApiUtil.OpenAITool] = self.tool_registry.export_openai_tools()
        turn_start_idx = self._get_unfinished_turn_start_index()
        if turn_start_idx is None:
            await self._run_chat_turn_with_host_loop(room, max_function_calls)
            return
        last_item = self._history.last()
        if last_item is None:
            await self._run_chat_turn_with_host_loop(room, max_function_calls)
            return

        if last_item.stage == AgentHistoryStage.INFER and last_item.status in (AgentHistoryStatus.INIT, AgentHistoryStatus.FAILED):
            assistant_message = await self._resume_infer_history_item(last_item, tools)
            tool_calls = assistant_message.tool_calls or []
            if tool_calls:
                turn_done = await self._execute_tool_calls(room, tool_calls)
                if turn_done:
                    return

        elif last_item.stage == AgentHistoryStage.TOOL_RESULT and last_item.status == AgentHistoryStatus.INIT:
            tool_call_id = str(last_item.tool_call_id or "")
            tool_call = self._find_tool_call_in_history(tool_call_id, start_idx=turn_start_idx)
            if tool_call is None:
                raise RuntimeError(f"resume tool call not found: agent_id={self.gt_agent.id}, tool_call_id={tool_call_id}")
            turn_done = await self._execute_tool_calls(
                room,
                [tool_call],
                reuse_history_items={tool_call_id: last_item},
            )
            if turn_done:
                return

        else:
            last_assistant = self._history.get_last_assistant_message(start_idx=turn_start_idx)
            if last_assistant is not None and last_assistant.tool_calls:
                turn_done = await self._execute_tool_calls(
                    room,
                    last_assistant.tool_calls,
                    execute_only_missing=True,
                )
                if turn_done:
                    return

        await self._run_chat_turn_with_host_loop(room, max_function_calls)

    async def _infer(self, tools: Optional[list[llmApiUtil.OpenAITool]]) -> llmApiUtil.OpenAIMessage:
        self._history.assert_infer_ready(f"agent_id={self.gt_agent.id}")
        ctx_tools: list[llmApiUtil.OpenAITool] | None = None
        if tools is not None and len(tools) > 0:
            ctx_tools = tools
        ctx = GtCoreAgentDialogContext(
            system_prompt=self.system_prompt,
            messages=self._history.export_openai_message_list(),
            tools=ctx_tools,
        )
        history_item = await self._history.append_stage_init(stage=AgentHistoryStage.INFER)
        infer_result: llmService.InferResult = await llmService.infer(self.gt_agent.model, ctx)
        if infer_result.ok is False or infer_result.response is None:
            error_message = infer_result.error_message or "unknown inference error"
            await self._history.finalize_history_item(
                history_item=history_item,
                message=None,
                status=AgentHistoryStatus.FAILED,
                error_message=error_message,
            )
            raise RuntimeError(f"LLM 推理失败: agent_id={self.gt_agent.id}, error={error_message}") from infer_result.error

        response = infer_result.response
        assistant_message = response.choices[0].message
        await self._history.finalize_history_item(
            history_item=history_item,
            message=assistant_message,
            status=AgentHistoryStatus.SUCCESS,
        )
        return assistant_message

    async def _resume_infer_history_item(
        self,
        history_item: GtAgentHistory,
        tools: Optional[list[llmApiUtil.OpenAITool]],
    ) -> llmApiUtil.OpenAIMessage:
        ctx_tools: list[llmApiUtil.OpenAITool] | None = None
        if tools is not None and len(tools) > 0:
            ctx_tools = tools
        ctx = GtCoreAgentDialogContext(
            system_prompt=self.system_prompt,
            messages=self._history.export_openai_message_list()[:-1],
            tools=ctx_tools,
        )
        infer_result: llmService.InferResult = await llmService.infer(self.gt_agent.model, ctx)
        if infer_result.ok is False or infer_result.response is None:
            error_message = infer_result.error_message or "unknown inference error"
            await self._history.finalize_history_item(
                history_item=history_item,
                message=None,
                status=AgentHistoryStatus.FAILED,
                error_message=error_message,
            )
            raise RuntimeError(f"LLM 推理失败: agent_id={self.gt_agent.id}, error={error_message}") from infer_result.error

        assistant_message = infer_result.response.choices[0].message
        await self._history.finalize_history_item(
            history_item=history_item,
            message=assistant_message,
            status=AgentHistoryStatus.SUCCESS,
        )
        return assistant_message

    def _find_tool_call_in_history(self, tool_call_id: str, start_idx: int = 0) -> llmApiUtil.OpenAIToolCall | None:
        if len(tool_call_id) == 0:
            return None
        for item in reversed(self._history[start_idx:]):
            if item.role != llmApiUtil.OpenaiLLMApiRole.ASSISTANT or item.tool_calls is None:
                continue
            for tool_call in item.tool_calls:
                if str(tool_call.id or "") == tool_call_id:
                    return tool_call
        return None

    async def _execute_tool(self) -> None:
        current_db_task = self.current_db_task
        assert current_db_task is not None, "current_db_task should not be None while executing tool"
        room_id = current_db_task.task_data.get("room_id")
        assert room_id is not None, "current_db_task should have room_id"
        room = roomService.get_room(room_id)
        assert room is not None, f"room should exist: room_id={room_id}"

        last_msg: llmApiUtil.OpenAIMessage | None = self._history.get_last_assistant_message()
        if last_msg is None or last_msg.tool_calls is None or len(last_msg.tool_calls) == 0:
            return

        for tool_call in last_msg.tool_calls:
            function: dict[str, Any] = tool_call.function if isinstance(tool_call.function, dict) else {}
            name = function.get("name", "")
            args = function.get("arguments", "")
            context: ToolCallContext = ToolCallContext(
                agent_name=self.gt_agent.name,
                team_id=room.team_id,
                chat_room=room,
                tool_name=name,
            )
            await self._execute_tool_call_with_history(
                tool_call,
                lambda: self._run_function_tool_call(tool_call, args, name, context),
            )

    async def _execute_tool_calls(
        self,
        room: ChatRoom,
        tool_calls: list[llmApiUtil.OpenAIToolCall],
        *,
        reuse_history_items: dict[str, GtAgentHistory] | None = None,
        execute_only_missing: bool = False,
    ) -> bool:
        logger.info(f"检测到工具调用: agent_id={self.gt_agent.id}, count={len(tool_calls)}")
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

            if history_item is not None:
                exec_result = await self._execute_tool_call_with_existing_history(
                    history_item,
                    tool_call,
                    lambda: self.tool_registry.execute_tool_call(tool_call, context),
                )
            else:
                exec_result = await self._execute_tool_call_with_history(
                    tool_call,
                    lambda: self.tool_registry.execute_tool_call(tool_call, context),
                )
            if exec_result.turn_finished and (
                exec_result.status == AgentHistoryStatus.SUCCESS
                or room.state == RoomState.INIT
            ):
                turn_done = True
        return turn_done

    async def _execute_tool_call_with_history(
        self,
        tool_call: llmApiUtil.OpenAIToolCall,
        executor: Callable[[], Awaitable[ToolExecutionResult]],
    ) -> ToolExecutionResult:
        assert tool_call.id, "tool_call.id should not be empty"
        history_item = await self._history.append_stage_init(
            stage=AgentHistoryStage.TOOL_RESULT,
            tool_call_id=str(tool_call.id),
        )
        exec_result = await executor()
        final_message = llmApiUtil.OpenAIMessage.tool_result(exec_result.tool_call_id, exec_result.result_json)
        await self._history.finalize_history_item(
            history_item=history_item,
            message=final_message,
            status=exec_result.status,
            error_message=exec_result.error_message,
            tags=exec_result.tags,
        )
        return exec_result

    async def _execute_tool_call_with_existing_history(
        self,
        history_item: GtAgentHistory,
        tool_call: llmApiUtil.OpenAIToolCall,
        executor: Callable[[], Awaitable[ToolExecutionResult]],
    ) -> ToolExecutionResult:
        assert tool_call.id, "tool_call.id should not be empty"
        exec_result = await executor()
        final_message = llmApiUtil.OpenAIMessage.tool_result(exec_result.tool_call_id, exec_result.result_json)
        await self._history.finalize_history_item(
            history_item=history_item,
            message=final_message,
            status=exec_result.status,
            error_message=exec_result.error_message,
            tags=exec_result.tags,
        )
        return exec_result

    async def _run_function_tool_call(
        self,
        tool_call: llmApiUtil.OpenAIToolCall,
        args: str,
        tool_name: str,
        context: ToolCallContext,
    ) -> ToolExecutionResult:
        result_data: dict[str, Any] = await funcToolService.run_tool_call(args, context=context)
        result_json = json.dumps(result_data, ensure_ascii=False)
        raw_success = result_data.get("success")
        status = AgentHistoryStatus.FAILED if raw_success is False else AgentHistoryStatus.SUCCESS
        error_message = None
        if status == AgentHistoryStatus.FAILED and result_data.get("message") is not None:
            error_message = str(result_data.get("message"))

        tags: list[AgentHistoryTag] | None = None
        if tool_name == "finish_chat_turn" and status == AgentHistoryStatus.SUCCESS:
            tags = [AgentHistoryTag.ROOM_TURN_FINISH]

        assert tool_call.id, "tool_call.id should not be empty"
        return ToolExecutionResult(
            tool_call_id=str(tool_call.id),
            result_json=result_json,
            status=status,
            error_message=error_message,
            tags=tags,
        )

    def dump_history_messages(self) -> List[GtAgentHistory]:
        return self._history.dump()

    def inject_history_messages(self, items: List[GtAgentHistory]) -> None:
        self._history.replace(items)

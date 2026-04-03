import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, List, Optional

from constants import AgentHistoryStage, AgentHistoryStatus, AgentHistoryTag, DriverType, MessageBusTopic, MemberStatus, RoomState
from model.coreModel.gtCoreAgentEvent import GtCoreRoomMessageEvent
from model.coreModel.gtCoreChatModel import GtCoreAgentDialogContext, GtCoreChatMessage
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtAgentHistory import GtAgentHistory
from service import funcToolService, llmService, messageBus, persistenceService, roomService
from service.agentService.agentHistroy import AgentHistory
from service.agentService.driver import AgentDriverConfig, AgentTurnSetup, build_agent_driver
from service.agentService.toolRegistry import AgentToolRegistry, ToolExecutionResult
from service.roomService import ChatRoom, ToolCallContext
from util import llmApiUtil
from util.chatMessageFormat import build_turn_context_prompt, format_room_message

logger = logging.getLogger(__name__)

MAX_INFER_RETRIES = 3


class Agent:
    """AI Team Agent 实例：承载在特定团队中的身份和状态，driver 负责具体驱动实现。"""

    def __init__(
        self,
        gt_agent: GtAgent,
        system_prompt: str,
        driver_config: Optional[AgentDriverConfig] = None,
        team_workdir: str = "",
        workspace_root: str = "",
    ):
        self.gt_agent: GtAgent = gt_agent
        self.system_prompt: str = system_prompt
        self.team_workdir: str = team_workdir
        self.workspace_root: str = workspace_root
        self._history_store: AgentHistory = AgentHistory(self.gt_agent.id or 0)
        self.tool_registry: AgentToolRegistry = AgentToolRegistry()
        self.wait_task_queue: asyncio.Queue = asyncio.Queue()
        self.status: MemberStatus = MemberStatus.IDLE
        self.current_room: Optional[ChatRoom] = None
        self.driver = build_agent_driver(self, driver_config or AgentDriverConfig(driver_type=DriverType.NATIVE))

    @property
    def _history(self) -> AgentHistory:
        return self._history_store

    @property
    def is_active(self) -> bool:
        return self.status == MemberStatus.ACTIVE or self.wait_task_queue.empty() is False

    async def startup(self) -> None:
        await self.driver.startup()
        self.driver.mark_started()

    async def close(self) -> None:
        await self.driver.shutdown()
        self.driver.mark_stopped()
        self.tool_registry.clear()

    def _peek_task(self) -> Any | None:
        if self.wait_task_queue.empty():
            return None
        return self.wait_task_queue._queue[0]

    def resume_failed(self) -> int:
        """清除 FAILED 状态，从队头任务读取 room_id 返回，供调用方触发续跑。"""
        if self.status != MemberStatus.FAILED:
            raise ValueError(f"Agent ID={self.gt_agent.id} 当前状态不是 FAILED（当前: {self.status.name}）")

        task = self._peek_task()
        assert isinstance(task, GtCoreRoomMessageEvent), "resume_failed requires pending room message event"
        room_id: int = task.room_id
        self.status = MemberStatus.IDLE
        self._publish_status(self.status)
        return room_id

    def _publish_status(self, status: MemberStatus) -> None:
        messageBus.publish(
            MessageBusTopic.AGENT_STATUS_CHANGED,
            gt_agent=self.gt_agent,
            status=status.name,
        )

    async def consume_task(self, max_function_calls: int) -> None:
        self.status = MemberStatus.ACTIVE
        self._publish_status(self.status)
        try:
            while self.wait_task_queue.empty() is False:
                task = self._peek_task()
                task_succeeded = False
                last_error: Exception | None = None
                for attempt in range(1, MAX_INFER_RETRIES + 1):
                    try:
                        if isinstance(task, GtCoreRoomMessageEvent):
                            await self.run_chat_turn(task.room_id, max_function_calls)
                        else:
                            raise TypeError(f"不支持的任务类型: {type(task).__name__}")
                        task_succeeded = True
                        break
                    except Exception as e:
                        last_error = e
                        logger.warning(
                            f"Agent 任务执行失败 (第 {attempt}/{MAX_INFER_RETRIES} 次): agent_id={self.gt_agent.id}, task={task!r}, error={e}",
                            exc_info=(attempt == MAX_INFER_RETRIES),
                        )

                if task_succeeded is False:
                    logger.error(
                        f"Agent 推理连续失败 {MAX_INFER_RETRIES} 次，标记为 FAILED: agent_id={self.gt_agent.id}, last_error={last_error}"
                    )
                    self.status = MemberStatus.FAILED
                    self._publish_status(self.status)
                    return

                self.wait_task_queue.get_nowait()
                self.wait_task_queue.task_done()
        finally:
            if self.status != MemberStatus.FAILED:
                self.status = MemberStatus.IDLE
                self._publish_status(self.status)

    async def pull_room_messages_to_history(self, room: ChatRoom) -> int:
        new_msgs: List[GtCoreChatMessage] = await room.get_unread_messages(self.gt_agent.name)
        logger.info(f"同步房间消息: agent_id={self.gt_agent.id}, room={room.name}, count={len(new_msgs)}")

        message_blocks: list[str] = []
        for msg in new_msgs:
            if msg.sender_name == self.gt_agent.name:
                continue
            message_blocks.append(format_room_message(room.name, msg.sender_name, msg.content))

        if len(message_blocks) == 0:
            return 0

        turn_context_message = llmApiUtil.OpenAIMessage.text(
            llmApiUtil.OpenaiLLMApiRole.USER,
            content=build_turn_context_prompt(room.name, message_blocks),
        )
        await self.append_history_message(
            turn_context_message,
            stage=AgentHistoryStage.INPUT,
            tags=[AgentHistoryTag.ROOM_TURN_BEGIN],
        )
        return 1

    async def run_chat_turn(self, room_id: int, max_function_calls: int = 5) -> None:
        room: ChatRoom | None = roomService.get_room(room_id)
        if room is None:
            logger.warning(f"run_chat_turn 跳过：room_id={room_id} 不存在, agent_id={self.gt_agent.id}")
            return

        self.current_room = room
        synced_count = await self.pull_room_messages_to_history(room)
        try:
            if self.driver.host_managed_turn_loop:
                await self._ensure_driver_started()
                await self._run_chat_turn_with_host_loop(max_function_calls)
            else:
                await self.driver.run_chat_turn(room, synced_count, max_function_calls)
        except Exception as e:
            logger.warning(f"run_chat_turn 异常: agent_id={self.gt_agent.id}, room={room.key}, error={e}")
            raise
        finally:
            self.current_room = None

    async def _ensure_driver_started(self) -> None:
        if self.driver.started:
            return
        await self.driver.startup()
        self.driver.mark_started()

    async def _run_chat_turn_with_host_loop(self, max_function_calls: int) -> None:
        turn_setup: AgentTurnSetup = self.driver.turn_setup
        tools: list[llmApiUtil.OpenAITool] = self.tool_registry.export_openai_tools()
        max_retries = max(1, turn_setup.max_retries)
        for _ in range(max_retries):
            turn_done = await self._run_until_reply(tools=tools, max_function_calls=max_function_calls)
            if turn_done:
                return
            if len(turn_setup.hint_prompt) > 0:
                await self.append_history_message(
                    llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiLLMApiRole.USER, turn_setup.hint_prompt),
                    stage=AgentHistoryStage.INPUT,
                )

    async def _run_until_reply(self, tools: Optional[list[llmApiUtil.OpenAITool]], max_function_calls: int) -> bool:
        current_room = self.current_room
        assert current_room is not None, "current_room should not be None while running chat turn"
        context: ToolCallContext = ToolCallContext(
            agent_name=self.gt_agent.name,
            team_id=current_room.team_id,
            chat_room=current_room,
        )
        for _ in range(max_function_calls):
            assistant_message: llmApiUtil.OpenAIMessage = await self._infer(tools)
            tool_calls: list[llmApiUtil.OpenAIToolCall] | None = assistant_message.tool_calls
            if tool_calls is None or len(tool_calls) == 0:
                return False

            logger.info(f"检测到工具调用: agent_id={self.gt_agent.id}, count={len(tool_calls)}")
            turn_done = False
            for tool_call in tool_calls:
                exec_result = await self._execute_tool_call_with_history(
                    tool_call,
                    lambda: self.tool_registry.execute_tool_call(tool_call, context),
                )
                if exec_result.turn_finished and (
                    exec_result.status == AgentHistoryStatus.SUCCESS
                    or current_room.state == RoomState.INIT
                ):
                    turn_done = True

            if turn_done:
                return True

        logger.warning(f"达到最大函数调用次数: agent_id={self.gt_agent.id}, max={max_function_calls}")
        return False

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
        history_item = await self._append_stage_init(stage=AgentHistoryStage.INFER)
        infer_result: llmService.InferResult = await llmService.infer(self.gt_agent.model, ctx)
        if infer_result.ok is False or infer_result.response is None:
            error_message = infer_result.error_message or "unknown inference error"
            await self._finalize_history_item(
                history_item=history_item,
                message=None,
                status=AgentHistoryStatus.FAILED,
                error_message=error_message,
            )
            raise RuntimeError(f"LLM 推理失败: agent_id={self.gt_agent.id}, error={error_message}") from infer_result.error

        response = infer_result.response
        assistant_message = response.choices[0].message
        await self._finalize_history_item(
            history_item=history_item,
            message=assistant_message,
            status=AgentHistoryStatus.SUCCESS,
        )
        return assistant_message

    async def _execute_tool(self) -> None:
        current_room = self.current_room
        assert current_room is not None, "current_room should not be None while executing tool"
        last_msg: llmApiUtil.OpenAIMessage | None = self._history.get_last_assistant_message()
        if last_msg is None or last_msg.tool_calls is None or len(last_msg.tool_calls) == 0:
            return

        for tool_call in last_msg.tool_calls:
            function: dict[str, Any] = tool_call.function if isinstance(tool_call.function, dict) else {}
            name = function.get("name", "")
            args = function.get("arguments", "")
            context: ToolCallContext = ToolCallContext(
                agent_name=self.gt_agent.name,
                team_id=current_room.team_id,
                chat_room=current_room,
                tool_name=name,
            )
            await self._execute_tool_call_with_history(
                tool_call,
                lambda: self._run_function_tool_call(tool_call, args, name, context),
            )

    async def _execute_tool_call_with_history(
        self,
        tool_call: llmApiUtil.OpenAIToolCall,
        executor: Callable[[], Awaitable[ToolExecutionResult]],
    ) -> ToolExecutionResult:
        assert tool_call.id, "tool_call.id should not be empty"
        history_item = await self._append_stage_init(
            stage=AgentHistoryStage.TOOL_RESULT,
            tool_call_id=str(tool_call.id),
        )
        exec_result = await executor()
        final_message = llmApiUtil.OpenAIMessage.tool_result(exec_result.tool_call_id, exec_result.result_json)
        await self._finalize_history_item(
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

    async def _append_stage_init(
        self,
        stage: AgentHistoryStage,
        tool_call_id: str | None = None,
        tags: list[AgentHistoryTag] | None = None,
    ) -> GtAgentHistory:
        if stage == AgentHistoryStage.INPUT:
            role = llmApiUtil.OpenaiLLMApiRole.USER
        elif stage == AgentHistoryStage.INFER:
            role = llmApiUtil.OpenaiLLMApiRole.ASSISTANT
        elif stage == AgentHistoryStage.TOOL_RESULT:
            role = llmApiUtil.OpenaiLLMApiRole.TOOL
        else:
            raise ValueError(f"不支持的 history stage: {stage}")

        init_message = llmApiUtil.OpenAIMessage(role=role, tool_call_id=tool_call_id)
        append_kwargs: dict[str, Any] = {"stage": stage, "status": AgentHistoryStatus.INIT}
        if tags is not None:
            append_kwargs["tags"] = tags

        item: GtAgentHistory = self._history.append_message(init_message, **append_kwargs)
        saved = await persistenceService.append_agent_history_message(item)
        if saved is not None:
            item.id = saved.id
        return item

    async def _finalize_history_item(
        self,
        history_item: GtAgentHistory,
        message: llmApiUtil.OpenAIMessage | None,
        status: AgentHistoryStatus,
        error_message: str | None = None,
        tags: list[AgentHistoryTag] | None = None,
    ) -> None:
        message_json: str | None = None
        if message is not None:
            message_json = message.model_dump_json(exclude_none=True)
            history_item.message_json = message_json
        history_item.status = status
        history_item.error_message = error_message
        if tags is not None:
            history_item.tags = list(tags)

        assert history_item.id is not None, "history row id should not be None after append"
        await persistenceService.update_agent_history_by_id(
            history_id=history_item.id,
            message_json=message_json,
            status=status,
            error_message=error_message,
            tags=(history_item.tags if tags is not None else None),
        )

    def dump_history_messages(self) -> List[GtAgentHistory]:
        return self._history.dump()

    def inject_history_messages(self, items: List[GtAgentHistory]) -> None:
        self._history.replace(items)

    async def append_history_message(
        self,
        message: llmApiUtil.OpenAIMessage,
        stage: AgentHistoryStage | None = None,
        status: AgentHistoryStatus | None = None,
        error_message: str | None = None,
        tags: list[AgentHistoryTag] | None = None,
    ) -> GtAgentHistory:
        target_status = status or AgentHistoryStatus.SUCCESS
        append_kwargs: dict[str, Any] = {"status": target_status}
        if stage is not None:
            append_kwargs["stage"] = stage
        if error_message is not None:
            append_kwargs["error_message"] = error_message
        if tags is not None:
            append_kwargs["tags"] = tags

        item: GtAgentHistory = self._history.append_message(message, **append_kwargs)
        saved = await persistenceService.append_agent_history_message(item)
        if saved is not None:
            item.id = saved.id
        return item

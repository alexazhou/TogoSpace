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
from util import llmApiUtil

logger = logging.getLogger(__name__)

MAX_INFER_RETRIES = 3


class Agent:
    """AI Team Agent 实例：承载在特定团队中的身份和状态，driver 负责具体驱动实现。"""

    def __init__(
        self,
        gt_agent: GtAgent,
        system_prompt: str,
        driver_config: Optional[AgentDriverConfig] = None,
        agent_workdir: str = "",
    ):
        self.gt_agent: GtAgent = gt_agent
        self.system_prompt: str = system_prompt
        self.agent_workdir: str = agent_workdir
        self._history_store: AgentHistoryStore = AgentHistoryStore(self.gt_agent.id or 0)
        self.tool_registry: AgentToolRegistry = AgentToolRegistry()
        self.status: AgentStatus = AgentStatus.IDLE
        self.current_task: Optional[GtAgentTask] = None
        self.driver = build_agent_driver(self, driver_config or AgentDriverConfig(driver_type=DriverType.NATIVE))

    @property
    def _history(self) -> AgentHistoryStore:
        return self._history_store

    @property
    def is_active(self) -> bool:
        """检查 Agent 是否活跃（状态为 ACTIVE 或有正在处理的任务）。"""
        return self.status == AgentStatus.ACTIVE or self.current_task is not None

    async def has_pending_tasks(self) -> bool:
        """检查是否有待处理的任务。"""
        return await gtAgentTaskManager.has_pending_or_running_tasks(self.gt_agent.id)

    async def startup(self) -> None:
        await self.driver.startup()
        self.driver.mark_started()

    async def close(self) -> None:
        await self.driver.shutdown()
        self.driver.mark_stopped()
        self.tool_registry.clear()

    def _publish_status(self, status: AgentStatus) -> None:
        messageBus.publish(
            MessageBusTopic.AGENT_STATUS_CHANGED,
            event="agent_status",
            agent_id=self.gt_agent.id,
            agent_name=self.gt_agent.name,
            team_id=self.gt_agent.team_id,
            status=status,
        )

    async def consume_task(self, max_function_calls: int) -> None:
        """从数据库获取并处理任务，直到没有待处理任务为止。"""
        self.status = AgentStatus.ACTIVE
        self._publish_status(self.status)
        try:
            while True:
                # 从数据库获取第一个待处理任务
                task = await gtAgentTaskManager.get_first_pending_task(self.gt_agent.id)
                if task is None:
                    break  # 没有待处理任务了

                # 原子地认领任务（乐观锁）
                claimed_task = await gtAgentTaskManager.claim_task(task.id)
                if claimed_task is None:
                    # 任务已被其他消费者认领，继续尝试下一个
                    continue

                self.current_task = claimed_task
                task_succeeded = False
                last_error: Exception | None = None

                for attempt in range(1, MAX_INFER_RETRIES + 1):
                    try:
                        await self.run_chat_turn(claimed_task, max_function_calls)
                        task_succeeded = True
                        break
                    except Exception as e:
                        last_error = e
                        logger.warning(
                            f"Agent 任务执行失败 (第 {attempt}/{MAX_INFER_RETRIES} 次): agent_id={self.gt_agent.id}, task={claimed_task!r}, error={e}",
                            exc_info=(attempt == MAX_INFER_RETRIES),
                        )

                if task_succeeded is False:
                    logger.error(
                        f"Agent 推理连续失败 {MAX_INFER_RETRIES} 次，标记为 FAILED: agent_id={self.gt_agent.id}, last_error={last_error}"
                    )
                    # 更新任务状态为 FAILED
                    error_msg = str(last_error) if last_error else None
                    await gtAgentTaskManager.update_task_status(claimed_task.id, AgentTaskStatus.FAILED, error_message=error_msg)
                    self.status = AgentStatus.FAILED
                    self.current_task = None
                    self._publish_status(self.status)
                    return

                # 更新任务状态为 COMPLETED
                await gtAgentTaskManager.update_task_status(claimed_task.id, AgentTaskStatus.COMPLETED)
                self.current_task = None
        finally:
            if self.status != AgentStatus.FAILED:
                self.status = AgentStatus.IDLE
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
        await self._history.append_history_message(turn_context_message,
            stage=AgentHistoryStage.INPUT,
            tags=[AgentHistoryTag.ROOM_TURN_BEGIN],
        )
        return 1

    async def run_chat_turn(self, task: GtAgentTask, max_function_calls: int = 5) -> None:
        room_id = task.task_data.get("room_id")
        if room_id is None:
            logger.warning(f"run_chat_turn 跳过：task 缺少 room_id, agent_id={self.gt_agent.id}, task_id={task.id}")
            return

        room: ChatRoom | None = roomService.get_room(room_id)
        if room is None:
            logger.warning(f"run_chat_turn 跳过：room_id={room_id} 不存在, agent_id={self.gt_agent.id}")
            return

        synced_count = await self.pull_room_messages_to_history(room)
        try:
            if self.driver.host_managed_turn_loop:
                assert self.driver.started is True, f"driver 尚未启动: agent_id={self.gt_agent.id}"
                await self._run_chat_turn_with_host_loop(room, max_function_calls)
            else:
                await self.driver.run_chat_turn(task, synced_count, max_function_calls)
        except Exception as e:
            logger.warning(f"run_chat_turn 异常: agent_id={self.gt_agent.id}, room={room.key}, error={e}")
            raise

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
        context: ToolCallContext = ToolCallContext(
            agent_name=self.gt_agent.name,
            team_id=room.team_id,
            chat_room=room,
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
                    or room.state == RoomState.INIT
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

    async def _execute_tool(self) -> None:
        current_task = self.current_task
        assert current_task is not None, "current_task should not be None while executing tool"
        room_id = current_task.task_data.get("room_id")
        assert room_id is not None, "current_task should have room_id"
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

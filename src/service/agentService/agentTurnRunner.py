"""AgentTurnRunner: Turn 内部逻辑 — 消息同步、host loop、推理、工具调用编排。

同时实现 AgentDriverHost 协议，作为 Driver 的宿主。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Awaitable, Callable, List

from constants import (
    AgentActivityStatus, AgentActivityType,
    AgentHistoryStatus, AgentHistoryTag,
    ScheduleJobType, DriverType, ModelInput, OpenaiApiRole, RoomState, RoomType, SpecialAgent,
    TaskStatus, TurnStepResult,
)
from model.coreModel.agentModel import AgentContextUsage
from model.coreModel.gtCoreChatModel import GtCoreAgentDialogContext
from model.dbModel.gtRoomMessage import GtRoomMessage
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtAgentHistory import GtAgentHistory
from model.dbModel.agentMessage import AgentMessage
from model.dbModel.gtScheduleJob import GtScheduleJob
from model.dbModel.gtAgentTask import GtAgentTask
from model.dbModel.historyUsage import CompactStage, HistoryUsage
from service import agentActivityService, llmService, roomService, taskService
from service.agentActivityService import AgentActivityMeta
from service.agentService.agentHistoryStore import AgentHistoryStore
from service.agentService import compact, contextUsage, promptBuilder, visionFallback
from service.llmService.core import resolve_model
from service.agentService.driver import AgentDriverConfig, AgentTurnSetup
from service.agentService.driver.factory import build_agent_driver
from service.agentService.toolRegistry import AgentToolRegistry, RegisteredTool, ToolExecutionResult
from service.roomService import ChatRoom, ToolCallContext
from util import configUtil, llmApiUtil, sessionUtil
from util.assertUtil import assertNotNull
from dal.db import gtAgentHistoryManager, gtAgentManager, gtAgentTaskEventManager, gtAgentTaskManager

logger = logging.getLogger(__name__)

# 流式 token 进度的写库节流：每 200ms 或每 10 个 chunk 更新一次活动记录。
# 推理与上下文压缩共用同一套节流参数。
_PROGRESS_THROTTLE_INTERVAL_SECONDS = 0.2
_PROGRESS_THROTTLE_CHUNK_COUNT = 10


def _detect_json_tool_call_in_content(content: str | None) -> bool:
    """检测 LLM 是否将工具调用以 JSON 对象形式写入了 content 字段（而非 tool_calls）。"""
    if not content or not isinstance(content, str):
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
        prompt_refresh_fn: Callable[[], Awaitable[str]] | None = None,
    ):
        self.gt_agent: GtAgent = gt_agent
        self.system_prompt: str = system_prompt
        self.agent_workdir: str = agent_workdir
        # 无参异步闭包：重新构建 System Prompt（含最新星标记忆快照）。
        # 为 None 时行为与引入该参数前**完全一致**（prompt 一旦构造就不刷新），
        # 这条「不传则不刷新」有专门的回归用例。
        self._prompt_refresh_fn: Callable[[], Awaitable[str]] | None = prompt_refresh_fn
        self._history: AgentHistoryStore = AgentHistoryStore(gt_agent.id or 0)
        self.tool_registry: AgentToolRegistry = AgentToolRegistry()
        self.driver = build_agent_driver(self, driver_config or AgentDriverConfig(driver_type=DriverType.NATIVE))
        self._current_room: ChatRoom | None = None
        self._current_job: GtScheduleJob | None = None
        # 手动 compact 的互斥标志。手动 compact 是**唯一**会「整体覆盖 _items」
        # （`reload_from_db`）又不在 turn 里面跑的路径，必须与 turn 互斥：
        # - 与 turn 互斥：`JobConsumer.consume()` 在取 job 前等它归位；
        # - 与另一次手动 compact 互斥：`execute_manual_compact` 进门检查并占用。
        # **不碰 `status`**：`status` 只是标签，`consume()` 进来无脑置 ACTIVE，
        # 拿它当锁会让「状态显示空闲但 Agent 正在干活」且 stop 被静默拒绝。
        self._compacting: bool = False

    @property
    def is_compacting(self) -> bool:
        """是否正在执行手动 compact。

        `JobConsumer.consume()` 据此在取 job 前等待，避免 turn 与 compact 抢同一份
        `_history`（compact 的 `reload_from_db` 会整体覆盖 `_items`，把这期间 turn
        在内存里 append 的消息抹掉）。
        """
        return self._compacting

    def try_acquire_manual_compact(self) -> bool:
        """检查并占用「手动 compact」临界区；已被占用返回 False。

        **这是同步方法，且调用方必须保证它与自己的前置检查之间没有 await**——
        「检查 + 置位」必须落在同一个不让出事件循环的同步段里，否则两个并发请求
        会双双通过检查。占用失败的调用方应立即拒绝请求，不得排队等待：
        排队会把「用户点了两次」变成「串行压了两次」。
        """
        if self._compacting:
            return False
        self._compacting = True
        return True

    def release_manual_compact(self) -> None:
        """释放临界区。幂等（重复调用不会出错，也不会误放别人的占用）。"""
        self._compacting = False

    async def _refresh_system_prompt(self) -> None:
        """从数据库重建 System Prompt（只在 compact 切新 session 后调用）。

        记忆写入是实时落库的，但**当前 session 的 prompt 故意不跟着变**：会话中途换
        System Prompt 会让大模型侧的 Prompt Cache 全部失效（step2 §1.2 原则 3）。
        所以刷新点只有一个——compact 切新 session 时。

        刷新失败只记日志、不抛：compact 已经成功（新 session 已建立），
        不能因为提示词刷新失败把整轮对话打挂；最坏情况是这一轮沿用旧快照。
        """
        if self._prompt_refresh_fn is None:
            return
        try:
            refreshed = await self._prompt_refresh_fn()
        except Exception:
            logger.warning(
                "刷新 System Prompt 失败，本轮沿用旧快照: agent_id=%s", self.gt_agent.id, exc_info=True,
            )
            return
        if refreshed:
            self.system_prompt = refreshed
            logger.info(
                "[memory-refresh] System Prompt 已按最新星标快照重建: agent_id=%s, chars=%d",
                self.gt_agent.id, len(refreshed),
            )

    @property
    def _session_id(self) -> str:
        """当前 session 的 id；compact 成功后自动切换到新 session。

        每次请求前现算，避免在 compact 前后持有过期值。
        推理前 history 非空且已落库（见 _infer_to_item 的 is_infer_ready 断言），
        agent 也必定已落库，因此 session 起点必然存在。
        """
        first_message_id = self._history.current_session_first_message_id
        assert self.gt_agent.id is not None, "生成 session id 时 agent 尚未落库"
        assert first_message_id is not None, "生成 session id 时 history 为空或首条消息未落库"
        return sessionUtil.build_agent_session_id(self.gt_agent.id, first_message_id)

    def _base_metadata(self, **extra) -> AgentActivityMeta:
        """构建活动记录 metadata，自动附加 task_room_id 与执行环境（§8）。

        环境信息放在这一个入口而不是每个调用点各自传：活动类型有十来个，
        漏一个就会出现「有的活动显示环境、有的不显示」这种前后不一致。
        """
        meta = AgentActivityMeta(
            task_room_id=self._current_room.room_id if self._current_room is not None else None,
            **extra,
        )
        return meta.merge_exec_env(self.driver.get_exec_env())

    def _make_progress_callback(self, activity_id: int):
        """生成流式 token 进度回调：把累计估算写进活动 metadata（节流）。

        推理与上下文压缩共用：两者都是持续输出的流式请求，前端活动行按
        `current_completion_tokens` 实时显示估算值。广播按 200ms 或 10 个 chunk
        节流；进度只是观测数据，`persist=False` 只广播不写库——最终值由收尾更新落库。

        回调自身带 `last_completion_tokens`：节流会把中间值丢掉，收尾时若网关没回
        usage，就用这里记下的**最新**估算值落库（否则「网关不回 usage」这个估算
        存在的唯一场景，库里反而一条 completion token 都没有）。
        """
        last_progress_time = time.monotonic()
        chunk_count_since_update = 0

        async def _on_progress(progress: llmService.InferStreamProgress) -> None:
            nonlocal last_progress_time, chunk_count_since_update
            # 每个 delta 都记（不受节流影响），供收尾兜底使用。
            if progress.current_completion_tokens is not None:
                _on_progress.last_completion_tokens = progress.current_completion_tokens
            chunk_count_since_update += 1
            now = time.monotonic()
            if (
                chunk_count_since_update >= _PROGRESS_THROTTLE_CHUNK_COUNT
                or (now - last_progress_time) >= _PROGRESS_THROTTLE_INTERVAL_SECONDS
            ):
                patch = AgentActivityMeta()
                patch.apply_progress(progress)
                await agentActivityService.update_activity_progress(activity_id, metadata_patch=patch, persist=False)
                last_progress_time = now
                chunk_count_since_update = 0

        _on_progress.last_completion_tokens = None
        return _on_progress

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
        """更新 activity 终态，并把执行环境 patch 为结束时的真实环境（§8）。

        一个 turn 内部可能 `enter_container`，活动开始时是宿主、结束时在容器里；
        只在开始写会在 UI 上留下「在容器里跑的活动显示宿主机」。
        """
        if activity_id is None:
            return
        patch = metadata_patch if metadata_patch is not None else AgentActivityMeta()
        await agentActivityService.update_activity_progress(
            activity_id,
            status=status,
            detail=detail,
            error_message=error_message,
            metadata_patch=patch.merge_exec_env(self.driver.get_exec_env()),
        )

    # ─── Turn 运行方法 ──────────────────────────────────────

    async def handle_cancel_turn(self) -> None:
        """人工取消当前 turn 的收尾逻辑：driver 清理 → history 清理。"""
        await self.driver.cancel_turn()
        if self._current_room is not None:
            self._current_room.cancel_current_turn()
        await self._history.finalize_cancel_turn()
        await agentActivityService.fail_started_activities(self.gt_agent.id, error_message="cancelled by user")

    async def _resolve_event_actor_name(self, actor_id: int) -> str:
        """事件 actor 的展示名：系统 / Operator / agent 的 display_name。"""
        if actor_id == SpecialAgent.SYSTEM.value:
            return "系统"
        if actor_id == SpecialAgent.OPERATOR.value:
            return "Operator"
        agent = await gtAgentManager.get_agent_by_id(actor_id)
        return agent.display_name if agent is not None else f"Agent#{actor_id}"

    async def _build_latest_task_event_line(self, agent_task_id: int) -> str | None:
        """取该任务最后一条**非认领**事件，渲染为通知里「最近事件」的单行文本（无则返回 None）。

        只读：仅查事件与 actor 展示名，不改状态、不触发唤醒/通知。
        """
        # exclude_claim：跳过「执行人认领」这条系统噪声。认领由 consumer 在 turn 开始前
        # 写入（`taskService.claim_task`），必然是本任务最新一条，会把创建人刚写的
        # 说明挤出通知——#64 / #83 / #86 都实际发生过这种遮盖。
        events = await gtAgentTaskEventManager.list_events(agent_task_id, limit=1, exclude_claim=True)
        if not events:
            return None
        event = events[0]
        return promptBuilder.build_task_event_line(
            actor_name=await self._resolve_event_actor_name(event.actor_id),
            from_status=event.from_status,
            to_status=event.to_status,
            content=event.content,
            created_at=event.created_at,
        )

    def _needs_task_attention(self, agent_task: GtAgentTask) -> bool:
        """复核门：当前 agent 此刻是否仍需处理该任务。

        - 执行人（assignee）且任务处于 `TODO` / `IN_PROGRESS` → 需要（执行通知）；
        - 验收人（reviewer）且任务处于 `REVIEWING` → 需要（验收通知）；
        - 其余情况（终态 / 暂缓 / 已改派 / 已是无关人员）→ 不需要，跳过本回合以免空转唤醒。

        验收人判定复用 `taskService.is_task_reviewer`（全仓单一口径，不另写一套）。
        """
        if agent_task.assignee_id == self.gt_agent.id and agent_task.status in (TaskStatus.TODO, TaskStatus.IN_PROGRESS):
            return True

        return taskService.is_task_reviewer(agent_task, self.gt_agent.id) and agent_task.status == TaskStatus.REVIEWING

    async def run_task_turn(self, job: GtScheduleJob) -> None:
        """执行一个完整 chat turn：同步消息 → 推理 → 工具调用循环。

        支持两种模式：
        - ROOM_MESSAGE：从房间同步消息后运行 turn loop，完成后刷新房间消息队列。
        - TODO_TASK：向 history 注入任务通知 prompt 后运行 turn loop，无房间依赖。
        若存在未完成 turn，则走续跑路径。
        """
        is_todo_task = job.job_type == ScheduleJobType.TODO_TASK

        if is_todo_task:
            agent_task_id = job.task_data.get("agent_task_id")
            assertNotNull(agent_task_id, error_message=f"job 缺少 agent_task_id, agent_id={self.gt_agent.id}, job_id={job.id}")
            agent_task = await gtAgentTaskManager.get_task(agent_task_id)
            assertNotNull(agent_task, error_message=f"agent_task_id={agent_task_id} 不存在, agent_id={self.gt_agent.id}")
            if not self._needs_task_attention(agent_task):
                # 复核门：调度记录不随任务状态失效，任务已终态 / 已流转 / 已改派时直接结束本回合，
                # 不注入通知、不调用 LLM（调用方会把调度记录正常置为 COMPLETED）。
                logger.info(
                    f"协作任务无需处理，跳过本回合: {self.gt_agent.name}(agent_id={self.gt_agent.id}), "
                    f"agent_task_id={agent_task_id}, status={agent_task.status.value}, "
                    f"assignee_id={agent_task.assignee_id}, reviewer_id={agent_task.reviewer_id}"
                )
                return
            logger.info(f"协作任务 turn 开始: {self.gt_agent.name}(agent_id={self.gt_agent.id}), agent_task_id={agent_task_id}, title={agent_task.title!r}")
            room = None
        else:
            room_id = job.task_data.get("room_id")
            assertNotNull(room_id, error_message=f"job 缺少 room_id, agent_id={self.gt_agent.id}, job_id={job.id}")
            room = roomService.get_room(room_id)
            assertNotNull(room, error_message=f"room_id={room_id} 不存在, agent_id={self.gt_agent.id}")
            assert room.state != RoomState.INIT, (
                f"Agent 不应在 INIT 状态下收到任务: agent_id={self.gt_agent.id}, room={room.name}, state={room.state}"
            )

        self._current_room = room
        self._current_job = job
        try:
            if self.driver.host_managed_turn_loop:
                assert self.driver.started is True, f"driver 尚未启动: agent_id={self.gt_agent.id}"

                if not self._history.has_active_turn():
                    if is_todo_task:
                        task_prompt = promptBuilder.build_todo_task_turn_prompt(
                            task_id=agent_task.id,
                            title=agent_task.title,
                            description=agent_task.description,
                            status_value=agent_task.status.value,
                            has_reviewer=agent_task.reviewer_id is not None,
                            # 取消权限单一口径见 `taskService.can_cancel`（创建人或验收人）。
                            can_cancel=taskService.can_cancel(agent_task, self.gt_agent.id),
                            latest_event=await self._build_latest_task_event_line(agent_task.id),
                        )
                        await self._history.append_history_message(GtAgentHistory.build(
                            AgentMessage(role=OpenaiApiRole.USER, content=task_prompt),
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
                        synced_count = await self.collect_room_messages(room, as_update=False)
                        if synced_count == 0:
                            logger.info(f"无新消息，自动跳过本轮: {self.gt_agent.name}(agent_id={self.gt_agent.id}), room={room.name}")
                            await room.handle_finish_request(self.gt_agent.id)
                            await self._wake_next_turn_if_unread(room)
                            return

                # 本轮消息已注入 history、尚未开始推理：此刻广播一次，
                # 让面板在「推理前的等待时间」里就能看到上下文涨了。
                self._publish_context_usage()

                await self._run_turn_loop(room)
                if room is not None:
                    await self._wake_next_turn_if_unread(room)

            else:
                synced_count = 1 if is_todo_task else await self.collect_room_messages(room, as_update=False)
                await self.driver.run_task_turn(job, synced_count)

        except asyncio.CancelledError:
            if not is_todo_task and room is not None:
                room.cancel_current_turn()
            raise
        finally:
            self._current_room = None
            self._current_job = None

    async def collect_room_messages(self, room: ChatRoom, *, as_update: bool) -> int:
        """统一的房间消息收取入口：拉取未读 → 过滤自身 → 构造 prompt 写入 history。

        `as_update` 决定语义，两个触发点共用同一套拉取与游标推进逻辑：

        - `False`（Turn 开始）：构造「本轮开始」的 prompt，带 `ROOM_TURN_BEGIN` 标签。
        - `True`（安全边界插话）：构造「更新」prompt，**不带** turn 标签，
          避免把一次中途插话伪装成又一个 turn 开始。

        返回 1 表示「追加了条目」，0 表示「没有他人消息」（游标照常推进）。
        尾唤醒与安全边界两种触发对返回值的期望不同，故必须显式返回。
        """
        new_msgs: List[GtRoomMessage] = await room.get_unread_messages(self.gt_agent.id)

        # 游标已推进，先把最新读游标广播出去：前端据此把消息从浮动待读区移入主流。
        # 放在「有无他人消息」判断之前，因为即便全是自己的消息，游标也确实前进了。
        if new_msgs:
            room.publish_read_status()

        own_count = sum(1 for msg in new_msgs if msg.sender_id == self.gt_agent.id)
        logger.info(f"同步房间消息: agent={self.gt_agent.name}(agent_id={self.gt_agent.id}), room={room.name}, raw={len(new_msgs)}, own={own_count}, others={len(new_msgs) - own_count}")

        others = [m for m in new_msgs if m.sender_id != self.gt_agent.id]
        if not others:
            return 0

        if as_update:
            prompt = promptBuilder.build_turn_update_prompt(room.name, new_msgs, self.gt_agent.id)
            await self._history.append_history_message(
                GtAgentHistory.build(AgentMessage(role=OpenaiApiRole.USER, content=prompt))
            )
        else:
            prompt = promptBuilder.build_turn_begin_prompt_from_messages(room.name, new_msgs, self.gt_agent.id)
            await self._history.append_history_message(GtAgentHistory.build(
                AgentMessage(role=OpenaiApiRole.USER, content=prompt),
                tags=[AgentHistoryTag.ROOM_TURN_BEGIN],
            ))

        meta = self._base_metadata(messages=[{"sender": m.sender_display_name, "content": m.content} for m in others])
        await agentActivityService.add_activity(
            gt_agent=self.gt_agent,
            activity_type=AgentActivityType.MESSAGE_RECEIVED,
            status=AgentActivityStatus.SUCCEEDED,
            metadata=meta,
        )
        return 1

    async def _wake_next_turn_if_unread(self, room: ChatRoom | None) -> None:
        """Turn 结束后，若私聊控制房间仍有未读消息，唤醒下一轮（替代旧 flush_queued_messages）。

        三个限定条件，每个都有对应的事故背景：

        - `RoomType.PRIVATE`：群聊一轮结束后会推进到下一位 AI，state 仍是 SCHEDULING、
          current_turn_agent_id 是下一位 AI。此时调 handle_finish_request(OPERATOR)
          必然触发 `caller != current_id` 告警，纯噪声。
        - `state == RoomState.IDLE`：只有私聊单 AI 说完切回 IDLE 时，由 OPERATOR
          唤醒才正好进入下一轮调度；房间还在 SCHEDULING 说明后继有人接手，不该插队。
        - `has_unread_messages`：只对「还有正经消息没读」唤醒。若只按
          `insert_immediately` 唤醒，Agent 发完言后自己那条消息会把自己再叫醒一次（空转）。

        唤醒触发者传「房间内除自己外的另一成员」而非恒传 OPERATOR：
        双 AI 单聊 `[A, B]` 里 `handle_finish_request` 会把触发者记为 `_last_speaker_id` 并跳过，
        传 OPERATOR 会跳过 OPERATOR（不在房间里），于是从 index=0 开始 → 误唤醒发送方 A。
        传另一成员后，跳过它正好落到自己，`current_turn_agent_id` 为接收方 B。
        """
        if (
            room is not None
            and room.room_type == RoomType.PRIVATE
            and room.state == RoomState.IDLE
            and room.has_unread_messages(self.gt_agent.id)
        ):
            other_id = next(
                (aid for aid in room.get_agent_ids() if aid != self.gt_agent.id),
                room.OPERATOR_MEMBER_ID,
            )
            await room.handle_finish_request(other_id)

    async def _inject_immediate_messages(self, room: ChatRoom) -> None:
        """安全边界插话：把该房间当前所有未读消息 **按 seq 顺序一次性**交给 Agent。

        不是「只取 immediate 那几条」——触发源是 immediate，但收取范围是全部未读。
        否则会出现「先发的普通消息 M1 被跳过、后发的 immediate M2 先被读到」，
        正是本次要消灭的乱序。
        """
        await self.collect_room_messages(room, as_update=True)

    async def _run_turn_loop(self, room: ChatRoom | None) -> None:
        """基于 history 状态推进的统一循环。room 为 None 时跳过房间相关操作（协作任务模式）。"""
        tools = self.tool_registry.export_openai_tools()
        turn_setup: AgentTurnSetup = self.driver.turn_setup
        failed_action_count = 0
        next_tool_choice: str | None = None

        while True:
            # 检查该 Agent 所在的所有已激活单聊房间是否有未读的立即注入消息
            # （含 Operator 控制房，它在 get_private_rooms_for_agent 里排首位）。
            # 注意：触发条件只看 immediate（'什么时候读'），但 _inject_immediate_messages
            # 收取的是该房间的全部未读（'读哪些'）。两者刻意分开，见该方法 docstring。
            if self._history.is_safe_for_immediate_insert():
                for priv_room in roomService.get_private_rooms_for_agent(self.gt_agent.team_id, self.gt_agent.id):
                    if priv_room.has_unread_immediate_messages(self.gt_agent.id):
                        await self._inject_immediate_messages(priv_room)

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
                        AgentMessage(role=OpenaiApiRole.USER, content=turn_setup.hint_prompt),
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
                        AgentMessage(role=OpenaiApiRole.USER, content=hint),
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
        if _detect_json_tool_call_in_content(assistant_message.text_content()):
            for tc in (assistant_message.tool_calls or []):
                await self._history.append_history_message(GtAgentHistory.build(
                    AgentMessage.tool_result(tc.id, '{"success": false, "message": "工具调用被跳过：模型输出格式异常"}'),
                    status=AgentHistoryStatus.FAILED,
                    error_message="工具调用被跳过：模型输出格式异常",
                ))
            return TurnStepResult.LLM_OUTPUT_ERROR
        elif assistant_message.tool_calls:
            return TurnStepResult.LLM_OUTPUT_TOOL_CALLS
        else:
            return TurnStepResult.LLM_OUTPUT_NO_ACTION

    async def _vision_fallback_recognize(
        self,
        history: AgentHistoryStore,
        *,
        supports_vision: bool,
    ) -> None:
        """视觉兜底：主模型不支持视觉时，为无 caption 的图片附件识别并写 caption（含持久化）。

        场景 B 才触发：主模型不支持视觉 + vision 槽位已配 + auto_vision_fallback 开启。
        已有 caption 的图片跳过（去重）；识别失败降级（caption 保持为空），不阻塞主流程。
        """
        if supports_vision:
            return
        setting = configUtil.get_app_config().setting
        if not setting.auto_vision_fallback:
            return
        if not setting.default_models.vision:
            return
        for history_id, msg in history.get_messages_since_compact():
            for att in (getattr(msg, "attachments", None) or []):
                if att.kind != "image":
                    continue
                if att.recognition and att.recognition.strip():
                    continue  # 已有识别结果（之前识别过），跳过；caption 引导语不影响识别
                try:
                    att.recognition = await visionFallback.recognize(att, session_id=self._session_id)
                except Exception:
                    # 降级：recognition 保持为空，由 llmService 转换层插入"无法读取图片"说明
                    logger.exception("vision fallback recognize failed, 保持 recognition 为空（降级处理）")
                    continue
                if history_id is not None:
                    # message 为 PydanticJsonField，整条序列化落库（含 recognition）
                    await gtAgentHistoryManager.update_agent_history_by_id(history_id, message=msg)
                logger.info(
                    "vision fallback recognize ok: history_id=%s, mime_type=%s, recognition_len=%d",
                    history_id, att.mime_type, len(att.recognition or ""),
                )

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

        provider_config, merged_llm_config = resolve_model(self.gt_agent.model)
        trigger_tokens = compact.calc_compact_trigger_tokens(merged_llm_config)
        hard_limit_tokens = compact.calc_hard_limit_tokens(merged_llm_config)
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

        def _build_request_retry_meta_for_terminal() -> AgentActivityMeta | None:
            """把请求重试状态转成终态 metadata；从未发生重试时返回 None。

            定义在 try 之外：异常处理路径也需要它，避免局部函数未定义时掩盖原始异常。
            """
            if request_retry_state["seen"] is not True:
                return None
            return AgentActivityMeta(
                request_state="",
                retry_delay_seconds=0,
                retry_attempt=int(request_retry_state["attempt"]) if request_retry_state["attempt"] is not None else 0,
                retry_max_attempts=int(request_retry_state["max_attempts"]) if request_retry_state["max_attempts"] is not None else 0,
                last_retry_error=str(request_retry_state["last_retry_error"]) if request_retry_state["last_retry_error"] is not None else "",
            )

        model_display = f"{merged_llm_config.name}@{provider_config.name}"
        supports_vision = ModelInput.IMAGE in (merged_llm_config.input or [])

        try:
            messages = history.build_infer_messages()
            estimated_tokens = compact.estimate_tokens(merged_llm_config.name, messages, self.system_prompt)

            messages, estimated_tokens, pre_compact_triggered = await self._check_compact(
                messages,
                trigger_prompt_tokens=estimated_tokens,
                estimated_tokens=estimated_tokens,
                check_stage="pre-check",
            )
            if pre_compact_triggered:
                compact_stage = "pre"

            # 场景 B：主模型不支持视觉时，先识别无 caption 的图片写 caption（含显式持久化）
            await self._vision_fallback_recognize(history, supports_vision=supports_vision)

            # 活动记录：LLM_INFER STARTED（pre-check compact 已完成，不会与 COMPACT 并行）
            activity = await agentActivityService.add_activity(
                gt_agent=self.gt_agent, activity_type=AgentActivityType.LLM_INFER,
                metadata=self._base_metadata(model=model_display, estimated_prompt_tokens=estimated_tokens),
            )
            activity_id = activity.id

            ctx = GtCoreAgentDialogContext(
                system_prompt=self.system_prompt, messages=messages, tools=tools, tool_choice=tool_choice,
                session_id=self._session_id,
            )

            # 流式推理 + 节流更新
            _on_progress = self._make_progress_callback(activity_id)

            async def _on_status_event(event: llmService.InferRequestStatusEvent) -> None:
                request_retry_state["seen"] = True
                request_retry_state["attempt"] = event.attempt
                request_retry_state["max_attempts"] = event.max_attempts
                request_retry_state["last_retry_error"] = event.error_message
                patch = AgentActivityMeta()
                patch.apply_request_status_event(event)
                await agentActivityService.update_activity_progress(activity_id, metadata_patch=patch)

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

                    estimated_tokens = await self._execute_compact(pre_compact_tokens=estimated_tokens)
                    messages = history.build_infer_messages()
                    if estimated_tokens >= hard_limit_tokens:
                        raise RuntimeError(f"overflow compact 后仍超限: agent_id={self.gt_agent.id}") from error

                    # 新建 infer 活动记录
                    retry_metadata = self._base_metadata(
                        model=model_display, estimated_prompt_tokens=estimated_tokens, overflow_retry=True,
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
                    # 新活动 id 需要重新绑定进度回调（旧的仍指向已 FAILED 的推理活动）
                    _on_progress = self._make_progress_callback(activity_id)

                    # compact 已成功，session 已切换：此处现算即为新 session 的 id
                    ctx = GtCoreAgentDialogContext(
                        system_prompt=self.system_prompt, messages=messages, tools=tools, tool_choice=tool_choice,
                        session_id=self._session_id,
                    )
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
                message=AgentMessage.from_openai(assistant_message),
                status=AgentHistoryStatus.SUCCESS,
                usage=usage_data,
            )
            assistant_committed = True

            # 活动记录：LLM_INFER SUCCEEDED
            final_meta = AgentActivityMeta()
            final_meta.apply_usage(usage)
            # 网关不回 usage 时（`apply_usage` 什么都不写），用流中估算的终值兜底落库。
            # 不加这一条，「不回 usage」这个场景里库里一个 completion token 都没有——
            # 而「按 delta 估算」正是为它做的。流中值只广播不落库（persist=False），
            # 不在这里落一次就永久丢了：刷新页面 / 新窗口从 DB 读，显示为空。
            # 键沿用 `current_completion_tokens`：前端 `activityMetaTokens` 读的就是它
            # （`final_total_tokens` 优先被读，有 usage 时不需要这里兜底）。
            if usage is None and _on_progress.last_completion_tokens is not None:
                final_meta.current_completion_tokens = _on_progress.last_completion_tokens
            request_retry_meta = _build_request_retry_meta_for_terminal()
            if request_retry_meta is not None:
                final_meta.request_state = request_retry_meta.request_state
                final_meta.retry_attempt = request_retry_meta.retry_attempt
                final_meta.retry_max_attempts = request_retry_meta.retry_max_attempts
                final_meta.retry_delay_seconds = request_retry_meta.retry_delay_seconds
                final_meta.last_retry_error = request_retry_meta.last_retry_error
            await self._finish_activity(activity_id, status=AgentActivityStatus.SUCCEEDED, metadata_patch=final_meta)

            # 上下文用量：优先用模型返回的真实 `usage.prompt_tokens`（比估算准），
            # 没有 usage 时退回估算。放在活动收尾之后，广播失败不影响推理结果。
            self._publish_context_usage(
                used_tokens=usage.prompt_tokens if usage and usage.prompt_tokens is not None else None,
            )

            # 活动记录：思考内容和直接发言
            if assistant_message.reasoning_content and assistant_message.reasoning_content.strip():
                await agentActivityService.add_activity(
                    gt_agent=self.gt_agent, activity_type=AgentActivityType.REASONING,
                    status=AgentActivityStatus.SUCCEEDED, detail=assistant_message.reasoning_content,
                    metadata=self._base_metadata(),
                )
            assistant_text = assistant_message.text_content()
            if assistant_text and assistant_text.strip():
                await agentActivityService.add_activity(
                    gt_agent=self.gt_agent, activity_type=AgentActivityType.CHAT_REPLY,
                    status=AgentActivityStatus.SUCCEEDED, detail=assistant_text,
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

            final_message = AgentMessage.tool_result(
                output_item.tool_call_id,
                json.dumps({"success": False, "message": error_msg}, ensure_ascii=False),
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
                final_message = AgentMessage.tool_result(
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
            schedule_job=self._current_job,
        )
        exec_result:ToolExecutionResult = await self.tool_registry.execute_tool_call(tool_call, context)

        # ── 工具结果写入历史（read_image 图片挂附件，拆分/排序由 llmService 发送时处理）──
        # AgentMessage.from_tool_result() 把结果转成一条 TOOL 消息；图片结果把图片挂到
        # attachments 上（content 剥离 base64）。turn runner 只负责写入，不做拆分。
        final_message = AgentMessage.from_tool_result(exec_result.tool_call_id, exec_result.result)
        await self._history.finalize_history_item(
            history_id=output_item.id,
            message=final_message,
            status=AgentHistoryStatus.SUCCESS if exec_result.success else AgentHistoryStatus.FAILED,
            error_message=exec_result.error_message,
            tags=[AgentHistoryTag.ROOM_TURN_FINISH] if (registered_tool.marks_turn_finish and exec_result.success) else None,
        )
        if final_message.attachments:
            logger.info(
                "read_image 图片结果已记录: agent_id=%d, tool=%s, mime=%s, file=%s",
                self.gt_agent.id, tool_name, final_message.attachments[0].mime_type, exec_result.result.get("file_path"),
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
        messages: list[AgentMessage],
        *,
        trigger_prompt_tokens: int,
        estimated_tokens: int,
        check_stage: str,
    ) -> tuple[list[AgentMessage], int, bool]:
        """在指定检查阶段检测 prompt token，必要时执行 compact。

        Returns: (messages, estimated_tokens, compact_triggered)
        """
        provider_config, merged_llm_config = resolve_model(self.gt_agent.model)
        trigger_tokens = compact.calc_compact_trigger_tokens(merged_llm_config)
        hard_limit_tokens = compact.calc_hard_limit_tokens(merged_llm_config)
        if trigger_prompt_tokens < trigger_tokens:
            return messages, estimated_tokens, False

        logger.info(
            f"{check_stage} compact 触发: {self.gt_agent.name}(agent_id={self.gt_agent.id}), "
            f"prompt_tokens={trigger_prompt_tokens}, trigger={trigger_tokens}"
        )
        post_compact_tokens = await self._execute_compact(pre_compact_tokens=trigger_prompt_tokens)

        messages = self._history.build_infer_messages()
        msg_summary = ", ".join(f"{m.role}:{len(m.content or '') if not m.tool_calls else 'TC'}" for m in messages)
        logger.info(
            "[compact-recheck] agent_id=%d, message_count=%d, messages=[%s]",
            self.gt_agent.id, len(messages), msg_summary,
        )
        # compact 内部已按裁剪后的历史算过新值，直接用（与 messages 同源）。
        estimated_tokens = post_compact_tokens
        if estimated_tokens >= hard_limit_tokens:
            raise RuntimeError(
                f"{check_stage} compact 后仍超限: agent_id={self.gt_agent.id}, "
                f"estimated={estimated_tokens}, hard_limit={hard_limit_tokens}"
            )

        # 压缩完成且刚算过新值：直接广播，不重复跑 tokenizer。
        self._publish_context_usage(used_tokens=estimated_tokens)

        return messages, estimated_tokens, True

    async def _execute_compact(self, *, pre_compact_tokens: int | None = None) -> int:
        """执行一次 compact：生成摘要 → 插入 COMPACT_SUMMARY → 内存裁剪。失败时 raise。

        Args:
            pre_compact_tokens: 压缩前的上下文估算。调用方通常已经算过（触发判定），
                直接传进来可避免重复跑 tokenizer；省略时这里补算一次。

        Returns:
            压缩后的上下文 token 估算。调用方复用它广播用量，不再重复跑 tokenizer。
        """
        _, merged_llm_config = resolve_model(self.gt_agent.model)

        if pre_compact_tokens is None:
            pre_compact_tokens = compact.estimate_tokens(
                merged_llm_config.name, self._history.build_infer_messages(), self.system_prompt,
            )

        compact_activity = await agentActivityService.add_activity(
            gt_agent=self.gt_agent, activity_type=AgentActivityType.COMPACT,
            metadata=self._base_metadata(context_tokens_before=pre_compact_tokens),
        )

        compact_plan = self._history.build_compact_plan()
        if compact_plan is None:
            logger.warning("compact 跳过：无可压缩消息, agent_id=%d", self.gt_agent.id)
            await self._finish_activity(compact_activity.id, status=AgentActivityStatus.FAILED, error_message="无可压缩消息")
            raise compact.NoCompactableHistoryError("compact 跳过：无可压缩消息")

        # 摘要 token 上限动态设为上下文长度的 10%，随模型配置自动伸缩
        compact_max_tokens = max(1, int(merged_llm_config.context_config.context_window_tokens * 0.1))
        try:
            # 摘要请求属于被压缩的旧 session：此处 _session_id 仍指向旧 session 的首条消息
            summary_text = await compact.compact_messages(
                messages=compact_plan.source_messages,
                system_prompt=self.system_prompt,
                model=self.gt_agent.model,
                tools=self.tool_registry.export_openai_tools(),
                max_tokens=compact_max_tokens,
                session_id=self._session_id,
                on_progress=self._make_progress_callback(compact_activity.id),
            )
        except Exception as e:
            error_detail = str(e)
            logger.warning("compact 失败: %s, agent_id=%d", error_detail, self.gt_agent.id)
            await self._finish_activity(compact_activity.id, status=AgentActivityStatus.FAILED, error_message=error_detail)
            raise

        await self._history.insert_compact_summary(
            AgentMessage(role=OpenaiApiRole.USER, content=summary_text),
            seq=compact_plan.insert_seq,
        )

        # 新 session 已建立：此刻刷新 System Prompt，让后续 token 估算与新 session 首次推理
        # 直接搭载最新星标快照（step2 §3.1）。放在 insert_compact_summary 之后、
        # 活动收尾之前——刷新失败不影响 compact 本身的成功状态。
        await self._refresh_system_prompt()

        # 压缩后的上下文估算：算一次后返回给调用方广播用量，同时写进活动记录，
        # 前端展示「压缩前 → 压缩后」。调用方复用返回值，不再重复跑 tokenizer。
        post_compact_tokens = compact.estimate_tokens(
            merged_llm_config.name, self._history.build_infer_messages(), self.system_prompt,
        )

        # 活动记录：COMPACT SUCCEEDED
        await self._finish_activity(
            compact_activity.id, status=AgentActivityStatus.SUCCEEDED,
            metadata_patch=AgentActivityMeta(context_tokens_after=post_compact_tokens),
        )
        return post_compact_tokens

    # ─── 上下文用量 ────────────────────────────────────────────

    def snapshot_context_usage(self) -> AgentContextUsage | None:
        """按内存态未压缩历史算一份占用快照；算不出来时 `None`。"""
        return contextUsage.calc_context_usage_from_history(
            model_name=self.gt_agent.model,
            items=list(self._history),
            system_prompt=self.system_prompt,
        )

    def _publish_context_usage(self, *, used_tokens: int | None = None) -> None:
        """算一次并广播；`used_tokens` 给定时跳过估算直接用它。

        统一从这里出去的原因见 `contextUsage` 模块注释：分子分母各算一份就会分叉。
        广播本身吞掉所有异常——**用量是观测指标，读不到指标不该让任务失败**。
        """
        try:
            usage = contextUsage.calc_context_usage_from_history(
                model_name=self.gt_agent.model,
                items=list(self._history),
                system_prompt=self.system_prompt,
            ) if used_tokens is None else contextUsage.calc_context_usage(
                model_name=self.gt_agent.model,
                messages=[],
                system_prompt=self.system_prompt,
                used_tokens=used_tokens,
            )
            contextUsage.publish_context_usage(
                team_id=self.gt_agent.team_id,
                agent_id=self.gt_agent.id,
                usage=usage,
            )
        except Exception as e:
            logger.warning("[context-usage] 广播失败（不影响主流程）: agent_id=%s, error=%s", self.gt_agent.id, e)

    async def execute_manual_compact(self) -> AgentContextUsage | None:
        """手动压缩入口：执行 compact 并返回压缩后的新用量。

        与自动路径共用 `_execute_compact`，区别是：
        1. 先重载历史，保证「用户点按钮」这一刻的压缩基准是最新的；
        2. 压缩后算一次新用量，**既作为接口返回值、也广播给其它打开的窗口**。

        压缩后的 token 估算由 `_execute_compact` 返回（与活动记录展示同一个值），
        这里不再重算；但手动路径没有后续的推理步骤，压缩完必须主动广播，
        否则前端只能等下一次推理才发现值变了。

        ## 并发前提（这条注释和实现同等重要）

        `reload_from_db()` 是**整体覆盖 `_items`**。它一旦与 turn 或另一次 compact
        交错，就会把对方刚写进内存的行抹掉；而 `insert_compact_summary` 走
        `on_conflict_ignore` + 按 `(agent_id, seq)` 回读，撞号不报错、静默取库里那行
        ⇒ 内存与库的摘要正文分叉（用户点完看到 A，刷新变 B）。

        所以本方法**必须**在 `_compacting` 临界区内运行：
        - 与 turn 互斥：`JobConsumer.consume()` 取 job 前等 `is_compacting` 归位；
        - 与另一次手动 compact 互斥：`Agent.compact_context()` 进门
          `try_acquire_manual_compact()` 占用，已占用即抛 `CompactInProgressError`
          （**拒绝，不排队**——排队等于让「点了两次」变成「压了两次」）。

        **下面的实现不在方法内获锁**：临界区由最外层 `Agent.compact_context()` 负责
        占用与释放（见该方法的注释——离接口层越近，将来被插入 await 破坏原子性的
        概率越低）。这里只断言前提成立：绕过临界区直接调用本方法，会与 turn 抢同一份
        `_history`，那种情况下宁可当场失败也不要静默产出分叉的数据。
        """
        if not self._compacting:
            raise RuntimeError(
                f"execute_manual_compact 必须在 _compacting 临界区内调用: agent_id={self.gt_agent.id}。"
                "绕过临界区会与 turn / 另一次 compact 争抢同一份 _history"
                "（reload_from_db 整体覆盖 _items）。"
            )
        await self._history.reload_from_db()
        post_compact_tokens = await self._execute_compact()

        # 复用 compact 返回的估算值，不再重算 tokenizer；messages 传空表示全部走 used_tokens。
        usage = contextUsage.calc_context_usage(
            model_name=self.gt_agent.model,
            messages=[],
            system_prompt=self.system_prompt,
            used_tokens=post_compact_tokens,
        )
        contextUsage.publish_context_usage(
            team_id=self.gt_agent.team_id,
            agent_id=self.gt_agent.id,
            usage=usage,
        )
        return usage

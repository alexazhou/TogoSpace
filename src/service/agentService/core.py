import asyncio
import json
import logging
import os
from typing import Any, List, Optional

from util import llmApiUtil, configUtil
from util.chatMessageFormat import build_turn_context_prompt, format_room_message
from model.coreModel.gtCoreChatModel import GtCoreAgentDialogContext, GtCoreChatMessage
from model.coreModel.gtCoreAgentEvent import GtCoreRoomMessageEvent
from model.dbModel.gtAgentHistory import GtAgentHistory
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtTeam import GtTeam
from model.dbModel.gtRoleTemplate import GtRoleTemplate
from service.agentService.driver import AgentDriverConfig, AgentTurnSetup, build_agent_driver, normalize_driver_config
from service.agentService.agentHistroy import AgentHistory
from service.agentService.toolRegistry import AgentToolRegistry, ToolExecutionResult
from service import llmService, funcToolService, roomService, messageBus, persistenceService
from dal.db import gtDeptManager, gtTeamManager, gtAgentManager, gtRoleTemplateManager
from service.roomService import ChatRoom, ToolCallContext
from peewee import IntegrityError
from exception import TeamAgentException
from constants import AgentHistoryTag, SpecialAgent, MessageBusTopic, MemberStatus, DriverType, EmployStatus

logger = logging.getLogger(__name__)

MAX_INFER_RETRIES = 3

_agents: dict[str, "Agent"] = {}
_team_ids: dict[str, int] = {}  # team_name -> team_id mapping

def _make_agent_key(team_name: str, agent_name: str) -> str:
    return f"{agent_name}@{team_name}"


def get_special_agent_by_id(agent_id: int | None) -> SpecialAgent | None:
    return SpecialAgent.value_of(agent_id)


async def load_team_ids_from_db() -> None:
    global _team_ids
    _team_ids = {team.name: team.id for team in await gtTeamManager.get_all_teams()}
    logger.info(f"Loaded team IDs: {_team_ids}")


class Agent:
    """AI Team Agent 实例：承载在特定团队中的身份和状态，driver 负责具体驱动实现。"""

    def __init__(
        self,
        name: str,
        team_name: str,
        system_prompt: str,
        model: str,
        driver_config: Optional[AgentDriverConfig] = None,
        template_name: str = "",
        team_workdir: str = "",
        workspace_root: str = "",
        agent_id: int = 0,
    ):
        self.name: str = name  # Agent 在团队中的昵称
        self.team_name: str = team_name
        self.template_name: str = template_name  # 所使用的角色模版名
        self.system_prompt: str = system_prompt
        self.model: str = model
        self.team_workdir: str = team_workdir
        self.workspace_root: str = workspace_root

        self._agent_id: int = agent_id
        self._history_store: AgentHistory = AgentHistory(agent_id)
        self.tool_registry: AgentToolRegistry = AgentToolRegistry()
        self.wait_task_queue: asyncio.Queue = asyncio.Queue()
        self.status: MemberStatus = MemberStatus.IDLE
        self.current_room: Optional[ChatRoom] = None
        self.driver = build_agent_driver(self, driver_config or AgentDriverConfig(driver_type=DriverType.NATIVE))

    @property
    def _history(self) -> AgentHistory:
        return self._history_store

    @property
    def team_id(self) -> int:
        return _team_ids.get(self.team_name, 0)

    @property
    def agent_id(self) -> int:
        return self._agent_id

    @property
    def key(self) -> str:
        return _make_agent_key(self.team_name, self.name)

    @property
    def is_active(self) -> bool:
        return self.status == MemberStatus.ACTIVE or not self.wait_task_queue.empty()

    async def startup(self) -> None:
        await self.driver.startup()
        self.driver.mark_started()

    async def close(self) -> None:
        await self.driver.shutdown()
        self.driver.mark_stopped()
        self.tool_registry.clear()

    def resume_failed(self) -> int:
        """清除 FAILED 状态，从队头任务读取 room_id 返回，供调用方触发续跑。"""
        if self.status != MemberStatus.FAILED:
            raise ValueError(f"Agent {self.key} 当前状态不是 FAILED（当前: {self.status.name}）")

        task: GtCoreRoomMessageEvent = self.wait_task_queue._queue[0]
        room_id: int = task.room_id
        self.status = MemberStatus.IDLE
        self._publish_status(self.status)
        return room_id

    def _publish_status(self, status: MemberStatus) -> None:
        messageBus.publish(
            MessageBusTopic.MEMBER_STATUS_CHANGED,
            member_name=self.name,
            team_id=self.team_id,
            team_name=self.team_name,
            status=status.name,
        )

    async def consume_task(self, max_function_calls: int) -> None:
        self.status = MemberStatus.ACTIVE
        self._publish_status(self.status)
        try:
            while self.wait_task_queue.empty() == False:
                task: Any = self.wait_task_queue._queue[0]  # peek，先不弹出

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
                            f"Agent 任务执行失败 (第 {attempt}/{MAX_INFER_RETRIES} 次): "
                            f"agent={self.key}, task={task!r}, error={e}",
                            exc_info=(attempt == MAX_INFER_RETRIES),
                        )


                if task_succeeded == False:
                    logger.error(
                        f"Agent 推理连续失败 {MAX_INFER_RETRIES} 次，标记为 FAILED: "
                        f"agent={self.key}, last_error={last_error}",
                    )

                    self.status = MemberStatus.FAILED
                    self._publish_status(self.status)
                    return  # 任务留在队头，等 resume 后重新消费

                # 成功后才弹出
                self.wait_task_queue.get_nowait()
                self.wait_task_queue.task_done()
        finally:
            if self.status != MemberStatus.FAILED:
                self.status = MemberStatus.IDLE
                self._publish_status(self.status)

    async def pull_room_messages_to_history(self, room: ChatRoom) -> int:
        new_msgs: List[GtCoreChatMessage] = await room.get_unread_messages(self.name)
        logger.info(f"同步房间消息: agent={self.key}, room={room.name}, count={len(new_msgs)}")

        message_blocks: list[str] = []
        for msg in new_msgs:
            if msg.sender_name == self.name:
                continue

            message_blocks.append(format_room_message(room.name, msg.sender_name, msg.content))

        if not message_blocks:
            return 0

        turn_context_message = llmApiUtil.OpenAIMessage.text(
            llmApiUtil.OpenaiLLMApiRole.USER,
            content=build_turn_context_prompt(room.name, message_blocks),
        )
        await self.append_history_message(
            turn_context_message,
            tags=[AgentHistoryTag.ROOM_TURN_BEGIN],
        )
        return 1

    async def run_chat_turn(self, room_id: int, max_function_calls: int = 5) -> None:
        room: ChatRoom | None = roomService.get_room(room_id)
        if room is None:
            logger.warning(f"run_chat_turn 跳过：room_id={room_id} 不存在, agent={self.key}")
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
            logger.warning(f"run_chat_turn 异常: agent={self.key}, room={room.key}, error={e}")
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

            if turn_setup.hint_prompt:
                await self.append_history_message(
                    llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiLLMApiRole.USER, turn_setup.hint_prompt)
                )

    async def _run_until_reply(
        self,
        tools: Optional[list[llmApiUtil.OpenAITool]],
        max_function_calls: int,
    ) -> bool:
        context: ToolCallContext = ToolCallContext(
            agent_name=self.name,
            team_name=self.team_name,
            chat_room=self.current_room,
        )
        for _ in range(max_function_calls):
            assistant_message: llmApiUtil.OpenAIMessage = await self._infer(tools)
            tool_calls: list[llmApiUtil.OpenAIToolCall] | None = assistant_message.tool_calls
            if not tool_calls:
                return False

            logger.info(f"检测到工具调用: agent={self.key}, count={len(tool_calls)}")
            turn_done = False
            for tool_call in tool_calls:
                exec_result: ToolExecutionResult = await self.tool_registry.execute_tool_call(tool_call, context)
                await self.append_history_message(
                    llmApiUtil.OpenAIMessage.tool_result(exec_result.tool_call_id, exec_result.result_json),
                    tags=exec_result.tags,
                )
                if exec_result.turn_finished:
                    turn_done = True

            if turn_done:
                return True

        logger.warning(f"达到最大函数调用次数: agent={self.key}, max={max_function_calls}")
        return False

    async def _infer(self, tools: Optional[list[llmApiUtil.OpenAITool]]) -> llmApiUtil.OpenAIMessage:
        self._history.assert_infer_ready(self.key)
        ctx = GtCoreAgentDialogContext(
            system_prompt=self.system_prompt,
            messages=self._history.export_openai_message_list(),
            tools=tools or None,
        )
        infer_result: llmService.InferResult = await llmService.infer(self.model, ctx)
        if infer_result.ok == False or infer_result.response is None:
            error_message = infer_result.error_message or "unknown inference error"
            raise RuntimeError(f"LLM 推理失败: agent={self.key}, error={error_message}") from infer_result.error

        response = infer_result.response
        assistant_message = response.choices[0].message
        await self.append_history_message(assistant_message)

        return assistant_message

    async def _execute_tool(self) -> None:
        last_msg: llmApiUtil.OpenAIMessage | None = self._history.get_last_assistant_message()
        if not last_msg or not last_msg.tool_calls:
            return

        for tool_call in last_msg.tool_calls:
            function: dict[str, Any] = tool_call.function if isinstance(tool_call.function, dict) else {}
            name = function.get("name", "")
            args = function.get("arguments", "")
            context: ToolCallContext = ToolCallContext(
                agent_name=self.name,
                team_name=self.team_name,
                chat_room=self.current_room,
                tool_name=name,
            )
            result_data: dict[str, Any] = await funcToolService.run_tool_call(args, context=context)
            result = json.dumps(result_data, ensure_ascii=False)
            tags: list[AgentHistoryTag] | None = None
            if name == "finish_chat_turn" and GtAgentHistory.is_tool_call_succeeded(result):
                tags = [AgentHistoryTag.ROOM_TURN_FINISH]

            await self.append_history_message(
                llmApiUtil.OpenAIMessage.tool_result(tool_call.id, result),
                tags=tags,
            )

    def dump_history_messages(self) -> List[GtAgentHistory]:
        return self._history.dump()

    def inject_history_messages(self, items: List[GtAgentHistory]) -> None:
        self._history.replace(items)

    async def append_history_message(
        self,
        message: llmApiUtil.OpenAIMessage,
        tags: list[AgentHistoryTag] | None = None,
    ) -> None:
        item: GtAgentHistory = self._history.append_message(message, tags=tags)
        await persistenceService.append_agent_history_message(item)


async def startup() -> None:
    global _agents, _team_ids
    _agents = {}
    _team_ids = {}


async def restore_state() -> None:
    """从数据库恢复所有 Agent 的历史消息。"""
    for agent in get_all_agents():
        items = await persistenceService.load_agent_history_message(agent.agent_id)
        if items:
            agent._history.replace(items)


async def _build_dept_context(team_id: int, agent_name: str) -> str:
    """查询 Agent 所在部门并格式化为系统提示注入块；不在任何部门时返回空字符串。"""
    agent_row = await gtAgentManager.get_agent(team_id, agent_name)
    if agent_row is None:
        return ""

    all_depts = await gtDeptManager.get_all_depts(team_id)
    if not all_depts:
        return ""

    # 找到 Agent 所在部门
    agent_dept = None
    for dept in all_depts:
        if agent_row.id in dept.agent_ids:
            agent_dept = dept
            break
    if agent_dept is None:
        return ""

    # 建立辅助映射
    dept_id_map = {d.id: d for d in all_depts}
    all_agents = await gtAgentManager.get_team_agents(team_id)
    agent_id_to_name: dict[int, str] = {m.id: m.name for m in all_agents}

    manager_name = agent_id_to_name.get(agent_dept.manager_id, "")
    other_agents = [
        agent_id_to_name[mid]
        for mid in agent_dept.agent_ids
        if mid in agent_id_to_name and agent_id_to_name[mid] != agent_name
    ]

    lines = ["---", "组织信息：", f"- 所在部门：{agent_dept.name}（{agent_dept.responsibility}）"]

    # 上级部门
    if agent_dept.parent_id is not None:
        parent = dept_id_map.get(agent_dept.parent_id)
        if parent is not None:
            parent_manager = agent_id_to_name.get(parent.manager_id, "")
            lines.append(f"- 上级部门：{parent.name}（主管：{parent_manager}）")

    # 本部门主管（自己是主管时省略）
    if manager_name and manager_name != agent_name:
        lines.append(f"- 本部门主管：{manager_name}")

    if other_agents:
        lines.append(f"- 本部门其他成员：{', '.join(other_agents)}")

    lines.append("---")
    return "\n".join(lines)


async def _create_team_agents(team_row: GtTeam, agent_rows: list[GtAgent], templates_by_id: dict[int, GtRoleTemplate], workspace_root: str | None = None) -> None:
    app_config = configUtil.get_app_config()
    base_prompt_tmpl = app_config.group_chat_prompt
    identity_prompt_tmpl = app_config.agent_identity_prompt
    default_model = llmService.get_default_model()
    resolved_workspace_root = workspace_root or app_config.setting.workspace_root
    assert resolved_workspace_root is not None, "workspace_root 未配置"

    team_name = team_row.name
    team_workdir = os.path.join(resolved_workspace_root, team_name)
    team_id = _team_ids.get(team_name, team_row.id)

    for agent_row in agent_rows:
        template = templates_by_id.get(agent_row.role_template_id)
        if template is None:
            logger.warning(f"角色模版不存在: agent={agent_row.name}, role_template_id={agent_row.role_template_id}，跳过创建")
            continue

        agent_name = agent_row.name
        template_name = template.name
        agent_specific_prompt = template.soul
        model_name = agent_row.model or template.model or default_model
        driver_config = normalize_driver_config(
            {
                "driver": agent_row.driver or template.driver,
                "allowed_tools": template.allowed_tools,
            }
        )
        dept_context = await _build_dept_context(team_id, agent_name) if team_id else ""

        identity_prompt = identity_prompt_tmpl.format(agent_name=agent_name, template_name=template_name)
        full_prompt = base_prompt_tmpl + "\n\n" + identity_prompt + "\n\n" + agent_specific_prompt
        if dept_context:
            full_prompt += "\n\n" + dept_context

        key = _make_agent_key(team_name, agent_name)
        agent = Agent(
            name=agent_name,
            team_name=team_name,
            system_prompt=full_prompt,
            model=model_name,
            driver_config=driver_config,
            template_name=template_name,
            team_workdir=team_workdir,
            workspace_root=resolved_workspace_root,
            agent_id=agent_row.id,
        )
        _agents[key] = agent
        logger.info(
            f"创建 Agent 实例: key={key}, template={template_name}, model={model_name}, driver={driver_config.driver_type}"
        )
        await agent.startup()


async def create_team_agents_from_db(workspace_root: str | None = None) -> None:
    await load_team_ids_from_db()
    for team_row in await gtTeamManager.get_all_teams():
        agent_rows = await gtAgentManager.get_team_agents(team_row.id)
        template_rows = await gtRoleTemplateManager.get_role_templates_by_ids(
            [agent.role_template_id for agent in agent_rows]
        )
        templates_by_id = {template.id: template for template in template_rows}
        await _create_team_agents(team_row, agent_rows, templates_by_id, workspace_root=workspace_root)


async def reload_team_agents_from_db(team_name: str, workspace_root: str | None = None) -> None:
    """按 Team 维度重建运行时 Agent 实例。"""
    team_suffix = f"@{team_name}"
    keys_to_remove = [k for k in _agents.keys() if k.endswith(team_suffix)]
    close_tasks: list[Any] = []
    for key in keys_to_remove:
        close_tasks.append(_agents[key].close())
    if close_tasks:
        await asyncio.gather(*close_tasks, return_exceptions=True)
    for key in keys_to_remove:
        _agents.pop(key, None)

    await load_team_ids_from_db()

    team_row = await gtTeamManager.get_team(team_name)
    if team_row is None:
        logger.warning(f"重建 Team Agent 失败: team '{team_name}' 不存在于配置中")
        return

    agent_rows = await gtAgentManager.get_team_agents(team_row.id)
    template_rows = await gtRoleTemplateManager.get_role_templates_by_ids(
        [agent.role_template_id for agent in agent_rows]
    )
    templates_by_id = {template.id: template for template in template_rows}
    await _create_team_agents(team_row, agent_rows, templates_by_id, workspace_root=workspace_root)


def get_team_agent(team_name: str, agent_name: str) -> "Agent":
    key = _make_agent_key(team_name, agent_name)
    return _agents[key]


def find_team_agent(team_name: str, agent_name: str) -> "Agent | None":
    key = _make_agent_key(team_name, agent_name)
    return _agents.get(key)


def get_all_agents() -> List["Agent"]:
    return list(_agents.values())


def get_team_agent_status_map(team_name: str) -> dict[int, MemberStatus]:
    return {
        agent.agent_id: agent.status
        for agent in _agents.values()
        if agent.team_name == team_name and agent.agent_id > 0
    }


def find_agent_by_id(agent_id: int) -> "Agent | None":
    return next((a for a in _agents.values() if a.agent_id == agent_id), None)


async def list_team_agents(team_id: int) -> list[GtAgent]:
    return await gtAgentManager.get_agents_by_employ_status(team_id, EmployStatus.ON_BOARD)

def get_team_agents(room_id: int) -> List["Agent"]:
    room = roomService.get_room(room_id)
    if room is None:
        return []
    members: List[str] = roomService.get_member_names(room_id)
    return [_agents[_make_agent_key(room.team_name, n)] for n in members if _make_agent_key(room.team_name, n) in _agents]


def get_all_rooms(team_name: str, agent_name: str) -> List[int]:
    return roomService.get_rooms_for_agent(_team_ids.get(team_name), agent_name)


async def overwrite_team_agents(team_id: int, agents_data: list[GtAgent]) -> list[GtAgent]:
    """全量覆盖成员列表：有 id 更新，无 id 创建，不在列表的设为离职状态。返回在职成员列表。"""
    existing_agents = await gtAgentManager.get_team_agents(team_id)
    existing_ids = {a.id for a in existing_agents}
    existing_by_id = {a.id: a for a in existing_agents}
    request_ids = {agent.id for agent in agents_data if agent.id is not None}

    # 1. 离职处理
    ids_to_offboard = existing_ids - request_ids
    if len(ids_to_offboard) > 0:
        await gtAgentManager.batch_update_agent_status(list(ids_to_offboard), EmployStatus.OFF_BOARD)

    # 2. 转换为 GtAgent 对象列表
    agents_to_save: list[GtAgent] = []
    for data in agents_data:
        agent_id = data.id

        if agent_id is not None:
            existing = existing_by_id.get(agent_id)
            if existing is None:
                raise TeamAgentException(
                    error_message=f"成员 ID 不存在于当前 team: {agent_id}",
                    error_code="member_not_found",
                )
            agent = existing
            agent.name = data.name
            agent.role_template_id = data.role_template_id
            agent.model = data.model or ""
            agent.driver = data.driver or DriverType.NATIVE
            agent.employ_status = EmployStatus.ON_BOARD
        else:
            agent = GtAgent(
                team_id=team_id,
                name=data.name,
                role_template_id=data.role_template_id,
                model=data.model or "",
                driver=data.driver or DriverType.NATIVE,
                employ_status=EmployStatus.ON_BOARD,
            )

        agents_to_save.append(agent)

    # 3. 批量保存
    try:
        await gtAgentManager.batch_save_agents(team_id, agents_to_save)
    except IntegrityError as e:
        raise TeamAgentException(
            error_message="成员保存失败，名称可能已存在或工号重复",
            error_code="MEMBER_SAVE_FAILED",
        ) from e

    return await gtAgentManager.get_agents_by_employ_status(team_id, EmployStatus.ON_BOARD)


async def overwrite_team_agent_employ_status(team_id: int, on_board_agent_ids: list[int] | set[int]) -> tuple[int, int]:
    """按团队成员全集同步在岗/离岗状态，返回 (on_board_count, off_board_count)。"""
    all_agents = await gtAgentManager.get_team_agents(team_id)
    on_board_set = set(on_board_agent_ids)
    on_board_ids = [agent.id for agent in all_agents if agent.id in on_board_set]
    off_board_ids = [agent.id for agent in all_agents if agent.id not in on_board_set]

    await gtAgentManager.batch_update_agent_status(on_board_ids, EmployStatus.ON_BOARD)
    await gtAgentManager.batch_update_agent_status(off_board_ids, EmployStatus.OFF_BOARD)

    return len(on_board_ids), len(off_board_ids)


async def shutdown() -> None:
    global _agents, _team_ids
    close_tasks: List[Any] = [a.close() for a in _agents.values()]
    if close_tasks:
        await asyncio.gather(*close_tasks, return_exceptions=True)
    _agents = {}
    _team_ids = {}

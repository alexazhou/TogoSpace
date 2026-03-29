import asyncio
import logging
import os
from typing import Any, List, Optional

from util import llmApiUtil, configUtil
from util.configTypes import TeamConfig, TeamRoomConfig
from model.coreModel.gtCoreChatModel import GtCoreAgentDialogContext, GtCoreChatMessage
from model.coreModel.gtCoreAgentEvent import GtCoreRoomMessageEvent
from model.coreModel.gtCoreWebModel import GtCoreAgentInfo
from model.dbModel.gtAgentHistory import GtAgentHistory
from service.agentService.driver import AgentDriverConfig, build_agent_driver, normalize_driver_config
from service import llmService, funcToolService, roomService, messageBus, persistenceService
from dal.db import gtDeptManager, gtTeamManager, gtAgentManager, gtRoleTemplateManager
from service.roomService import ChatRoom, ChatContext
from constants import SpecialAgent, MessageBusTopic, MemberStatus, DriverType

logger = logging.getLogger(__name__)

_agents: dict[str, "Agent"] = {}
_team_ids: dict[str, int] = {}  # team_name -> team_id mapping


def _make_agent_key(team_name: str, agent_name: str) -> str:
    return f"{agent_name}@{team_name}"


def _iter_team_rooms(team_config: TeamConfig) -> list[TeamRoomConfig]:
    return team_config.preset_rooms


async def load_team_ids(teams_config: list[TeamConfig]) -> None:
    """Load team_id for each team name."""
    global _team_ids
    _team_ids = {}
    for team in teams_config:
        team_name = team.name
        team_row = await gtTeamManager.get_team(team_name)
        if team_row:
            _team_ids[team_name] = team_row.id
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
    ):
        self.name: str = name  # Agent 在团队中的昵称
        self.team_name: str = team_name
        self.template_name: str = template_name  # 所使用的角色模版名
        self.system_prompt: str = system_prompt
        self.model: str = model
        self.team_workdir: str = team_workdir
        self.workspace_root: str = workspace_root

        self._agent_id: int = 0
        self._history: list[llmApiUtil.OpenAIMessage] = []
        self.wait_task_queue: asyncio.Queue = asyncio.Queue()
        self.status: MemberStatus = MemberStatus.IDLE
        self.current_room: Optional[ChatRoom] = None
        self.driver = build_agent_driver(self, driver_config or AgentDriverConfig(driver_type=DriverType.NATIVE))

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

    def get_info(self) -> GtCoreAgentInfo:
        return GtCoreAgentInfo(
            name=self.name,
            template_name=self.template_name or None,
            model=self.model,
            team_name=self.team_name,
            status=MemberStatus.ACTIVE if self.is_active else MemberStatus.IDLE,
        )

    async def startup(self) -> None:
        await self.driver.startup()

    async def close(self) -> None:
        await self.driver.shutdown()

    def _publish_status(self, status: MemberStatus) -> None:
        messageBus.publish(
            MessageBusTopic.MEMBER_STATUS_CHANGED,
            member_name=self.name,
            team_name=self.team_name,
            status=status.name,
        )

    async def consume_task(self, max_function_calls: int) -> None:
        self.status = MemberStatus.ACTIVE
        self._publish_status(self.status)
        try:
            while True:
                try:
                    task = self.wait_task_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

                try:
                    if isinstance(task, GtCoreRoomMessageEvent):
                        await self.run_chat_turn(task.room_id, max_function_calls)
                    else:
                        raise TypeError(f"不支持的任务类型: {type(task).__name__}")
                except Exception as e:
                    logger.error(f"Agent 处理任务失败: agent={self.key}, task={task!r}, error={e}", exc_info=True)
                finally:
                    self.wait_task_queue.task_done()
        finally:
            self.status = MemberStatus.IDLE
            self._publish_status(self.status)

    async def sync_room_messages(self, room: ChatRoom) -> int:
        new_msgs: List[GtCoreChatMessage] = await room.get_unread_messages(self.name)
        logger.info(f"同步房间消息: agent={self.key}, room={room.name}, count={len(new_msgs)}")

        synced_count = 0
        for msg in new_msgs:
            if msg.sender_name == self.name:
                continue

            message: llmApiUtil.OpenAIMessage
            if msg.sender_name == SpecialAgent.SYSTEM.name:
                message = llmApiUtil.OpenAIMessage.text(
                    llmApiUtil.OpenaiLLMApiRole.USER,
                    content=f"{room.name} 房间系统消息: {msg.content}",
                )
            else:
                message = llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiLLMApiRole.USER, content=f"{msg.sender_name} 在 {room.name} 房间发言: {msg.content}")

            await self.append_history_message(message)
            synced_count += 1

        return synced_count

    async def run_chat_turn(self, room_id: int, max_function_calls: int = 5) -> None:
        room = roomService.get_room(room_id)
        if room is None:
            logger.warning(f"run_chat_turn 跳过：room_id={room_id} 不存在, agent={self.key}")
            return
        self.current_room = room
        synced_count = await self.sync_room_messages(room)

        try:
            await self.driver.run_chat_turn(room, synced_count, max_function_calls)
        except Exception as e:
            logger.warning(f"run_chat_turn 异常: agent={self.key}, room={room.key}, error={e}")
            raise
        finally:
            self.current_room = None

    async def sync_room(self, room: ChatRoom) -> None:
        await self.sync_room_messages(room)

    async def _infer(self, tools: Optional[list[llmApiUtil.OpenAITool]]) -> llmApiUtil.OpenAIMessage:
        assert self._history and self._history[-1].role in (
            llmApiUtil.OpenaiLLMApiRole.USER,
            llmApiUtil.OpenaiLLMApiRole.TOOL,
            llmApiUtil.OpenaiLLMApiRole.SYSTEM,
        ), f"[{self.key}] _infer 前最后一条消息不能是 assistant，当前为: {self._history[-1].role if self._history else 'empty'}"
        ctx = GtCoreAgentDialogContext(system_prompt=self.system_prompt, messages=self._history, tools=tools or None)
        response: llmApiUtil.OpenAIResponse = await llmService.infer(self.model, ctx)
        assistant_message: llmApiUtil.OpenAIMessage = response.choices[0].message
        await self.append_history_message(assistant_message)

        return assistant_message

    async def _execute_tool(self) -> None:
        last_msg = self.get_last_assistant_message()
        if not last_msg or not last_msg.tool_calls:
            return

        for tool_call in last_msg.tool_calls:
            function = tool_call.function if isinstance(tool_call.function, dict) else {}
            name = function.get("name", "")
            args = function.get("arguments", "")
            context = ChatContext(agent_name=self.name, team_name=self.team_name, chat_room=self.current_room)
            result = await funcToolService.run_tool_call(name, args, context=context)
            await self.append_history_message(llmApiUtil.OpenAIMessage.tool_result(tool_call.id, result))

    def get_last_assistant_message(self, start_idx: int = 0) -> Optional[llmApiUtil.OpenAIMessage]:
        recent_history = self._history[start_idx:]

        for message in reversed(recent_history):
            if message.role == llmApiUtil.OpenaiLLMApiRole.ASSISTANT:
                return message

        return None

    def dump_history_messages(self) -> List[GtAgentHistory]:
        return [
            GtAgentHistory(
                agent_id=self.agent_id,
                seq=idx,
                message_json=msg.model_dump_json(exclude_none=True),
            )
            for idx, msg in enumerate(self._history)
        ]

    def inject_history_messages(self, items: List[GtAgentHistory]) -> None:
        self._history = [llmApiUtil.OpenAIMessage.model_validate_json(item.message_json) for item in items]

    async def append_history_message(self, message: llmApiUtil.OpenAIMessage) -> None:
        self._history.append(message)
        await self._persist_history_message(message)

    async def _persist_history_message(self, message: llmApiUtil.OpenAIMessage) -> None:
        seq: int = len(self._history) - 1
        item = GtAgentHistory(
            agent_id=self._agent_id,
            seq=seq,
            message_json=message.model_dump_json(exclude_none=True),
        )
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
            agent.inject_history_messages(items)


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
    all_agents = await gtAgentManager.get_agents_by_team(team_id)
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


async def create_team_agents(teams_config: list[TeamConfig], workspace_root: str | None = None) -> None:
    """创建团队 Agent 实例。"""
    app_config = configUtil.get_app_config()
    base_prompt_tmpl = app_config.group_chat_prompt
    default_model = llmService.get_default_model()
    resolved_workspace_root = workspace_root or app_config.setting.workspace_root
    assert resolved_workspace_root is not None, "workspace_root 未配置"

    for team_config in teams_config:
        team_name = team_config.name
        team_workdir = os.path.join(resolved_workspace_root, team_name)

        for member_cfg in team_config.members:
            agent_name = member_cfg.name
            template_name = member_cfg.role_template
            cfg = await gtRoleTemplateManager.get_role_template_by_name(template_name)
            if cfg is None:
                logger.warning(f"角色模版不存在: agent={agent_name}, template={template_name}，跳过创建")
                continue

            agent_specific_prompt = cfg.soul

            # model 覆盖：AgentConfig > RoleTemplate > default
            model_name = member_cfg.model or cfg.model or default_model

            # driver 覆盖：AgentConfig.driver 优先，否则用 RoleTemplate.driver
            if member_cfg.driver:
                driver_config = normalize_driver_config({"driver": member_cfg.driver})
            else:
                driver_config = normalize_driver_config(
                    {
                        "driver": cfg.driver,
                        "allowed_tools": cfg.allowed_tools,
                    }
                )

            # 部门上下文注入
            team_id = _team_ids.get(team_name, 0)
            dept_context = await _build_dept_context(team_id, agent_name) if team_id else ""

            full_prompt = base_prompt_tmpl + "\n\n" + agent_specific_prompt
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
            )
            _agents[key] = agent
            logger.info(
                f"创建 Agent 实例: key={key}, template={template_name}, model={model_name}, driver={driver_config.driver_type}"
            )
            await agent.startup()
            try:
                agent_row = await gtAgentManager.get_agent(agent.team_id, agent.name)
                if agent_row:
                    agent._agent_id = agent_row.id
            except Exception as e:
                logger.warning(f"写入 Agent 数据失败: agent={agent.key}, error={e}")


async def reload_team_agents(team_name: str, teams_config: list[TeamConfig], workspace_root: str | None = None) -> None:
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

    await load_team_ids(teams_config)

    target_config = next((cfg for cfg in teams_config if cfg.name == team_name), None)
    if target_config is None:
        logger.warning(f"重建 Team Agent 失败: team '{team_name}' 不存在于配置中")
        return

    await create_team_agents([target_config], workspace_root=workspace_root)


def get_team_agent(team_name: str, agent_name: str) -> "Agent":
    key = _make_agent_key(team_name, agent_name)
    return _agents[key]


def find_team_agent(team_name: str, agent_name: str) -> "Agent | None":
    key = _make_agent_key(team_name, agent_name)
    return _agents.get(key)


def get_all_agents() -> List["Agent"]:
    return list(_agents.values())


def get_team_agents(room_id: int) -> List["Agent"]:
    room = roomService.get_room(room_id)
    if room is None:
        return []
    members: List[str] = roomService.get_member_names(room_id)
    return [_agents[_make_agent_key(room.team_name, n)] for n in members if _make_agent_key(room.team_name, n) in _agents]


def get_all_rooms(team_name: str, agent_name: str) -> List[int]:
    return roomService.get_rooms_for_agent(_team_ids.get(team_name), agent_name)


async def shutdown() -> None:
    global _agents, _team_ids
    close_tasks: List[Any] = [a.close() for a in _agents.values()]
    if close_tasks:
        await asyncio.gather(*close_tasks, return_exceptions=True)
    _agents = {}
    _team_ids = {}

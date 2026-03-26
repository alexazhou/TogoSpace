import asyncio
import logging
import os
from typing import Any, Callable, Dict, List, Optional

from util import llmApiUtil, configUtil
from util.configTypes import AgentTemplate, TeamConfig, TeamRoomConfig
from model.coreModel.gtCoreChatModel import GtCoreMemberDialogContext, GtCoreChatMessage
from model.coreModel.gtCoreAgentEvent import GtCoreRoomMessageEvent
from model.coreModel.gtCoreWebModel import GtCoreMemberInfo
from model.dbModel.gtAgentHistory import GtAgentHistory
from .driver import AgentDriverConfig, build_agent_driver, normalize_driver_config
from service import llmService, funcToolService, roomService, messageBus, persistenceService
from dal.db import gtAgentManager
from service.roomService import ChatRoom, ChatContext
from constants import SpecialAgent, MessageBusTopic, MemberStatus

logger = logging.getLogger(__name__)

_agent_templates: Dict[str, AgentTemplate] = {}
_team_members: Dict[str, "TeamMember"] = {}
_team_ids: Dict[str, int] = {}  # team_name -> team_id mapping


def _make_member_key(team_name: str, member_name: str) -> str:
    return f"{member_name}@{team_name}"


def _iter_team_rooms(team_config: TeamConfig) -> list[TeamRoomConfig]:
    return team_config.preset_rooms


async def load_team_ids(teams_config: list[TeamConfig]) -> None:
    """Load team_id for each team name."""
    from dal.db import gtTeamManager
    global _team_ids
    _team_ids = {}
    for team in teams_config:
        team_name = team.name
        team_row = await gtTeamManager.get_team(team_name)
        if team_row:
            _team_ids[team_name] = team_row.id
    logger.info(f"Loaded team IDs: {_team_ids}")


class TeamMember:
    """AI Team Member 实例：承载在特定团队中的身份和状态，driver 负责具体驱动实现。"""

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
        self.name: str = name  # 成员在团队中的昵称
        self.team_name: str = team_name
        self.template_name: str = template_name  # 所使用的 Agent 模版名
        self.system_prompt: str = system_prompt
        self.model: str = model
        self.team_workdir: str = team_workdir
        self.workspace_root: str = workspace_root

        self._history: list[llmApiUtil.OpenAIMessage] = []
        self.wait_task_queue: asyncio.Queue = asyncio.Queue()
        self.status: MemberStatus = MemberStatus.IDLE
        self.current_room: Optional[ChatRoom] = None
        self.driver = build_agent_driver(self, driver_config or AgentDriverConfig(driver_type="native"))

    @property
    def team_id(self) -> int:
        return _team_ids.get(self.team_name, 0)

    @property
    def key(self) -> str:
        return _make_member_key(self.team_name, self.name)

    @property
    def is_active(self) -> bool:
        return self.status == MemberStatus.ACTIVE or not self.wait_task_queue.empty()

    def get_info(self) -> GtCoreMemberInfo:
        return GtCoreMemberInfo(
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
                    logger.error(f"成员处理任务失败: member={self.key}, task={task!r}, error={e}", exc_info=True)
                finally:
                    self.wait_task_queue.task_done()
        finally:
            self.status = MemberStatus.IDLE
            self._publish_status(self.status)

    async def sync_room_messages(self, room: ChatRoom) -> int:
        new_msgs: List[GtCoreChatMessage] = await room.get_unread_messages(self.name)
        logger.info(f"同步房间消息: member={self.key}, room={room.name}, count={len(new_msgs)}")

        synced_count = 0
        for msg in new_msgs:
            if msg.sender_name == self.name:
                continue

            message: llmApiUtil.OpenAIMessage
            if msg.sender_name == "system":
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
            logger.warning(f"run_chat_turn 跳过：room_id={room_id} 不存在, member={self.key}")
            return
        self.current_room = room
        synced_count = await self.sync_room_messages(room)

        try:
            await self.driver.run_chat_turn(room, synced_count, max_function_calls)
        except Exception as e:
            logger.warning(f"run_chat_turn 异常: member={self.key}, room={room.key}, error={e}")
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
        ctx = GtCoreMemberDialogContext(system_prompt=self.system_prompt, messages=self._history, tools=tools or None)
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
                team_id=self.team_id,
                agent_name=self.name,
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
            team_id=self.team_id,
            agent_name=self.name,
            seq=seq,
            message_json=message.model_dump_json(exclude_none=True),
        )
        await persistenceService.append_agent_history_message(item)


async def startup() -> None:
    global _agent_templates, _team_members
    _agent_templates = {}
    _team_members = {}


def load_agent_config(agents_config: List[AgentTemplate] | None = None) -> None:
    """加载 Agent 模版配置。"""
    global _agent_templates
    resolved = agents_config if agents_config is not None else configUtil.get_app_config().agents
    _agent_templates = {cfg.name: cfg for cfg in resolved}
    logger.info(f"加载 Agent 模版: {list(_agent_templates.keys())}")


async def create_team_members(teams_config: list[TeamConfig], workspace_root: str | None = None) -> None:
    """创建团队成员实例。"""
    base_prompt_tmpl = configUtil.load_prompt("src/prompts/GroupChat.md")
    default_model = llmService.get_default_model()
    resolved_workspace_root = workspace_root or configUtil.get_app_config().setting.workspace_root
    assert resolved_workspace_root is not None, "workspace_root 未配置"

    for team_config in teams_config:
        team_name = team_config.name
        if team_config.working_directory:
            team_workdir = team_config.working_directory
        else:
            team_workdir = os.path.join(resolved_workspace_root, team_name)

        for member_cfg in team_config.members:
            member_name = member_cfg.name
            template_name = member_cfg.agent
            if template_name not in _agent_templates:
                logger.warning(f"Agent 模版不存在: member={member_name}, agent={template_name}，跳过创建")
                continue

            cfg: AgentTemplate = _agent_templates[template_name]
            if cfg.system_prompt:
                agent_specific_prompt = cfg.system_prompt
            else:
                agent_specific_prompt = configUtil.load_prompt(cfg.prompt_file)

            full_prompt = base_prompt_tmpl + "\n\n" + agent_specific_prompt
            model_name = cfg.model or default_model
            key = _make_member_key(team_name, member_name)
            driver_config = normalize_driver_config(cfg)
            member = TeamMember(
                name=member_name,
                team_name=team_name,
                system_prompt=full_prompt,
                model=model_name,
                driver_config=driver_config,
                template_name=template_name,
                team_workdir=team_workdir,
                workspace_root=resolved_workspace_root,
            )
            _team_members[key] = member
            logger.info(
                f"创建成员实例: key={key}, template={template_name}, model={model_name}, driver={driver_config.driver_type}"
            )
            await member.startup()
            try:
                await gtAgentManager.upsert_agent(member.team_id, member.name, member.model, member.template_name)
            except Exception as e:
                logger.warning(f"写入成员数据失败: member={member.key}, error={e}")


async def reload_team_members(team_name: str, teams_config: list[TeamConfig], workspace_root: str | None = None) -> None:
    """按 Team 维度重建运行时成员实例。"""
    team_suffix = f"@{team_name}"
    keys_to_remove = [k for k in _team_members.keys() if k.endswith(team_suffix)]
    close_tasks: list[Any] = []
    for key in keys_to_remove:
        close_tasks.append(_team_members[key].close())
    if close_tasks:
        await asyncio.gather(*close_tasks, return_exceptions=True)
    for key in keys_to_remove:
        _team_members.pop(key, None)

    await load_team_ids(teams_config)

    target_config = next((cfg for cfg in teams_config if cfg.name == team_name), None)
    if target_config is None:
        logger.warning(f"重建 Team 成员失败: team '{team_name}' 不存在于配置中")
        return

    await create_team_members([target_config], workspace_root=workspace_root)


def get_team_member(team_name: str, member_name: str) -> TeamMember:
    key = _make_member_key(team_name, member_name)
    return _team_members[key]


def find_team_member(team_name: str, member_name: str) -> TeamMember | None:
    key = _make_member_key(team_name, member_name)
    return _team_members.get(key)


def get_all_team_members() -> List[TeamMember]:
    return list(_team_members.values())


def get_all_agent_definitions() -> list[AgentTemplate]:
    return list(_agent_templates.values())


def get_agent_definition(template_name: str) -> AgentTemplate | None:
    return _agent_templates.get(template_name)


def get_team_members(room_id: int) -> List[TeamMember]:
    room = roomService.get_room(room_id)
    if room is None:
        return []
    members: List[str] = roomService.get_member_names(room_id)
    return [_team_members[_make_member_key(room.team_name, n)] for n in members if _make_member_key(room.team_name, n) in _team_members]


def get_all_rooms(team_name: str, member_name: str) -> List[int]:
    return roomService.get_rooms_for_agent(_team_ids.get(team_name), member_name)


async def shutdown() -> None:
    global _team_members, _agent_templates
    close_tasks: List[Any] = [m.close() for m in _team_members.values()]
    if close_tasks:
        await asyncio.gather(*close_tasks, return_exceptions=True)
    _team_members = {}
    _agent_templates = {}

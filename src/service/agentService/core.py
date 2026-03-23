import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional

from util import llmApiUtil, configUtil
from model.coreModel.gtCoreChatModel import AgentDialogContext, ChatMessage
from model.coreModel.gtCoreAgentEvent import RoomMessageEvent
from model.dbModel.gtAgentHistory import GtAgentHistory
from .driver import AgentDriverConfig, build_agent_driver, normalize_driver_config
from service import llmService, funcToolService, roomService, messageBus, persistenceService
from service.roomService import ChatRoom, ChatContext
from constants import SpecialAgent, MessageBusTopic, AgentStatus

logger = logging.getLogger(__name__)

_agent_defs: Dict[str, dict] = {}
_agents: Dict[str, "Agent"] = {}
_team_ids: Dict[str, int] = {}  # team_name -> team_id mapping


def _make_agent_key(team_name: str, agent_name: str) -> str:
    return f"{agent_name}@{team_name}"


def _iter_team_rooms(team_config: dict) -> list[dict]:
    return team_config.get("rooms") or team_config.get("groups") or []


async def load_team_ids(teams_config: list) -> None:
    """Load team_id for each team name."""
    from dal.db import gtTeamManager
    global _team_ids
    _team_ids = {}
    for team in teams_config:
        team_name = team["name"]
        team_row = await gtTeamManager.get_team(team_name)
        if team_row:
            _team_ids[team_name] = team_row.id
    logger.info(f"Loaded team IDs: {_team_ids}")


class Agent:
    """AI Agent 壳对象：承载稳定状态，driver 负责具体驱动实现。"""

    def __init__( self, name: str, team_name: str, system_prompt: str, model: str, driver_config: Optional[AgentDriverConfig] = None):
        self.name: str = name
        self.team_name: str = team_name
        self.system_prompt: str = system_prompt
        self.model: str = model

        self._history: list[llmApiUtil.LlmApiMessage] = []
        self.wait_task_queue: asyncio.Queue = asyncio.Queue()
        self.status: AgentStatus = AgentStatus.IDLE
        self.current_room: Optional[ChatRoom] = None
        self.driver = build_agent_driver(self, driver_config or AgentDriverConfig(driver_type="native"))

    @property
    def team_id(self) -> int:
        return _team_ids.get(self.team_name, 0)

    @property
    def key(self) -> str:
        return _make_agent_key(self.team_name, self.name)

    @property
    def is_active(self) -> bool:
        return self.status == AgentStatus.ACTIVE or not self.wait_task_queue.empty()

    async def startup(self) -> None:
        await self.driver.startup()

    async def close(self) -> None:
        await self.driver.shutdown()

    def _publish_status(self, status: AgentStatus) -> None:
        messageBus.publish(
            MessageBusTopic.AGENT_STATUS_CHANGED,
            agent_name=self.name,
            team_name=self.team_name,
            status=status.name,
        )

    async def consume_task(self, max_function_calls: int) -> None:
        # 一个 Agent 可能会连续收到多个房间事件，这里串行消费，避免同一实例并发跑多个回合。
        self.status = AgentStatus.ACTIVE
        self._publish_status(self.status)
        try:
            while True:
                try:
                    task = self.wait_task_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

                try:
                    if isinstance(task, RoomMessageEvent):
                        await self.run_chat_turn(task.room_id, max_function_calls)
                    else:
                        raise TypeError(f"不支持的 Agent 任务类型: {type(task).__name__}")
                except Exception as e:
                    logger.error(f"Agent 处理任务失败: agent={self.key}, task={task!r}, error={e}", exc_info=True)
                finally:
                    self.wait_task_queue.task_done()
        finally:
            self.status = AgentStatus.IDLE
            self._publish_status(self.status)

    async def sync_room_messages(self, room: ChatRoom) -> int:
        # 把该 Agent 在房间中的未读消息追加到私有 history，返回本次真正同步进去的条数。
        new_msgs: List[ChatMessage] = await room.get_unread_messages(self.name)
        logger.info(f"同步房间消息: agent={self.key}, room={room.name}, count={len(new_msgs)}")

        synced_count = 0
        for msg in new_msgs:
            if msg.sender_name == self.name:
                continue

            message: llmApiUtil.LlmApiMessage
            if msg.sender_name == "system":
                message = llmApiUtil.LlmApiMessage.text(
                    llmApiUtil.OpenaiLLMApiRole.USER,
                    content=f"{room.name} 房间系统消息: {msg.content}",
                )
            else:
                message = llmApiUtil.LlmApiMessage.text(llmApiUtil.OpenaiLLMApiRole.USER, content=f"{msg.sender_name} 在 {room.name} 房间发言: {msg.content}")

            await self.append_history_message(message)
            synced_count += 1

        return synced_count

    async def run_chat_turn(self, room_id: int, max_function_calls: int = 5) -> None:
        # Agent 统一维护当前房间上下文 and 消息同步，driver 只负责跑这一轮聊天逻辑。
        room = roomService.get_room(room_id)
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

    async def _infer(self, tools: Optional[list[llmApiUtil.Tool]]) -> llmApiUtil.LlmApiMessage:
        # 每次推理都基于当前 history 组装完整上下文，并把 assistant 回复继续追加回 history。
        assert self._history and self._history[-1].role in (
            llmApiUtil.OpenaiLLMApiRole.USER,
            llmApiUtil.OpenaiLLMApiRole.TOOL,
            llmApiUtil.OpenaiLLMApiRole.SYSTEM,
        ), f"[{self.key}] _infer 前最后一条消息不能是 assistant，当前为: {self._history[-1].role if self._history else 'empty'}"
        ctx = AgentDialogContext(system_prompt=self.system_prompt, messages=self._history, tools=tools or None)
        response: llmApiUtil.LlmApiResponse = await llmService.infer(self.model, ctx)
        assistant_message: llmApiUtil.LlmApiMessage = response.choices[0].message
        await self.append_history_message(assistant_message)

        return assistant_message

    async def _execute_tool(self) -> None:
        """执行最后一条 assistant 消息中的所有 tool_calls，并将结果写入 history。"""
        last_msg = self.get_last_assistant_message()
        if not last_msg or not last_msg.tool_calls:
            return

        for tool_call in last_msg.tool_calls:
            function = tool_call.function if isinstance(tool_call.function, dict) else {}
            name = function.get("name", "")
            args = function.get("arguments", "")
            context = ChatContext(agent_name=self.name, team_name=self.team_name, chat_room=self.current_room)
            result = await funcToolService.run_tool_call(name, args, context=context)
            await self.append_history_message(llmApiUtil.LlmApiMessage.tool_result(tool_call.id, result))

    def get_last_assistant_message(self, start_idx: int = 0) -> Optional[llmApiUtil.LlmApiMessage]:
        """获取历史中最后一条 assistant 消息。"""
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
        self._history = [llmApiUtil.LlmApiMessage.model_validate_json(item.message_json) for item in items]

    async def append_history_message(self, message: llmApiUtil.LlmApiMessage) -> None:
        self._history.append(message)
        await self._persist_history_message(message)

    async def _persist_history_message(self, message: llmApiUtil.LlmApiMessage) -> None:
        seq: int = len(self._history) - 1
        item = GtAgentHistory(
            team_id=self.team_id,
            agent_name=self.name,
            seq=seq,
            message_json=message.model_dump_json(exclude_none=True),
        )
        await persistenceService.append_agent_history_message(item)


async def startup() -> None:
    global _agent_defs, _agents
    _agent_defs = {}
    _agents = {}


def load_agent_config(agents_config: list) -> None:
    global _agent_defs
    _agent_defs = {cfg["name"]: cfg for cfg in agents_config}
    logger.info(f"加载 Agent 定义: {list(_agent_defs.keys())}")


async def create_team_agents(teams_config: list) -> None:
    base_prompt_tmpl = configUtil.load_prompt("src/prompts/GroupChat.md")

    for team_config in teams_config:
        team_name = team_config["name"]

        agent_names_in_team = set()
        for room in _iter_team_rooms(team_config):
            for name in room["members"]:
                if name != SpecialAgent.OPERATOR:
                    agent_names_in_team.add(name)

        for name in agent_names_in_team:
            if name not in _agent_defs:
                logger.warning(f"Agent 定义不存在: {name}，跳过创建")
                continue

            cfg: dict[str, Any] = _agent_defs[name]
            if "system_prompt" in cfg:
                agent_specific_prompt = cfg["system_prompt"]
            else:
                agent_specific_prompt = configUtil.load_prompt(cfg["prompt_file"])

            full_prompt = base_prompt_tmpl + "\n\n" + agent_specific_prompt
            key = _make_agent_key(team_name, name)
            driver_config = normalize_driver_config(cfg)
            agent = Agent(
                name=name,
                team_name=team_name,
                system_prompt=full_prompt,
                model=cfg["model"],
                driver_config=driver_config,
            )
            _agents[key] = agent
            logger.info(
                f"创建 Agent 实例: key={key}, model={cfg['model']}, driver={driver_config.driver_type}"
            )
            await agent.startup()


def get_agent(team_name: str, agent_name: str) -> Agent:
    key = _make_agent_key(team_name, agent_name)
    return _agents[key]


def get_all_agents() -> List[Agent]:
    return list(_agents.values())


def get_agents(room_id: int) -> List[Agent]:
    room = roomService.get_room(room_id)
    members: List[str] = roomService.get_member_names(room_id)
    return [_agents[_make_agent_key(room.team_name, n)] for n in members if _make_agent_key(room.team_name, n) in _agents]


def get_all_rooms(team_name: str, agent_name: str) -> List[int]:
    return roomService.get_rooms_for_agent(_team_ids.get(team_name), agent_name)


async def shutdown() -> None:
    global _agents, _agent_defs
    close_tasks: List[Any] = [a.close() for a in _agents.values()]
    if close_tasks:
        await asyncio.gather(*close_tasks, return_exceptions=True)
    _agents = {}
    _agent_defs = {}

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional

from util.llm_api_util import OpenaiLLMApiRole, LlmApiMessage, LlmApiResponse, Tool
from util.config_util import load_prompt
from model.chat_context import ChatContext
from model.chat_model import AgentDialogContext, ChatMessage
from model.agent_event import RoomMessageEvent
from model.db_model.agent_history_message import AgentHistoryMessageRecord
from .driver import AgentDriverConfig, build_agent_driver, normalize_driver_config
from service import llm_service, func_tool_service, room_service, message_bus, persistence_service
from service.room_service import ChatRoom
from constants import SpecialAgent, MessageBusTopic, AgentStatus

logger = logging.getLogger(__name__)

_agent_defs: Dict[str, dict] = {}
_agents: Dict[str, "Agent"] = {}


def _make_agent_key(team_name: str, agent_name: str) -> str:
    return f"{agent_name}@{team_name}"


class Agent:
    """AI Agent 壳对象：承载稳定状态，driver 负责具体驱动实现。"""

    def __init__( self, name: str, team_name: str, system_prompt: str, model: str, driver_config: Optional[AgentDriverConfig] = None):
        self.name: str = name
        self.team_name: str = team_name
        self.system_prompt: str = system_prompt
        self.model: str = model

        self._history: List[LlmApiMessage] = []
        self.wait_task_queue: asyncio.Queue = asyncio.Queue()
        self.status: AgentStatus = AgentStatus.IDLE
        self.current_room: Optional[ChatRoom] = None
        self.driver = build_agent_driver(self, driver_config or AgentDriverConfig(driver_type="native"))

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
        message_bus.publish(
            MessageBusTopic.AGENT_STATUS_CHANGED,
            agent_name=self.name,
            team_name=self.team_name,
            status=status.value,
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
                        await self.run_chat_turn(task.room_key, max_function_calls)
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

            message: LlmApiMessage
            if msg.sender_name == "system":
                message = LlmApiMessage(role=OpenaiLLMApiRole.USER, content=f"{room.name} 房间系统消息: {msg.content}")
            else:
                message = LlmApiMessage.text(OpenaiLLMApiRole.USER, content=f"{msg.sender_name} 在 {room.name} 房间发言: {msg.content}")

            await self.append_history_message(message)
            synced_count += 1

        return synced_count

    async def run_chat_turn(self, room_key: str, max_function_calls: int = 5) -> None:
        # Agent 统一维护当前房间上下文和消息同步，driver 只负责跑这一轮聊天逻辑。
        room = room_service.get_room(room_key)
        self.current_room = room
        synced_count = await self.sync_room_messages(room)

        try:
            await self.driver.run_chat_turn(room, synced_count, max_function_calls)
        finally:
            self.current_room = None

    async def sync_room(self, room: ChatRoom) -> None:
        await self.sync_room_messages(room)

    async def _infer(self, tools: Optional[List[Tool]]) -> LlmApiMessage:
        # 每次推理都基于当前 history 组装完整上下文，并把 assistant 回复继续追加回 history。
        assert self._history and self._history[-1].role in (
            OpenaiLLMApiRole.USER,
            OpenaiLLMApiRole.TOOL,
            OpenaiLLMApiRole.SYSTEM,
        ), f"[{self.key}] _infer 前最后一条消息不能是 assistant，当前为: {self._history[-1].role if self._history else 'empty'}"
        ctx = AgentDialogContext(system_prompt=self.system_prompt, messages=self._history, tools=tools or None)
        response: LlmApiResponse = await llm_service.infer(self.model, ctx)
        assistant_message: LlmApiMessage = response.choices[0].message
        await self.append_history_message(assistant_message)

        return assistant_message

    async def _execute_tool(self, tool_call_id: str, name: str, args: str) -> str:
        # 执行 LLM 发起的 function call，返回执行结果。
        # context 携带当前 agent 身份和房间引用，供 send_chat_msg 等需要房间操作的工具使用。
        context = ChatContext(agent_name=self.name, team_name=self.team_name, chat_room=self.current_room)
        result = await func_tool_service.run_tool_call(name, args, context=context)
        await self.append_history_message(LlmApiMessage.tool_result(tool_call_id, result))

        return result

    def get_last_assistant_tool_call(self, start_idx: int = 0) -> Optional[dict]:
        recent_history = self._history[start_idx:]

        for message in reversed(recent_history):
            if message.role != OpenaiLLMApiRole.ASSISTANT:
                continue

            tool_calls = message.tool_calls or []

            if not tool_calls:
                continue

            call = tool_calls[-1]
            function = call.function if isinstance(call.function, dict) else {}
            return {
                "name": function.get("name"),
                "args": function.get("arguments", ""),
            }

        return None

    def dump_history_messages(self) -> List[AgentHistoryMessageRecord]:
        return [
            AgentHistoryMessageRecord(
                agent_key=self.key,
                seq=idx,
                message_json=msg.model_dump_json(exclude_none=True),
            )
            for idx, msg in enumerate(self._history)
        ]

    def inject_history_messages(self, items: List[AgentHistoryMessageRecord]) -> None:
        self._history = [LlmApiMessage.model_validate_json(item.message_json) for item in items]

    async def append_history_message(self, message: LlmApiMessage) -> None:
        self._history.append(message)
        await self._persist_history_message(message)

    async def _persist_history_message(self, message: LlmApiMessage) -> None:
        seq: int = len(self._history) - 1
        item = AgentHistoryMessageRecord(
            agent_key=self.key,
            seq=seq,
            message_json=message.model_dump_json(exclude_none=True),
        )
        await persistence_service.append_agent_history_message(item)


async def startup() -> None:
    global _agent_defs, _agents
    _agent_defs = {}
    _agents = {}


def load_agent_config(agents_config: list) -> None:
    global _agent_defs
    _agent_defs = {cfg["name"]: cfg for cfg in agents_config}
    logger.info(f"加载 Agent 定义: {list(_agent_defs.keys())}")


async def create_team_agents(teams_config: list) -> None:
    base_prompt_tmpl = load_prompt("src/prompts/GroupChat.md")

    for team_config in teams_config:
        team_name = team_config["name"]

        agent_names_in_team = set()
        for group in team_config["groups"]:
            for name in group["members"]:
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
                agent_specific_prompt = load_prompt(cfg["prompt_file"])

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


def get_agents(team_name: str, room_name: str) -> List[Agent]:
    members: List[str] = room_service.get_member_names(team_name, room_name)
    return [_agents[_make_agent_key(team_name, n)] for n in members if _make_agent_key(team_name, n) in _agents]


def get_all_rooms(team_name: str, agent_name: str) -> List[str]:
    return room_service.get_rooms_for_agent(team_name, agent_name)


async def shutdown() -> None:
    global _agents, _agent_defs
    close_tasks: List[Any] = [a.close() for a in _agents.values()]
    if close_tasks:
        await asyncio.gather(*close_tasks, return_exceptions=True)
    _agents = {}
    _agent_defs = {}

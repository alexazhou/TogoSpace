import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from util.llm_api_util import OpenaiLLMApiRole, LlmApiMessage, LlmApiResponse, Tool
from util.config_util import load_prompt
from model.chat_model import AgentDialogContext, ChatMessage
from model.chat_context import ChatContext
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


@dataclass
class AgentTurnActionResult:
    ok: bool
    message: str
    turn_finished: bool = False


class Agent:
    """AI Agent 壳对象：承载稳定状态，driver 负责具体驱动实现。"""

    def __init__(
        self,
        name: str,
        team_name: str,
        system_prompt: str,
        model: str,
        driver_config: Optional[AgentDriverConfig] = None,
    ):
        self.name: str = name
        self.team_name: str = team_name
        self.system_prompt: str = system_prompt
        self.model: str = model

        self._history: List[LlmApiMessage] = []
        self.wait_task_queue: asyncio.Queue = asyncio.Queue()
        self.status: AgentStatus = AgentStatus.IDLE
        self._turn_ctx: Optional[ChatContext] = None
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
        self.status = AgentStatus.ACTIVE
        self._publish_status(self.status)
        try:
            while True:
                try:
                    event: RoomMessageEvent = self.wait_task_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

                try:
                    await self.run_turn(event.room_key, max_function_calls)
                except Exception as e:
                    logger.error(f"Agent 处理任务失败: agent={self.key}, room={event.room_key}, error={e}", exc_info=True)
                finally:
                    self.wait_task_queue.task_done()
        finally:
            self.status = AgentStatus.IDLE
            self._publish_status(self.status)

    async def sync_room_messages(self, room: ChatRoom) -> int:
        new_msgs: List[ChatMessage] = await room.get_unread_messages(self.name)
        logger.info(f"同步房间消息: agent={self.key}, room={room.name}, count={len(new_msgs)}")

        synced_count = 0
        for msg in new_msgs:
            if msg.sender_name == self.name:
                continue

            if msg.sender_name == "system":
                await self.append_history_message(
                    LlmApiMessage(
                        role=OpenaiLLMApiRole.USER,
                        content=f"{room.name} 房间系统消息: {msg.content}",
                    )
                )
                synced_count += 1
                continue

            await self.append_history_message(
                LlmApiMessage.text(
                    OpenaiLLMApiRole.USER,
                    f"{msg.sender_name} 在 {room.name} 房间发言: {msg.content}",
                )
            )
            synced_count += 1

        return synced_count

    async def run_turn(self, room_key: str, max_function_calls: int = 5) -> None:
        await self.driver.run_turn(room_key, max_function_calls)

    async def send_chat_message(self, room_name: str, msg: str) -> AgentTurnActionResult:
        room_key = room_name if "@" in room_name else f"{room_name}@{self.team_name}"
        target_room: ChatRoom = room_service.get_room(room_key)
        if target_room is None:
            logger.warning(f"send_chat_msg: 目标房间不存在 {room_key}，回落到当前房间")
            target_room = self.current_room
        if target_room is None:
            return AgentTurnActionResult(ok=False, message="error: 当前没有可用的房间上下文。")

        await target_room.add_message(self.name, msg)
        await self.append_history_message(LlmApiMessage.text(OpenaiLLMApiRole.ASSISTANT, msg))
        current_room = self.current_room
        if target_room is current_room:
            self.current_room = None
            return AgentTurnActionResult(ok=True, message="success: 消息已发送，本轮发言结束。", turn_finished=True)
        if current_room is None:
            return AgentTurnActionResult(ok=True, message=f"success: 消息已发送到 {target_room.name}。")
        return AgentTurnActionResult(
            ok=True,
            message=f"success: 消息已发送到 {target_room.name}。你还需要调用 send_chat_msg 向当前房间 {current_room.name} 发言，或调用 skip_chat_msg 跳过。",
        )

    def skip_chat_turn(self) -> AgentTurnActionResult:
        room = self.current_room
        if room is None:
            return AgentTurnActionResult(ok=False, message="error: 当前没有激活的房间上下文。")

        ok = room.skip_turn(sender=self.name)
        if not ok:
            current = room.get_current_turn_agent()
            return AgentTurnActionResult(
                ok=False,
                message=f"error: 现在不是你的发言轮次（当前应由 {current} 发言），请勿再调用任何工具。",
            )

        self.current_room = None
        return AgentTurnActionResult(ok=True, message="success: 已跳过本轮发言。", turn_finished=True)

    async def chat(
        self,
        tools: Optional[List[Tool]] = None,
        done_check: Optional[Callable[[], bool]] = None,
        max_function_calls: int = 5,
    ) -> LlmApiMessage:
        assistant_message: Optional[LlmApiMessage] = None
        for _ in range(max_function_calls):
            assistant_message = await self._infer(tools)

            if not assistant_message.tool_calls:
                return assistant_message

            logger.info(f"检测到工具调用: agent={self.key}, count={len(assistant_message.tool_calls)}")
            for tool_call in assistant_message.tool_calls:
                name = tool_call.function.get("name", "")
                args = tool_call.function.get("arguments", "")
                await self._execute_tool(tool_call.id, name, args)

            if done_check and done_check():
                return assistant_message

        logger.warning(f"达到最大函数调用次数: agent={self.key}, max={max_function_calls}")
        return assistant_message

    async def sync_room(self, room: ChatRoom) -> None:
        await self.sync_room_messages(room)

    async def _infer(self, tools: Optional[List[Tool]]) -> LlmApiMessage:
        assert self._history and self._history[-1].role in (
            OpenaiLLMApiRole.USER,
            OpenaiLLMApiRole.TOOL,
            OpenaiLLMApiRole.SYSTEM,
        ), f"[{self.key}] _infer 前最后一条消息不能是 assistant，当前为: {self._history[-1].role if self._history else 'empty'}"
        ctx = AgentDialogContext(
            system_prompt=self.system_prompt,
            messages=self._history,
            tools=tools or None,
        )
        response: LlmApiResponse = await llm_service.infer(self.model, ctx)
        assistant_message: LlmApiMessage = response.choices[0].message
        await self.append_history_message(assistant_message)
        return assistant_message

    async def _execute_tool(self, tool_call_id: str, name: str, args: str) -> None:
        result = await func_tool_service.run_tool_call(name, args, context=self._turn_ctx)
        await self.append_history_message(LlmApiMessage.tool_result(tool_call_id, result))

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

    @staticmethod
    def make_text_message(role: str, content: str) -> LlmApiMessage:
        return LlmApiMessage.text(role, content)

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

import asyncio
import logging
from typing import Dict, List, Optional

from service import message_bus
from service.message_bus import Message
from util.llm_api_util import LlmApiMessage
from model.agent_event import RoomMessageEvent
from model.chat_context import ChatContext
from service import agent_service, room_service as chat_room, func_tool_service as agent_tools
from service.agent_service import Agent
from service.room_service import ChatRoom
from constants import MessageBusTopic, TurnStatus, TurnCheckResult

logger = logging.getLogger(__name__)

_rooms_config: list = []
_max_function_calls: int = 5
_running: Dict[str, asyncio.Task] = {}


def init(rooms_config: list, max_function_calls: int = 5) -> None:
    """初始化调度器，须在 run() 前调用一次。"""
    global _rooms_config, _max_function_calls
    _rooms_config = rooms_config
    _max_function_calls = max_function_calls
    message_bus.subscribe(MessageBusTopic.ROOM_AGENT_TURN, _on_agent_turn)


def stop() -> None:
    """重置调度器状态。"""
    global _rooms_config, _max_function_calls, _running
    _rooms_config = []
    _max_function_calls = 5
    _running = {}


def _on_agent_turn(msg: Message) -> None:
    """订阅 ROOM_AGENT_TURN：若该 Agent 当前未运行则创建 Task 加入运行列表。"""
    agent_name: str = msg.payload["agent_name"]
    room_name: str = msg.payload["room_name"]
    agent = agent_service.get_agent(agent_name)
    agent.wait_event_queue.put_nowait(RoomMessageEvent(room_name))
    existing = _running.get(agent_name)
    if existing is None or existing.done():
        _running[agent_name] = asyncio.create_task(_run_agent(agent))
        logger.info(f"[{agent_name}] 加入运行列表")


async def run() -> None:
    """以 Agent 为中心的事件驱动调度。"""
    global _running
    _running = {}

    for r in _rooms_config:
        room = chat_room.get_room(r["name"])
        agents = agent_service.get_agents(r["name"])
        agent_names = [a.name for a in agents]
        logger.info(f"[{r['name']}] 参与者: {agent_names}，最大轮次: {r['max_turns']}")
        room.setup_turns([a.name for a in agents], r["max_turns"])

    # 循环 gather 直到所有 Task 完成
    # 每轮 gather 结束后，新创建的 Task 会在下一轮被拾取
    while True:
        pending = [t for t in _running.values() if not t.done()]
        if not pending:
            break
        await asyncio.gather(*pending, return_exceptions=True)
        for name, task in list(_running.items()):
            if task.done():
                logger.info(f"[{name}] 从运行列表移除")
                del _running[name]

    for r in _rooms_config:
        logger.info(f"\n{chat_room.get_room(r['name']).format_log()}")


async def _run_agent(agent: Agent) -> None:
    """消费队列中当前所有事件，队列为空后退出。"""
    while not agent.wait_event_queue.empty():
        event: RoomMessageEvent = agent.wait_event_queue.get_nowait()
        await _handle_event(agent, event)
        agent.wait_event_queue.task_done()
    logger.info(f"[{agent.name}] 队列为空，退出运行")


async def _handle_event(agent: Agent, event: RoomMessageEvent) -> None:
    """处理单个房间消息事件：同步房间消息并驱动 Agent 发言。"""
    room: ChatRoom = chat_room.get_room(event.room_name)
    agent.sync_room(room)

    try:
        agent_context = ChatContext(
            agent_name=agent.name,
            chat_room=room,
            get_room=chat_room.get_room,
        )
        last_called: Dict[str, Optional[str]] = {"name": None}

        def executor(name: str, args: str, _ctx: ChatContext = agent_context) -> str:
            last_called["name"] = name
            return agent_tools.run_tool_call(name, args, context=_ctx)

        def turn_checker(msg: LlmApiMessage) -> TurnCheckResult:
            if last_called["name"] == "send_chat_msg":
                return TurnCheckResult(TurnStatus.SUCCESS)
            if not msg.tool_calls:
                return TurnCheckResult(TurnStatus.ERROR, "你必须调用 send_chat_msg 工具发送消息，不能直接输出文字。")
            return TurnCheckResult(TurnStatus.CONTINUE)

        response: LlmApiMessage = await agent.chat(
            tools=agent_tools.get_tools(),
            function_executor=executor,
            turn_checker=turn_checker,
            max_function_calls=_max_function_calls,
        )

        if response.content:
            logger.info(f"[{event.room_name}] {agent.name} (思考): {response.content}")
    except Exception as e:
        logger.error(f"[{event.room_name}] {agent.name} 生成回复失败: {e}")

import asyncio
import logging
from typing import Dict, Set

from service import message_bus
from service.message_bus import Message
from model.agent_event import RoomMessageEvent
from service import agent_service, room_service as chat_room
from service.agent_service import Agent
from constants import MessageBusTopic

logger = logging.getLogger(__name__)

_rooms_config: list = []
_max_function_calls: int = 5
_active_agents: Set[str] = set()   # 有待处理事件或正在运行的 Agent
_running: Dict[str, asyncio.Task] = {}


def init(rooms_config: list, max_function_calls: int = 5) -> None:
    """初始化调度器，须在 run() 前调用一次。"""
    global _rooms_config, _max_function_calls
    _rooms_config = rooms_config
    _max_function_calls = max_function_calls
    message_bus.subscribe(MessageBusTopic.ROOM_AGENT_TURN, _on_agent_turn)


def stop() -> None:
    """重置调度器状态。"""
    global _rooms_config, _max_function_calls, _active_agents, _running
    _rooms_config = []
    _max_function_calls = 5
    _active_agents = set()
    _running = {}


def _on_agent_turn(msg: Message) -> None:
    """订阅 ROOM_AGENT_TURN：将事件入队，标记 Agent 为活跃，若未运行则创建 Task。"""
    agent_name: str = msg.payload["agent_name"]
    room_name: str = msg.payload["room_name"]
    agent = agent_service.get_agent(agent_name)
    agent.wait_event_queue.put_nowait(RoomMessageEvent(room_name))
    _active_agents.add(agent_name)
    logger.info(f"Agent 激活: agent={agent_name}, room={room_name}")
    existing = _running.get(agent_name)
    if existing is None or existing.done():
        _running[agent_name] = asyncio.create_task(_run_agent(agent))


async def run() -> None:
    """以 Agent 为中心的事件驱动调度，所有 Agent 均不活跃后结束。"""
    global _active_agents, _running
    _active_agents = set()
    _running = {}

    for r in _rooms_config:
        room = chat_room.get_room(r["name"])
        logger.info(f"初始化轮次配置: room={r['name']}, max_turns={r['max_turns']}")
        room.setup_turns([a.name for a in agent_service.get_agents(r["name"])], r["max_turns"])

    # 循环直到所有 Agent 均不活跃
    while _active_agents:
        pending = {t for t in _running.values() if not t.done()}
        if not pending:
            break
        done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            name = next((n for n, t in _running.items() if t is task), None)
            if name is not None:
                del _running[name]

    for r in _rooms_config:
        logger.info(f"\n{chat_room.get_room(r['name']).format_log()}")


async def _run_agent(agent: Agent) -> None:
    """消费队列中当前所有事件；队列清空后将 Agent 标记为不活跃。"""
    while not agent.wait_event_queue.empty():
        event: RoomMessageEvent = agent.wait_event_queue.get_nowait()
        await _handle_event(agent, event)
        agent.wait_event_queue.task_done()
    _active_agents.discard(agent.name)
    logger.info(f"agent all task done, go sleep: agent={agent.name}")


async def _handle_event(agent: Agent, event: RoomMessageEvent) -> None:
    """处理单个房间消息事件：委托 agent_service 完成一轮发言。"""
    try:
        await agent_service.run_turn(agent, event.room_name, _max_function_calls)
    except Exception as e:
        logger.error(f"生成回复失败: agent={agent.name}, room={event.room_name}, error={e}")

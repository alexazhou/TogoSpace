import asyncio
import logging
from typing import Dict, List

from service import message_bus
from service.message_bus import Message
from model.agent_event import RoomMessageEvent
from service import agent_service, room_service as chat_room
from service.agent_service import Agent
from constants import MessageBusTopic

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

    # 每当有 Task 完成，立刻将 _running 中新增的 Task 补入等待集合
    pending: set = set(_running.values())
    while pending:
        done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            name = next(n for n, t in _running.items() if t is task)
            logger.info(f"[{name}] 从运行列表移除")
            del _running[name]
        for task in _running.values():
            if not task.done():
                pending.add(task)

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
    """处理单个房间消息事件：委托 agent_service 完成一轮发言。"""
    try:
        await agent_service.run_turn(agent, event.room_name, _max_function_calls)
    except Exception as e:
        logger.error(f"[{event.room_name}] {agent.name} 生成回复失败: {e}")

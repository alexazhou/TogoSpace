import asyncio
import logging
from typing import Dict, Set

from service import message_bus
from service.message_bus import Message
from model.agent_event import RoomMessageEvent
from service import agent_service, room_service as chat_room
from service.agent_service import Agent
from constants import MessageBusTopic, SpecialAgent

logger = logging.getLogger(__name__)

_rooms_config: list = []
_max_function_calls: int = 5
_active_agents: Set[str] = set()   # 有待处理事件或正在运行的 Agent
_running: Dict[str, asyncio.Task] = {}
_stop_event: asyncio.Event = asyncio.Event()


def is_agent_active(agent_name: str) -> bool:
    """如果 Agent 正在运行任务，或者其事件队列中仍有待处理项，则视为活跃。"""
    task = _running.get(agent_name)
    if task and not task.done():
        return True
    
    try:
        agent = agent_service.get_agent(agent_name)
        if not agent.wait_event_queue.empty():
            return True
    except (KeyError, AttributeError):
        pass
        
    return False


def init(rooms_config: list, max_function_calls: int = 5) -> None:
    """初始化调度器，须在 run() 前调用一次。"""
    global _rooms_config, _max_function_calls
    _rooms_config = rooms_config
    _max_function_calls = max_function_calls
    _stop_event.clear()
    message_bus.subscribe(MessageBusTopic.ROOM_AGENT_TURN, _on_agent_turn)


def stop() -> None:
    """重置调度器状态。"""
    global _rooms_config, _max_function_calls, _active_agents, _running
    _stop_event.set()
    _rooms_config = []
    _max_function_calls = 5
    _active_agents = set()
    _running = {}


def _on_agent_turn(msg: Message) -> None:
    """订阅 ROOM_AGENT_TURN：将事件入队，标记 Agent 为活跃，若未运行则创建 Task。"""
    agent_name: str = msg.payload["agent_name"]
    room_name: str = msg.payload["room_name"]

    if agent_name == SpecialAgent.OPERATOR:
        logger.info(f"轮到人类操作者，系统进入等待状态: room={room_name}")
        return

    agent = agent_service.get_agent(agent_name)
    agent.wait_event_queue.put_nowait(RoomMessageEvent(room_name))
    _active_agents.add(agent_name)
    logger.info(f"Agent 激活: agent={agent_name}, room={room_name}")
    existing = _running.get(agent_name)
    if existing is None or existing.done():
        _running[agent_name] = asyncio.create_task(_run_agent(agent))


async def run() -> None:
    """持续运行的事件调度器，支持实时接入。"""
    global _active_agents, _running
    _active_agents = set()
    _running = {}

    for r in _rooms_config:
        room = chat_room.get_room(r["name"])
        logger.info(f"初始化轮次配置: room={r['name']}, max_turns={r['max_turns']}")
        room.setup_turns(chat_room.get_member_names(r["name"]), r["max_turns"])

    # 持续运行，直到 _stop_event 被设置
    stop_waiter = asyncio.create_task(_stop_event.wait())
    
    while not _stop_event.is_set():
        pending = {t for n, t in _running.items() if not t.done()}
        pending.add(stop_waiter)
            
        done, _ = await asyncio.wait(
            pending, 
            return_when=asyncio.FIRST_COMPLETED
        )
        for task in done:
            if task is stop_waiter:
                break
            # 清理已完成的任务引用
            names_to_del = [n for n, t in _running.items() if t is task]
            for n in names_to_del:
                del _running[n]
        
        if stop_waiter.done():
            break

    if not stop_waiter.done():
        stop_waiter.cancel()

    logger.info("Scheduler 已停止运行")
    for r in _rooms_config:
        logger.info(f"\n{chat_room.get_room(r['name']).format_log()}")


async def _run_agent(agent: Agent) -> None:
    """持续消费队列中的事件，直到队列为空且没有新事件。"""
    _active_agents.add(agent.name)
    try:
        while True:
            try:
                # 尝试以非阻塞方式获取事件
                event: RoomMessageEvent = agent.wait_event_queue.get_nowait()
            except asyncio.QueueEmpty:
                # 队列空了，退出循环
                break
                
            await _handle_event(agent, event)
            agent.wait_event_queue.task_done()
    finally:
        _active_agents.discard(agent.name)
        logger.info(f"Agent 进入休眠: agent={agent.name}")


async def _handle_event(agent: Agent, event: RoomMessageEvent) -> None:
    """处理单个房间消息事件：委托 agent_service 完成一轮发言。"""
    try:
        await agent_service.run_turn(agent, event.room_name, _max_function_calls)
    except Exception as e:
        logger.error(f"生成回复失败: agent={agent.name}, room={event.room_name}, error={e}")

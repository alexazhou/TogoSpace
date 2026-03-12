import asyncio
import logging
from typing import Dict, Set, Optional

from service import message_bus
from service.message_bus import Message
from model.agent_event import RoomMessageEvent
from service import agent_service, room_service as chat_room
from service.agent_service import Agent
from constants import MessageBusTopic, SpecialAgent

logger = logging.getLogger(__name__)

_teams_config: list = []
_running: Dict[str, asyncio.Task] = {}
_stop_event: asyncio.Event = asyncio.Event()


def init(teams_config: list) -> None:
    """初始化调度器，须在 run() 前调用一次。"""
    global _teams_config
    _teams_config = teams_config
    _stop_event.clear()
    message_bus.subscribe(MessageBusTopic.ROOM_AGENT_TURN, _on_agent_turn)


def stop() -> None:
    """重置调度器状态。"""
    global _teams_config, _running
    _stop_event.set()
    _teams_config = []
    _running = {}


def _on_agent_turn(msg: Message) -> None:
    """订阅 ROOM_AGENT_TURN：将任务入队，标记 Agent 为活跃，若未运行则创建 Task。"""
    agent_name: str = msg.payload["agent_name"]
    room_key: str = msg.payload["room_key"]
    team_name: str = msg.payload["team_name"]

    logger.info(f"收到轮次事件: agent={agent_name}, room={room_key}, team={team_name}")

    if agent_name == SpecialAgent.OPERATOR:
        logger.info(f"轮到人类操作者，系统进入等待状态: room={room_key}")
        return

    try:
        agent = agent_service.get_agent(team_name, agent_name)
    except KeyError:
        logger.error(f"Agent 不存在: agent_name={agent_name}, team_name={team_name}, key={agent_name}@{team_name}")
        return
    except Exception as e:
        logger.error(f"获取 Agent 失败: agent_name={agent_name}, team_name={team_name}, error={e}")
        return

    agent.wait_task_queue.put_nowait(RoomMessageEvent(room_key))

    # 使用 agent@team 作为 running key
    agent_key = agent.key
    logger.info(f"Agent 激活: agent={agent_key}, room={room_key}")
    existing = _running.get(agent_key)
    if existing is None or existing.done():
        # 从 team config 中获取 max_function_calls
        max_fc = 5
        for team in _teams_config:
            if team["name"] == team_name:
                max_fc = team.get("max_function_calls", 5)
                break
        logger.info(f"创建新任务: agent={agent_key}, max_function_calls={max_fc}")
        _running[agent_key] = asyncio.create_task(agent.consume_task(max_fc))
    else:
        logger.info(f"Agent 任务已在运行: agent={agent_key}")


async def run() -> None:
    """持续运行的事件调度器，支持实时接入。"""
    global _running
    _running = {}

    for team in _teams_config:
        team_name = team["name"]
        for group in team["groups"]:
            room_key = f"{group['name']}@{team_name}"
            room = chat_room.get_room(room_key)
            logger.info(f"初始化轮次配置: room={room_key}, max_turns={group['max_turns']}")
            room.setup_turns(group["members"], group["max_turns"])

    # 持续运行，直到 _stop_event 被设置
    stop_waiter = asyncio.create_task(_stop_event.wait())

    while not _stop_event.is_set():
        # 仅等待未完成的任务
        pending = {t for t in _running.values() if not t.done()}
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
    for team in _teams_config:
        team_name = team["name"]
        for group in team["groups"]:
            room_key = f"{group['name']}@{team_name}"
            logger.info(f"\n{chat_room.get_room(room_key).format_log()}")

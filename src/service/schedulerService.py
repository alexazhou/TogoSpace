import asyncio
import logging
from typing import Dict

from service import messageBus
from service.messageBus import Message
from model.coreModel.gtCoreAgentEvent import RoomMessageEvent
from service import agentService, roomService as chat_room
from service.agentService import Agent
from dal.db import gtRoomManager
from constants import MessageBusTopic, SpecialAgent

logger = logging.getLogger(__name__)

_teams_config: list = []
_running: Dict[str, asyncio.Task] = {}
_stop_event: asyncio.Event = asyncio.Event()


def _iter_team_rooms(team_config: dict) -> list[dict]:
    return team_config.get("rooms") or team_config.get("groups") or []


async def startup(teams_config: list) -> None:
    """初始化调度器，须在 run() 前调用一次。"""
    global _teams_config, _stop_event
    _teams_config = teams_config
    _stop_event = asyncio.Event()
    messageBus.subscribe(MessageBusTopic.ROOM_AGENT_TURN, _on_agent_turn)


def add_agent(agent: Agent, max_fc: int) -> None:
    """将 agent 加入调度池，若已在运行则跳过。"""
    existing: asyncio.Task | None = _running.get(agent.key)
    if existing is not None and not existing.done():
        return
    task = asyncio.create_task(agent.consume_task(max_fc))
    _running[agent.key] = task
    task.add_done_callback(lambda t: _on_task_done(agent.key, t))


def _on_task_done(key: str, task: asyncio.Task) -> None:
    """Task 完成回调：仅当完成的 task 仍是当前注册的任务时才移出调度池。

    asyncio 在协程返回时立即将 task 标记为 done，但 done callback 通过
    loop.call_soon 异步调度，稍后才执行。在这段空隙内，同一 agent 可能已被
    重新入队并在 _running 中注册了新 task。此时若直接 remove_agent，会误删
    新 task 并取消它。通过 `is` 判断确保只有"自己的"task 完成时才触发移除。
    """
    if _running.get(key) is task:
        remove_agent(key)


def remove_agent(agent_key: str) -> None:
    """从调度池移出 agent。"""
    task = _running.pop(agent_key, None)
    if task and not task.done():
        task.cancel()


def _on_agent_turn(msg: Message) -> None:
    """订阅 ROOM_AGENT_TURN：将任务入队，若 agent 未运行则加入调度池。"""
    agent_name: str = msg.payload["agent_name"]
    room_id: int = msg.payload["room_id"]
    team_name: str = msg.payload["team_name"]

    if agent_name == SpecialAgent.OPERATOR:
        logger.info(f"轮到人类操作者，系统进入等待状态: room_id={room_id}")
        return

    try:
        agent: Agent = agentService.get_agent(team_name, agent_name)
    except KeyError:
        logger.error(f"Agent 不存在: agent_name={agent_name}, team_name={team_name}")
        return
    except Exception as e:
        logger.error(f"获取 Agent 失败: agent_name={agent_name}, team_name={team_name}, error={e}")
        return

    # 去重：同一房间已在队列中则跳过，避免重复调度
    if any(e.room_id == room_id for e in agent.wait_task_queue._queue):
        logger.debug(f"跳过重复入队: agent={agent.key}, room_id={room_id}")
        return

    agent.wait_task_queue.put_nowait(RoomMessageEvent(room_id))

    max_fc = 5
    for team in _teams_config:
        if team["name"] == team_name:
            max_fc = team.get("max_function_calls", 5)
            break
    add_agent(agent, max_fc)


async def run() -> None:
    """持续运行直到 stop() 被调用。"""
    await _stop_event.wait()

    logger.info("Scheduler 已停止运行")
    for team in _teams_config:
        team_name = team["name"]
        for room in _iter_team_rooms(team):
            room_ref = f"{room['name']}@{team_name}"
            try:
                runtime_room = chat_room.get_room(room_ref)
            except RuntimeError:
                continue
            logger.info(f"\n{runtime_room.format_log()}")


def replay_scheduling_rooms() -> None:
    for room in chat_room.get_all_rooms():
        room.start_scheduling()


def stop() -> None:
    """通知 run() 退出循环。"""
    _stop_event.set()


def shutdown() -> None:
    """清空调度状态，强制结束 run()。"""
    global _teams_config, _running
    stop()
    _teams_config = []
    for task in _running.values():
        if not task.done():
            task.cancel()
    _running = {}


def refresh_team_config(team_name: str, teams_config: list) -> None:
    """刷新指定 Team 的调度配置。"""
    global _teams_config
    _teams_config = teams_config
    logger.info(f"Team '{team_name}' 的调度配置已刷新")


def stop_team(team_name: str) -> None:
    """停止指定 Team 的所有调度任务。"""
    to_remove = [key for key in _running.keys() if key.endswith(f"@{team_name}")]
    for agent_key in to_remove:
        remove_agent(agent_key)
    logger.info(f"Team '{team_name}' 的 {len(to_remove)} 个调度任务已停止")

import asyncio
import logging
from typing import Dict

from service import messageBus
from service.messageBus import Message
from model.coreModel.gtCoreAgentEvent import GtCoreRoomMessageEvent
from service import agentService, roomService as chat_room
from service.agentService import Agent
from dal.db import gtTeamManager
from constants import MessageBusTopic, AgentStatus

logger = logging.getLogger(__name__)

_global_max_fc: int = 5
_running_tasks: Dict[int, asyncio.Task] = {}
_running_agents: Dict[int, Agent] = {}
_stop_event: asyncio.Event = asyncio.Event()

async def startup() -> None:
    """初始化调度器，须在 run() 前调用一次。"""
    global _global_max_fc, _running_agents, _stop_event
    _global_max_fc = 5
    _running_agents = {}
    _stop_event = asyncio.Event()
    messageBus.subscribe(MessageBusTopic.ROOM_AGENT_TURN, _on_agent_turn)


def add_agent(agent: Agent, max_fc: int) -> None:
    """将 Agent 加入调度池，若已在运行或处于 FAILED 状态则跳过。"""
    if agent.status == AgentStatus.FAILED:
        return

    agent_id = agent.gt_agent.id
    existing: asyncio.Task | None = _running_tasks.get(agent_id)

    if existing is not None and existing.done() == False:
        return

    task = asyncio.create_task(agent.consume_task(max_fc))
    _running_tasks[agent_id] = task
    _running_agents[agent_id] = agent
    task.add_done_callback(lambda t: _on_task_done(agent, t))


def _on_task_done(agent: Agent, task: asyncio.Task) -> None:
    """Task 完成回调：仅清理当前 Agent 任务，并在收尾竞态时自动续起消费。

    asyncio 在协程返回时立即将 task 标记为 done，但 done callback 通过
    loop.call_soon 异步调度，稍后才执行。在这段空隙内，同一 Agent 可能已被
    重新入队并在 _running_tasks 中注册了新 task。此时若直接 remove_agent，会误删
    新 task 并取消它。通过 `is` 判断确保只有"自己的"task 完成时才触发移除。
    """
    agent_id = agent.gt_agent.id
    if _running_tasks.get(agent_id) is not task:
        return

    _running_tasks.pop(agent_id, None)
    _running_agents.pop(agent_id, None)

    # 收尾竞态兜底：如果 task 结束时队列里还有事件，立即续起一个新 task。
    if not agent.wait_task_queue.empty():
        logger.info("Agent 任务收尾时检测到待处理事件，自动续起消费: agent_id=%s", agent_id)
        add_agent(agent, _global_max_fc)


def remove_agent(agent_id: int) -> None:
    """从调度池移出 Agent。"""
    task = _running_tasks.pop(agent_id, None)
    _running_agents.pop(agent_id, None)
    if task and not task.done():
        task.cancel()


def _on_agent_turn(msg: Message) -> None:
    """订阅 ROOM_AGENT_TURN：将 Agent 任务入队，若 Agent 未运行则加入调度池。"""
    agent_id: int = msg.payload["agent_id"]
    room_id: int = msg.payload["room_id"]

    room = chat_room.get_room(room_id)
    assert room is not None, f"room must exist before scheduling: room_id={room_id}, agent_id={agent_id}"
    agent = agentService.get_agent(agent_id)

    # 去重：同一房间已在队列中则跳过，避免重复调度
    queued_events = list(getattr(agent.wait_task_queue, "_queue", []))
    if any(e.room_id == room_id for e in queued_events):
        logger.debug(f"跳过重复入队: agent_id={agent_id}, room_id={room_id}")
        return

    agent.wait_task_queue.put_nowait(GtCoreRoomMessageEvent(room_id))

    add_agent(agent, _global_max_fc)


async def run() -> None:
    """持续运行直到 stop() 被调用。"""
    await _stop_event.wait()

    logger.info("Scheduler 已停止运行")
    for runtime_room in chat_room.get_all_rooms():
        logger.info(f"\n{runtime_room.format_log()}")


async def start_scheduling(team_name: str | None = None) -> None:
    """统一开始调度入口：激活/重放房间轮次事件。"""
    await chat_room.activate_rooms(team_name)
    logger.info("开始调度完成: team=%s", team_name or "ALL")


async def replay_scheduling_rooms() -> None:
    """兼容入口：重放可调度房间。"""
    await start_scheduling()


def stop() -> None:
    """通知 run() 退出循环。"""
    _stop_event.set()


def shutdown() -> None:
    """清空调度状态，强制结束 run()。"""
    global _running_tasks, _running_agents
    messageBus.unsubscribe(MessageBusTopic.ROOM_AGENT_TURN, _on_agent_turn)
    stop()
    for task in _running_tasks.values():
        if task.done():
            continue
        try:
            if task.get_loop().is_closed():
                continue
            task.cancel()
        except RuntimeError:
            continue
    _running_tasks = {}
    _running_agents = {}


def stop_team(team_id: int) -> None:
    """停止指定 Team 的所有调度任务。"""
    to_remove = [agent_id for agent_id, agent in _running_agents.items() if agent.gt_agent.team_id == team_id]
    for agent_id in to_remove:
        remove_agent(agent_id)
    logger.info(f"Team ID={team_id} 的 {len(to_remove)} 个调度任务已停止")

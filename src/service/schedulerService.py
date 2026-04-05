import asyncio
import logging

from service import messageBus
from service.messageBus import EventBusMessage
from service import agentService, roomService as chat_room
from dal.db import gtAgentTaskManager
from model.dbModel.gtAgent import GtAgent
from constants import MessageBusTopic, AgentTaskType, SpecialAgent

logger = logging.getLogger(__name__)

_stop_event: asyncio.Event = asyncio.Event()

async def startup() -> None:
    """初始化调度器，须在 run() 前调用一次。"""
    global _stop_event
    _stop_event = asyncio.Event()
    messageBus.subscribe(MessageBusTopic.ROOM_AGENT_TURN, _on_agent_turn)


def stop_agent_task(agent_id: int) -> None:
    """停止 Agent 对应的消费 task。"""
    try:
        agent = agentService.get_agent(agent_id)
    except KeyError:
        return
    agent.stop_consumer_task()


async def _on_agent_turn(msg: EventBusMessage) -> None:
    """订阅 ROOM_AGENT_TURN：创建任务记录，并在需要时启动消费协程。"""
    gt_agent: GtAgent = msg.payload["gt_agent"]
    agent_id: int = gt_agent.id
    room_id: int = msg.payload["room_id"]

    special_agent = SpecialAgent.value_of(agent_id)
    if special_agent is not None:
        logger.info(
            "跳过特殊成员回合调度: agent_id=%s, special_agent=%s, room_id=%s",
            agent_id,
            special_agent.name,
            room_id,
        )
        return

    room = chat_room.get_room(room_id)
    assert room is not None, f"room must exist before scheduling: room_id={room_id}, agent_id={agent_id}"
    agent = agentService.get_agent(gt_agent.id)

    # 去重：检查数据库中是否已有该房间的 PENDING 任务
    if await gtAgentTaskManager.has_pending_room_task(agent_id, room_id):
        logger.debug(f"跳过重复任务创建: agent_id={agent_id}, room_id={room_id}")
        return

    # 创建任务记录
    await gtAgentTaskManager.create_task(
        gt_agent.id,
        AgentTaskType.ROOM_MESSAGE,
        {"room_id": room_id},
    )

    agent.start_consumer_task()


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


def stop() -> None:
    """通知 run() 退出循环。"""
    _stop_event.set()


def shutdown() -> None:
    """清空调度状态，强制结束 run()。"""
    messageBus.unsubscribe(MessageBusTopic.ROOM_AGENT_TURN, _on_agent_turn)
    stop()
    for agent in agentService.get_all_agents():
        agent.stop_consumer_task()


def stop_team(team_id: int) -> None:
    """停止指定 Team 的所有运行中消费 task。"""
    team_agents = agentService.get_team_agents(team_id)
    for agent in team_agents:
        agent.stop_consumer_task()
    logger.info(f"Team ID={team_id} 的 {len(team_agents)} 个消费 task 已停止")

import logging

from service import messageBus
from service.messageBus import EventBusMessage
from service import agentService, roomService as chat_room
from dal.db import gtAgentTaskManager
from constants import MessageBusTopic, AgentTaskType, SpecialAgent
from model.dbModel.gtAgent import GtAgent

logger = logging.getLogger(__name__)

async def startup() -> None:
    """初始化调度器。"""
    messageBus.subscribe(MessageBusTopic.ROOM_STATUS_CHANGED, _on_room_status_changed)


def stop_agent_task(agent_id: int) -> None:
    """停止 Agent 对应的消费 task。"""
    try:
        agent = agentService.get_agent(agent_id)
    except KeyError:
        return
    agent.stop_consumer_task()


async def _on_room_status_changed(msg: EventBusMessage) -> None:
    """订阅 ROOM_STATUS_CHANGED：need_scheduling=True 时创建任务记录并在需要时启动消费协程。"""
    if not msg.payload["need_scheduling"]:
        return

    gt_agent: GtAgent = msg.payload["current_turn_agent"]
    agent_id: int = gt_agent.id
    room_id: int = msg.payload["gt_room"].id

    assert SpecialAgent.value_of(agent_id) is None, \
        f"need_scheduling=True must not be set for special agents: agent_id={agent_id}, room_id={room_id}"

    agent = agentService.get_agent(agent_id)

    # 去重：检查数据库中是否已有该房间的 PENDING 任务
    if await gtAgentTaskManager.has_pending_room_task(agent_id, room_id):
        logger.debug(f"跳过重复任务创建: agent_id={agent_id}, room_id={room_id}")
        return

    # 创建任务记录
    await gtAgentTaskManager.create_task(
        agent_id,
        AgentTaskType.ROOM_MESSAGE,
        {"room_id": room_id},
    )

    agent.start_consumer_task()


async def start_scheduling(team_name: str | None = None) -> None:
    """统一开始调度入口：激活/重放房间轮次事件。"""
    await chat_room.activate_rooms(team_name)
    logger.info("开始调度完成: team=%s", team_name or "ALL")


def shutdown() -> None:
    """清空调度状态。"""
    messageBus.unsubscribe(MessageBusTopic.ROOM_STATUS_CHANGED, _on_room_status_changed)
    for agent in agentService.get_all_agents():
        agent.stop_consumer_task()
    logger.info("Scheduler 已停止运行")
    for runtime_room in chat_room.get_all_rooms():
        logger.info(f"\n{runtime_room.format_log()}")


def stop_scheduler_team(team_id: int) -> None:
    """停止指定 Team 的所有运行中消费 task。"""
    team_agents = agentService.get_team_agents(team_id)
    for agent in team_agents:
        agent.stop_consumer_task()
    logger.info(f"Team ID={team_id} 的 {len(team_agents)} 个消费 task 已停止")

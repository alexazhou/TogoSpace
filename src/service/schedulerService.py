import logging

from service import messageBus
from service.messageBus import EventBusMessage
from service import agentService, roomService as chat_room, serviceUtil
from dal.db import gtScheculeTaskManager
from constants import MessageBusTopic, AgentTaskType, SpecialAgent, ScheduleState
from model.coreModel.serviceStatus import ServiceStatusInfo
from model.dbModel.gtAgent import GtAgent
from util import configUtil

logger = logging.getLogger(__name__)

_schedule_state: ScheduleState = ScheduleState.STOPPED
_schedule_not_running_reason: str = ""  # 调度未运行原因，仅在非 RUNNING 状态时可能有值

s_status = ServiceStatusInfo(name="schedulerService")


def get_schedule_state() -> ScheduleState:
    return _schedule_state


def get_schedule_not_running_reason() -> str:
    """获取调度未运行原因，仅在非 RUNNING 状态时可能有值。"""
    return _schedule_not_running_reason


def _publish_state_change() -> None:
    """发布调度状态变更事件。"""
    messageBus.publish(
        MessageBusTopic.SCHEDULE_STATE_CHANGED,
        schedule_state=_schedule_state.name,
        not_running_reason=_schedule_not_running_reason,
    )


def _reset() -> None:
    """纯资源清理：``shutdown()`` 的清理体；幂等。

    退订事件、停止所有 Agent 消费协程并复位调度状态。
    """
    global _schedule_state, _schedule_not_running_reason
    messageBus.unsubscribe(MessageBusTopic.ROOM_STATUS_CHANGED, _on_room_status_changed)
    for agent in agentService.get_all_agents():
        agent.stop_consumer_task()
    _schedule_state = ScheduleState.STOPPED
    _schedule_not_running_reason = ""
    logger.info("Scheduler 已停止运行")
    for runtime_room in chat_room.get_all_rooms():
        logger.info(f"\n{runtime_room.format_log()}")


def _unsubscribe() -> None:
    """收窄的启动回滚：``startup()`` 只申请「订阅」这一项资源，失败时只退订。

    回滚动作刻意不越出 ``startup`` 的申请范围（不退订之外的停 consumer/遍历房间属 ``shutdown`` 职责），
    保持 on_error 与「启动失败必回滚自身已申请资源」一一对应；幂等。
    """
    messageBus.unsubscribe(MessageBusTopic.ROOM_STATUS_CHANGED, _on_room_status_changed)


@serviceUtil.service_start_wrapper(s_status, on_error=_unsubscribe)
async def startup() -> None:
    """初始化调度器，订阅事件。需在 team 恢复完成后手动调用 start_schedule() 开启调度。"""
    # 幂等订阅：先退订再订阅，确保 ROOM_STATUS_CHANGED 回调只注册一次。
    messageBus.unsubscribe(MessageBusTopic.ROOM_STATUS_CHANGED, _on_room_status_changed)
    messageBus.subscribe(MessageBusTopic.ROOM_STATUS_CHANGED, _on_room_status_changed)


async def start_schedule() -> None:
    """检查前置条件并尝试开启调度。成功切到 RUNNING 并激活所有 team，否则切到 BLOCKED。"""
    global _schedule_state, _schedule_not_running_reason
    if configUtil.get_app_config().setting.demo_mode.read_only:
        _schedule_state = ScheduleState.BLOCKED
        _schedule_not_running_reason = "演示模式已冻结数据"
        logger.info("调度闸门已阻塞: state=%s, reason=%s", _schedule_state.value, _schedule_not_running_reason)
        _publish_state_change()
        return
    if configUtil.is_initialized():
        _schedule_state = ScheduleState.RUNNING
        _schedule_not_running_reason = ""
        logger.info("调度闸门已开启: state=%s", _schedule_state.value)
        _publish_state_change()
        await start_scheduling(team_name=None)
    else:
        _schedule_state = ScheduleState.BLOCKED
        _schedule_not_running_reason = "未配置大模型服务，请到后台配置大模型服务"
        logger.info("调度闸门已阻塞: state=%s, reason=%s", _schedule_state.value, _schedule_not_running_reason)
        _publish_state_change()


def stop_schedule(reason: str = "") -> None:
    """显式停止调度。

    Args:
        reason: 停止/阻塞原因，可选。
    """
    global _schedule_state, _schedule_not_running_reason
    _schedule_state = ScheduleState.BLOCKED if reason else ScheduleState.STOPPED
    _schedule_not_running_reason = reason
    logger.info("调度闸门已停止/阻塞: state=%s, reason=%s", _schedule_state.value, _schedule_not_running_reason)
    _publish_state_change()


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

    if _schedule_state != ScheduleState.RUNNING:
        return

    agent_id: int = msg.payload["current_turn_agent_id"]
    room_id: int = msg.payload["gt_room"].id

    assert SpecialAgent.value_of(agent_id) is None, \
        f"need_scheduling=True must not be set for special agents: agent_id={agent_id}, room_id={room_id}"

    agent = agentService.get_agent(agent_id)

    # 去重：检查数据库中是否已有该房间的 PENDING/FAILED 任务
    if await gtScheculeTaskManager.has_pending_room_task(agent_id, room_id, include_failed=True):
        logger.debug(f"跳过重复任务创建: agent_id={agent_id}, room_id={room_id}")
        agent.start_consumer_task()
        return

    # 创建任务记录
    await gtScheculeTaskManager.create_task(
        agent_id,
        AgentTaskType.ROOM_MESSAGE,
        {"room_id": room_id},
    )

    agent.start_consumer_task()


async def start_scheduling(team_name: str | None = None) -> None:
    """统一开始调度入口：激活/重放房间轮次事件。仅在 RUNNING 状态下执行。"""
    if _schedule_state != ScheduleState.RUNNING:
        logger.info("调度闸门未开启，跳过房间激活: state=%s, team=%s", _schedule_state.value, team_name or "ALL")
        return
    await chat_room.activate_rooms(team_name)
    logger.info("开始调度完成: team=%s", team_name or "ALL")


@serviceUtil.service_stop_wrapper(s_status)
def shutdown() -> None:
    """清空调度状态。"""
    _reset()


def stop_scheduler_team(team_id: int) -> None:
    """停止指定 Team 的所有运行中消费 task。"""
    team_agents = agentService.get_team_agents(team_id)
    for agent in team_agents:
        agent.stop_consumer_task()
    logger.info(f"Team ID={team_id} 的 {len(team_agents)} 个 Agent 已停止")

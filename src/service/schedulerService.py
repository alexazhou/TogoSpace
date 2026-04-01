import asyncio
import logging
from typing import Dict

from service import messageBus
from service.messageBus import Message
from model.coreModel.gtCoreAgentEvent import GtCoreRoomMessageEvent
from service import agentService, roomService as chat_room
from service.agentService import Agent
from dal.db import gtTeamManager
from constants import MessageBusTopic, MemberStatus, SpecialAgent

logger = logging.getLogger(__name__)

_team_max_fc: Dict[str, int] = {}
_running: Dict[str, asyncio.Task] = {}
_stop_event: asyncio.Event = asyncio.Event()

async def startup() -> None:
    """初始化调度器，须在 run() 前调用一次。"""
    global _team_max_fc, _stop_event
    _team_max_fc = {team.name: team.max_function_calls or 5 for team in await gtTeamManager.get_all_teams()}
    _stop_event = asyncio.Event()
    messageBus.subscribe(MessageBusTopic.ROOM_MEMBER_TURN, _on_member_turn)


def add_member(member: Agent, max_fc: int) -> None:
    """将成员加入调度池，若已在运行或处于 FAILED 状态则跳过。"""
    if member.status == MemberStatus.FAILED:
        return

    existing: asyncio.Task | None = _running.get(member.key)

    if existing is not None and existing.done() == False:
        return

    task = asyncio.create_task(member.consume_task(max_fc))
    _running[member.key] = task
    task.add_done_callback(lambda t: _on_task_done(member, t))


def _resolve_max_fc(team_name: str) -> int:
    return _team_max_fc.get(team_name, 5)


def _on_task_done(member: Agent, task: asyncio.Task) -> None:
    """Task 完成回调：仅清理当前任务，并在收尾竞态时自动续起消费。

    asyncio 在协程返回时立即将 task 标记为 done，但 done callback 通过
    loop.call_soon 异步调度，稍后才执行。在这段空隙内，同一成员可能已被
    重新入队并在 _running 中注册了新 task。此时若直接 remove_member，会误删
    新 task 并取消它。通过 `is` 判断确保只有"自己的"task 完成时才触发移除。
    """
    key = member.key
    if _running.get(key) is not task:
        return

    _running.pop(key, None)

    # 收尾竞态兜底：如果 task 结束时队列里还有事件，立即续起一个新 task。
    if not member.wait_task_queue.empty():
        logger.info("成员任务收尾时检测到待处理事件，自动续起消费: member=%s", key)
        add_member(member, _resolve_max_fc(member.team_name))


def remove_member(member_key: str) -> None:
    """从调度池移出成员。"""
    task = _running.pop(member_key, None)
    if task and not task.done():
        task.cancel()


def _on_member_turn(msg: Message) -> None:
    """订阅 ROOM_MEMBER_TURN：将任务入队，若成员未运行则加入调度池。"""
    member_name: str = msg.payload["member_name"]
    room_id: int = msg.payload["room_id"]
    team_name: str = msg.payload["team_name"]

    if SpecialAgent.value_of(member_name) == SpecialAgent.OPERATOR:
        logger.info(f"轮到人类操作者，系统进入等待状态: room_id={room_id}")
        return

    try:
        member: Agent = agentService.get_team_agent(team_name, member_name)
    except KeyError:
        logger.error(f"成员不存在: member_name={member_name}, team_name={team_name}")
        return
    except Exception as e:
        logger.error(f"获取成员失败: member_name={member_name}, team_name={team_name}, error={e}")
        return

    # 去重：同一房间已在队列中则跳过，避免重复调度
    queued_events = list(getattr(member.wait_task_queue, "_queue", []))
    if any(e.room_id == room_id for e in queued_events):
        logger.debug(f"跳过重复入队: member={member.key}, room_id={room_id}")
        return

    member.wait_task_queue.put_nowait(GtCoreRoomMessageEvent(room_id))

    add_member(member, _resolve_max_fc(team_name))


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
    global _team_max_fc, _running
    messageBus.unsubscribe(MessageBusTopic.ROOM_MEMBER_TURN, _on_member_turn)
    stop()
    _team_max_fc = {}
    for task in _running.values():
        if not task.done():
            task.cancel()
    _running = {}


async def refresh_team_config(team_name: str) -> None:
    """刷新指定 Team 的调度配置。"""
    team_row = await gtTeamManager.get_team(team_name)
    if team_row is None:
        _team_max_fc.pop(team_name, None)
        return
    _team_max_fc[team_name] = team_row.max_function_calls or 5
    logger.info(f"Team '{team_name}' 的调度配置已刷新")


def stop_team(team_name: str) -> None:
    """停止指定 Team 的所有调度任务。"""
    to_remove = [key for key in _running.keys() if key.endswith(f"@{team_name}")]
    for member_key in to_remove:
        remove_member(member_key)
    logger.info(f"Team '{team_name}' 的 {len(to_remove)} 个调度任务已停止")

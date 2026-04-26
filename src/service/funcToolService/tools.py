from __future__ import annotations
from typing import Any, Callable, Optional
import datetime
import logging
from zoneinfo import ZoneInfo

from constants import AgentStatus, RoomState, SpecialAgent
from dal.db import gtAgentManager, gtRoomManager
from model.dbModel.gtDept import GtDept
from service.roomService import ToolCallContext
import service.roomService as roomService
from util import configUtil, i18nUtil

logger = logging.getLogger(__name__)

# Tool 返回值规范
# 所有 tool 函数统一返回 dict，由 funcToolService.run_tool_call 序列化为 JSON 字符串后交给 LLM。
# 必填字段：
#   success: bool  — 操作是否成功
# 可选字段（按情况选用，不强制两者都有）：
#   message: str   — 文本信息（成功提示、错误说明等）
#   <其他字段>     — 结构化数据，字段名与语义一致，如 agents: list


def get_time(timezone: Optional[str] = None) -> dict:
    """获取当前时间

    Args:
        timezone: 可选的时区名称，如 "Asia/Shanghai"，默认使用本地时区
    """
    if timezone:
        try:
            tz = ZoneInfo(timezone)
            now = datetime.datetime.now(tz)
            return {"success": True, "message": f"当前时间（时区 {timezone}）: {now.strftime('%Y-%m-%d %H:%M:%S')}"}
        except Exception:
            return {"success": False, "message": f"未知时区: {timezone}"}
    else:
        now = datetime.datetime.now()
        return {"success": True, "message": f"当前本地时间: {now.strftime('%Y-%m-%d %H:%M:%S')}"}


def _require_team_context(_context: ToolCallContext | None) -> tuple[bool, int]:
    if _context is None or _context.team_id <= 0:
        return False, 0
    return True, _context.team_id


def _resolve_agent_name(agent_id: int, id_to_name: dict[int, str]) -> str:
    if agent_id == int(SpecialAgent.SYSTEM.value):
        return SpecialAgent.SYSTEM.name
    if agent_id == int(SpecialAgent.OPERATOR.value):
        return SpecialAgent.OPERATOR.name
    return id_to_name.get(agent_id, f"unknown({agent_id})")


def _find_dept_node(node: GtDept | None, dept_id: int) -> GtDept | None:
    if node is None:
        return None
    if node.id == dept_id:
        return node
    for child in node.children:
        found = _find_dept_node(child, dept_id)
        if found is not None:
            return found
    return None


def _serialize_dept_node(node: GtDept, id_to_name: dict[int, str]) -> dict[str, Any]:
    lang = configUtil.get_language()
    dept_name = i18nUtil.extract_i18n_str(
        node.i18n.get("dept_name") if node.i18n else None,
        default=node.name,
        lang=lang,
    ) or node.name
    responsibility = i18nUtil.extract_i18n_str(
        node.i18n.get("responsibility") if node.i18n else None,
        default=node.responsibility,
        lang=lang,
    ) or node.responsibility
    members = [_resolve_agent_name(agent_id, id_to_name) for agent_id in node.agent_ids]
    return {
        "dept_id": node.id,
        "dept_name": dept_name,
        "dept_responsibility": responsibility,
        "manager": _resolve_agent_name(node.manager_id, id_to_name),
        "members": members,
        "member_count": len(members),
        "children": [_serialize_dept_node(child, id_to_name) for child in node.children],
    }


async def _build_team_agent_name_map(team_id: int) -> dict[int, str]:
    # 临时优先复用运行态 Agent，拿不到时再回退 DB，避免工具在测试/恢复场景下名称缺失。
    try:
        from service import agentService

        team_agents = agentService.get_team_agents(team_id)
        if team_agents:
            return {agent.gt_agent.id: agent.gt_agent.name for agent in team_agents}
    except Exception:
        logger.debug("build team agent name map from runtime failed, fallback to db", exc_info=True)

    gt_agents = await gtAgentManager.get_team_all_agents(team_id)
    return {agent.id: agent.name for agent in gt_agents}


def _truncate_error_message(message: str | None, limit: int = 100) -> str:
    if not message:
        return ""
    if len(message) <= limit:
        return message
    return message[:limit].rstrip() + "..."


async def get_dept_info(dept_id: Optional[int] = None, _context: ToolCallContext = None) -> dict:
    """查询部门信息。不传 dept_id 时返回整个团队部门树，传入时返回指定部门及其子树。

    Args:
        dept_id: 部门 ID，省略时返回整个团队
    """
    ok, team_id = _require_team_context(_context)
    if not ok:
        return {"success": False, "message": "当前没有可用的团队上下文。"}

    from service import deptService

    root = await deptService.get_dept_tree(team_id)
    if root is None:
        return {"success": False, "message": "当前团队还没有部门信息。"}

    target = root if dept_id is None else _find_dept_node(root, dept_id)
    if target is None:
        return {"success": False, "message": f"未找到部门: dept_id={dept_id}"}

    id_to_name = await _build_team_agent_name_map(team_id)
    return {"success": True, "dept": _serialize_dept_node(target, id_to_name)}


async def get_room_info(room_name: Optional[str] = None, _context: ToolCallContext = None) -> dict:
    """查询房间信息。不传 room_name 时返回团队房间列表，传入时返回指定房间详情。

    Args:
        room_name: 房间名称，省略时返回所有房间
    """
    ok, team_id = _require_team_context(_context)
    if not ok:
        return {"success": False, "message": "当前没有可用的团队上下文。"}

    id_to_name = await _build_team_agent_name_map(team_id)

    if room_name is None:
        room_configs = await gtRoomManager.get_rooms_by_team(team_id)
        rooms: list[dict[str, Any]] = []
        for room_config in room_configs:
            runtime_room = roomService.get_room(room_config.id)
            rooms.append({
                "room_name": room_config.name,
                "room_type": room_config.type.name,
                "state": runtime_room.state.name if runtime_room is not None else RoomState.INIT.name,
                "members": [
                    _resolve_agent_name(agent_id, id_to_name)
                    for agent_id in (room_config.agent_ids or [])
                    if agent_id != int(SpecialAgent.SYSTEM.value)
                ],
                "member_count": len([
                    agent_id
                    for agent_id in (room_config.agent_ids or [])
                    if agent_id != int(SpecialAgent.SYSTEM.value)
                ]),
            })
        return {"success": True, "rooms": rooms}

    room_config = await gtRoomManager.get_room_by_team_and_name(team_id, room_name)
    if room_config is None:
        return {"success": False, "message": f"未找到房间: {room_name}"}

    runtime_room = roomService.get_room(room_config.id)
    room_dict: dict[str, Any] = {
        "room_name": room_config.name,
        "room_type": room_config.type.name,
        "state": runtime_room.state.name if runtime_room is not None else RoomState.INIT.name,
        "members": [
            _resolve_agent_name(agent_id, id_to_name)
            for agent_id in (room_config.agent_ids or [])
            if agent_id != int(SpecialAgent.SYSTEM.value)
        ],
        "member_count": len([
            agent_id
            for agent_id in (room_config.agent_ids or [])
            if agent_id != int(SpecialAgent.SYSTEM.value)
        ]),
        "current_turn": runtime_room._get_agent_stable_name(runtime_room.get_current_turn_agent_id()) if runtime_room is not None and runtime_room.state == RoomState.SCHEDULING else None,
        "total_messages": len(runtime_room.messages) if runtime_room is not None else 0,
    }
    return {"success": True, "room": room_dict}


async def get_agent_info(agent_name: Optional[str] = None, _context: ToolCallContext = None) -> dict:
    """查询 Agent 信息。不传 agent_name 时返回团队成员列表，传入时返回指定成员详情。

    Args:
        agent_name: Agent 名称，省略时返回所有 Agent
    """
    ok, team_id = _require_team_context(_context)
    if not ok:
        return {"success": False, "message": "当前没有可用的团队上下文。"}

    from service import agentService, deptService
    from dal.db import gtAgentTaskManager

    team_agents = agentService.get_team_agents(team_id)

    async def _build_agent_dict(agent: Any, *, detail: bool) -> dict[str, Any]:
        agent_id = agent.gt_agent.id
        dept = await deptService.get_agent_dept(team_id, agent_id)
        first_task = await gtAgentTaskManager.get_first_unfinish_task(agent_id) if agent.status == AgentStatus.FAILED else None
        info: dict[str, Any] = {
            "name": agent.gt_agent.name,
            "status": agent.status.name,
            "department": dept.name if dept is not None else "off_board",
        }
        if first_task is not None:
            info["error_summary"] = _truncate_error_message(first_task.error_message)
        if detail:
            info["role"] = "manager" if dept is not None and dept.manager_id == agent_id else "member"
            info["rooms"] = [
                room.name
                for room in roomService.get_all_rooms()
                if room.team_id == team_id and agent_id in room.get_agent_ids()
            ]
            info["can_wake_up"] = agent.status == AgentStatus.FAILED
        return info

    if agent_name is None:
        agents = [await _build_agent_dict(agent, detail=False) for agent in team_agents]
        return {"success": True, "agents": agents}

    target_agent = next((agent for agent in team_agents if agent.gt_agent.name == agent_name), None)
    if target_agent is None:
        return {"success": False, "message": f"未找到成员: {agent_name}"}

    return {"success": True, "agent": await _build_agent_dict(target_agent, detail=True)}


async def wake_up_agent(agent_name: str, _context: ToolCallContext = None) -> dict:
    """唤醒处于 FAILED 状态的 Agent，使其重新进入调度循环。

    Args:
        agent_name: 要唤醒的 Agent 名称
    """
    ok, team_id = _require_team_context(_context)
    if not ok:
        return {"success": False, "message": "当前没有可用的团队上下文。"}

    from service import agentService

    team_agents = agentService.get_team_agents(team_id)
    target_agent = next((agent for agent in team_agents if agent.gt_agent.name == agent_name), None)
    if target_agent is None:
        return {"success": False, "message": f"未找到成员: {agent_name}"}

    if target_agent.status != AgentStatus.FAILED:
        return {"success": False, "message": f"{agent_name} 当前状态为 {target_agent.status.name}，无需唤醒。"}

    try:
        await target_agent.resume_failed()
    except Exception as exc:
        return {"success": False, "message": f"唤醒 {agent_name} 失败: {exc}"}

    return {"success": True, "message": f"已成功唤醒 {agent_name}，该成员将重新进入调度循环。"}


async def send_chat_msg(room_name: str, msg: str, _context: ToolCallContext = None) -> dict:
    """向聊天窗口发送消息

    Args:
        room_name: 要发送消息的窗口名称
        msg: 要发送的消息
    """
    if _context is None:
        logger.warning("发送消息失败，聊天室上下文未设置")
        return {"success": False, "message": "当前没有可用的房间上下文。"}

    logger.info(f"发送消息: sender_id={_context.agent_id}, room={room_name}, msg={msg}")

    try:
        room_config = await gtRoomManager.get_room_by_team_and_name(_context.team_id, room_name)
        target_room = roomService.get_room(room_config.id) if room_config is not None else None
    except Exception:
        try:
            team_rooms = await gtRoomManager.get_rooms_by_team(_context.team_id)
            room_config = next((room for room in team_rooms if room.name == room_name), None)
            target_room = roomService.get_room(room_config.id) if room_config else None
        except Exception:
            target_room = None

    if target_room is None:
        logger.warning(f"send_chat_msg: 目标房间不存在 room={room_name} team_id={_context.team_id}")
        return {"success": False, "message": f"目标房间不存在: {room_name} (team_id={_context.team_id})"}

    if _context.chat_room is not None and target_room.room_id != _context.chat_room.room_id:
        sender_id = _context.agent_id
        if not target_room.can_post_message(sender_id):
            logger.warning(
                "send_chat_msg: 发言者不在目标房间 agents 中 sender_id=%s room=%s team_id=%s agents=%s",
                _context.agent_id,
                room_name,
                _context.team_id,
                target_room.get_agent_ids(),
            )
            return {"success": False, "message": f"你不在目标房间 {target_room.name} 中，发送失败。"}

    sender_id = _context.agent_id
    if not (_context.chat_room and _context.chat_room.can_post_message(sender_id)):
        logger.warning(f"send_chat_msg: 发言者不在当前房间中 sender_id={_context.agent_id}")
        return {"success": False, "message": f"发言者（agent_id={_context.agent_id}）不在当前房间中"}
    await target_room.add_message(sender_id, msg)

    if target_room is _context.chat_room:
        return {"success": True, "message": f"【状态更新】发言已成功同步到房间。你当前的发言任务已视为完成。如果没有其他辅助工具（如查看信息）需要调用，请立即执行 finish_chat_turn 提交并结束本轮。"}

    assert _context.chat_room is not None, "send_chat_msg: 跨房间发言时 chat_room 不应为 None"

    return {"success": True, "message": (
        f"【状态更新】消息已发送达 {target_room.name}。你当前的发言任务已视为完成。如果没有其他业务逻辑，请立即执行 finish_chat_turn 结束本轮。"
    )}


async def finish_chat_turn(_context: ToolCallContext = None) -> dict:
    """结束本轮行动。当你完成所有发言和工具调用后，必须调用此工具来把行动机会让给下一位成员。
    如果你觉得当前话题不需要回复，或者没有话要说，请直接调用此工具来跳过本轮。"""
    if _context is None or _context.chat_room is None:
        logger.warning("结束行动失败，聊天室上下文未设置")
        return {"success": False, "message": "当前没有激活的房间上下文。"}

    logger.info(f"Agent 结束行动: agent_id={_context.agent_id}")
    ok = await _context.chat_room.finish_turn(_context.agent_id)

    if not ok:
        current_id = _context.chat_room.get_current_turn_agent_id()
        logger.warning(f"finish_turn 被房间拒绝（发言位不匹配），但仍视为行动结束: agent_id={_context.agent_id}, current_turn_id={current_id}, room={_context.chat_room.key}")

    return {"success": True, "message": "已结束了本轮行动."}

FUNCTION_REGISTRY: dict[str, Callable[..., dict] | Callable[..., object]] = {
    "get_time": get_time,
    "send_chat_msg": send_chat_msg,
    "finish_chat_turn": finish_chat_turn,
    "get_dept_info": get_dept_info,
    "get_room_info": get_room_info,
    "get_agent_info": get_agent_info,
    "wake_up_agent": wake_up_agent,
}

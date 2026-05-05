from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Sequence

from dal.db import gtRoomManager, gtTeamManager, gtAgentManager, gtRoomMessageManager
from service import messageBus
from util import configUtil, assertUtil, i18nUtil
from exception import TogoException
from model.coreModel.gtCoreChatModel import GtCoreRoomMessage
from model.dbModel.gtDept import DeptRoomSpec
from model.dbModel.gtRoom import GtRoom
from model.dbModel.gtRoomMessage import GtRoomMessage
from model.dbModel.gtTeam import GtTeam
from model.dbModel.gtAgent import GtAgent
from constants import MessageBusTopic, RoomState, RoomType, SpecialAgent
from .chatRoom import ChatRoom

logger = logging.getLogger("service.roomService")


def resolve_room_max_turns(max_turns: int | None) -> int:
    if max_turns is not None:
        return max_turns
    return configUtil.get_app_config().setting.default_room_max_turns


@dataclass
class ToolCallContext:
    """工具调用时注入的上下文，包含当前 Agent、工具名和聊天室信息。"""
    agent_id: int
    team_id: int
    chat_room: ChatRoom
    tool_name: str = ""

_rooms: Dict[str, ChatRoom] = {}  # room_key -> ChatRoom
_rooms_by_id: Dict[int, ChatRoom] = {}


async def startup() -> None:
    """初始化房间服务，清空所有房间。"""
    _rooms.clear()
    _rooms_by_id.clear()


async def _load_room(
    gt_team: GtTeam,
    gt_room: GtRoom,
    agent_ids: List[int],
) -> None:
    """将数据库房间装载到运行态。"""
    room = ChatRoom(team=gt_team, room=gt_room, agent_ids=agent_ids)
    _rooms[room.key] = room
    _rooms_by_id[room.room_id] = room

    logger.info(f"创建并初始化聊天室: room_id={room.room_id}, type={room.room_type.name}, agent_ids={agent_ids}")
    if gt_room.max_turns > 0:
        logger.info(f"初始化轮次配置: room_id={room.room_id}, max_turns={gt_room.max_turns}")


async def load_team_rooms(team_id: int) -> None:
    """从数据库读取指定 Team 的房间配置，并重建对应的内存房间对象。"""
    gt_team = await gtTeamManager.get_team_by_id(team_id)
    if gt_team is None:
        logger.warning(f"加载 Team 房间失败: Team ID '{team_id}' 不存在")
        return

    await close_team_rooms(team_id)

    gt_rooms = await gtRoomManager.get_rooms_by_team(gt_team.id)
    for gt_room in gt_rooms:
        await _load_room(
            gt_team=gt_team,
            gt_room=gt_room,
            agent_ids=gt_room.agent_ids or [],
        )

    logger.info(f"Team '{gt_team.name}' 的内存房间已重建，共 {len(gt_rooms)} 个房间")


async def load_all_rooms() -> None:
    """从数据库读取所有房间配置，并创建对应的内存房间对象。"""
    for gt_team in await gtTeamManager.get_all_teams():
        for gt_room in await gtRoomManager.get_rooms_by_team(gt_team.id):
            await _load_room(
                gt_team=gt_team,
                gt_room=gt_room,
                agent_ids=gt_room.agent_ids or [],
            )


async def close_team_rooms(team_id: int) -> None:
    """关闭并移除指定 Team 的内存房间对象。"""
    to_close = [room_key for room_key, room in _rooms.items() if room.team_id == team_id]
    for room_key in to_close:
        room = _rooms.pop(room_key)
        _rooms_by_id.pop(room.room_id, None)
    logger.info(f"Team ID={team_id} 的 {len(to_close)} 个聊天室已关闭")


async def _restore_room_runtime_state(room: ChatRoom) -> None:
    """恢复单个房间的消息、已读指针和轮次进度。"""
    gt_room_messages = await gtRoomMessageManager.get_room_messages(room.room_id)
    agent_read_index, turn_pos = await gtRoomManager.get_room_state(room.room_id)
    recovered_from_db = bool(gt_room_messages)
    restored_messages: list[GtCoreRoomMessage] | None = None

    logger.info(f"[恢复状态] room={room.name}, room_id={room.room_id}, msg_count={len(gt_room_messages)}, read_index={agent_read_index}, turn_pos={turn_pos}")
    logger.info(f"[恢复状态-详细] room_id={room.room_id}, agent_read_index type={type(agent_read_index)}, turn_pos type={type(turn_pos)}")

    if gt_room_messages:
        restored_messages = []
        for row in gt_room_messages:
            # 从数据库获取 display_name（SYSTEM agent 也有数据库记录）
            agent = await gtAgentManager.get_agent_by_id(row.agent_id)
            assert agent, f"agent_id '{row.agent_id}' not found"
            sender_display_name = agent.display_name
            restored_messages.append(GtCoreRoomMessage(
                sender_id=row.agent_id,
                sender_display_name=sender_display_name,
                content=row.content,
                send_time=datetime.fromisoformat(row.send_time),
                insert_immediately=row.insert_immediately,
                seq=row.seq,
                db_id=row.id,
            ))

    if restored_messages is not None or agent_read_index is not None:
        room.inject_runtime_state(
            messages=restored_messages,
            agent_read_index=agent_read_index,
            turn_pos=turn_pos,
        )
    elif recovered_from_db and room.messages:
        room.mark_all_messages_read()

    room.rebuild_state_from_history(persisted_turn_pos=turn_pos if recovered_from_db else None)


async def restore_team_rooms_runtime_state(team_id: int) -> None:
    """恢复指定 Team 下所有内存房间的消息、已读指针和轮次进度。"""
    for room in get_all_rooms():
        if room.team_id != team_id:
            continue
        await _restore_room_runtime_state(room)


async def restore_all_rooms_runtime_state() -> None:
    """恢复所有内存房间的消息、已读指针和轮次进度。"""
    for room in get_all_rooms():
        await _restore_room_runtime_state(room)


def get_room_by_key(room_key: str) -> ChatRoom:
    """通过 room_key（room_name@team_name）返回聊天室实例。"""
    room = _rooms.get(room_key)
    if room is None:
        raise RuntimeError(f"聊天室 '{room_key}' 不存在")
    return room


def get_room(room_id: int) -> ChatRoom | None:
    """通过数据库主键 room_id 返回聊天室实例，不存在时返回 None。"""
    return _rooms_by_id.get(room_id)


async def get_room_messages_from_db(room_id: int) -> list[GtRoomMessage]:
    """从数据库加载房间消息，固定走持久层。"""
    return await gtRoomMessageManager.get_room_messages(room_id)


def get_all_rooms() -> List[ChatRoom]:
    """返回所有聊天室实例列表。"""
    return list(_rooms.values())


async def get_or_create_control_room(team_id: int, agent_id: int) -> tuple[ChatRoom, bool]:
    """获取或创建操作者与指定 Agent 的私聊控制房间。

    先查找 team 下包含该 agent_id 的 PRIVATE 房间；若不存在则自动创建并激活。
    返回 (ChatRoom, created)，created=True 表示本次新建。
    """
    gt_team = await gtTeamManager.get_team_by_id(team_id)
    assert gt_team is not None, f"team_id={team_id} not found"

    # 查找现有 PRIVATE 房间
    gt_room = await gtRoomManager.get_private_room_by_agent(team_id, agent_id)
    if gt_room is not None:
        room = _rooms_by_id.get(gt_room.id)
        if room is None:
            # DB 有记录但内存里没有（如重启后），重新装载
            await _load_room(gt_team=gt_team, gt_room=gt_room, agent_ids=gt_room.agent_ids or [])
            room = _rooms_by_id[gt_room.id]
        return room, False

    # 不存在则创建新控制房间
    gt_agent = await gtAgentManager.get_agent_by_id(agent_id)
    agent_name = gt_agent.name if gt_agent else str(agent_id)

    new_gt_room = GtRoom(
        team_id=team_id,
        name=agent_name,
        type=RoomType.PRIVATE,
        initial_topic="",
        max_turns=-1,
        agent_ids=[int(SpecialAgent.OPERATOR.value), agent_id],
    )
    saved_room = await gtRoomManager.save_room(new_gt_room)
    await _load_room(gt_team=gt_team, gt_room=saved_room, agent_ids=[int(SpecialAgent.OPERATOR.value), agent_id])

    room = _rooms_by_id[saved_room.id]
    await room.activate_scheduling()

    messageBus.publish(MessageBusTopic.ROOM_ADDED, gt_room=saved_room, team_id=team_id)
    logger.info(f"自动创建控制房间: room_id={saved_room.id}, agent_id={agent_id}")

    return room, True


def shutdown() -> None:
    """移除所有聊天室，程序退出前调用。"""
    _rooms.clear()
    _rooms_by_id.clear()


async def update_room_agents(room_id: int, agent_ids: list[int]) -> None:
    room = await gtRoomManager.get_room_by_id(room_id)
    assertUtil.assertNotNull(room, error_message=f"room_id '{room_id}' not found", error_code="room_not_found")

    agent_count = len(agent_ids or [])
    if agent_count < 2:
        raise TogoException(
            f"房间成员不足 2 人（当前 {agent_count} 人）",
            error_code="ROOM_AGENTS_TOO_FEW",
        )

    room.agent_ids = agent_ids
    await gtRoomManager.save_room(room)


async def overwrite_dept_rooms(team_id: int, rooms: Sequence[DeptRoomSpec]) -> None:
    """按部门房间信息同步 DEPT 房间。

    行为约定：
    - 以 biz_id 作为幂等键，存在则更新，不存在则创建。
    - 每个目标房间都会同步 Agent 列表为 spec.agent_ids。
    - 最后会删除 team 下不在本次 biz_id 列表中的 DEPT 房间。
    """
    # 去重并固定“目标态”：同一 biz_id 仅保留最后一条 spec。
    by_biz_id: dict[str, DeptRoomSpec] = {room.biz_id: room for room in rooms}

    for spec in by_biz_id.values():
        # 1) 按 biz_id 查找目标房间，不存在则初始化一个待创建对象。
        existing = await gtRoomManager.get_room_by_biz_id(team_id, spec.biz_id)
        room = existing or GtRoom(
            team_id=team_id,
            name="",
            type=RoomType.GROUP,
            initial_topic="",
            max_turns=10,
            agent_ids=[],
            biz_id=spec.biz_id,
            tags=["DEPT"],
        )

        room.team_id = team_id
        room.name = spec.name
        room.type = RoomType.GROUP
        # 从 i18n 解析 initial_topic，若无 i18n 则使用 spec.initial_topic
        if spec.i18n and "initial_topic" in spec.i18n:
            lang = configUtil.get_language()
            room.initial_topic = i18nUtil.extract_i18n_str(
                spec.i18n.get("initial_topic"),
                default=spec.initial_topic,
                lang=lang,
            ) or spec.initial_topic
        else:
            room.initial_topic = spec.initial_topic
        room.max_turns = resolve_room_max_turns(spec.max_turns)
        room.biz_id = spec.biz_id
        room.tags = ["DEPT"]
        room.i18n = spec.i18n or {}

        # 2) 保存房间元信息，再覆盖成员列表。
        saved_room = await gtRoomManager.save_room(room)
        await update_room_agents(saved_room.id, spec.agent_ids)

    # 3) 清理不在目标态中的历史 DEPT 房间。
    await gtRoomManager.delete_rooms_by_biz_ids_not_in(team_id, list(by_biz_id.keys()))


async def create_team_rooms(team_id: int, rooms: Sequence[GtRoom]) -> None:
    """创建 team rooms：要求 team 还没有任何房间。"""
    existing_rooms = await gtRoomManager.get_rooms_by_team(team_id)
    assertUtil.assertTrue(
        len(existing_rooms) == 0,
        error_message=f"team_id '{team_id}' already has rooms, use overwrite_team_rooms instead",
        error_code="TEAM_ROOMS_ALREADY_EXIST",
    )
    await batch_create_rooms(team_id, rooms)


async def batch_create_rooms(team_id: int, rooms: Sequence[GtRoom]) -> None:
    """批量创建房间（create-only）。若房间已存在则报错。"""
    room_list = list(rooms)
    seen_names: set[str] = set()
    for room in room_list:
        if room.id is not None:
            raise TogoException(
                f"create-only 场景不允许传入 room.id: '{room.id}'",
                error_code="ROOM_ID_NOT_ALLOWED_ON_CREATE",
            )

        if room.name in seen_names:
            raise TogoException(
                f"房间名称重复: '{room.name}'",
                error_code="ROOM_NAME_DUPLICATED",
            )
        seen_names.add(room.name)

    existing_rooms = await gtRoomManager.get_rooms_by_team_and_names(
        team_id,
        [room.name for room in room_list],
    )
    if existing_rooms:
        raise TogoException(
            f"房间名称已存在: '{existing_rooms[0].name}'",
            error_code="ROOM_ALREADY_EXISTS",
        )

    for room in room_list:
        room.team_id = team_id
    await gtRoomManager.batch_save_rooms(room_list)


async def overwrite_team_rooms(team_id: int, rooms: Sequence[GtRoom]) -> None:
    """常规更新流程：按目标房间集创建/更新房间，并清理已移除房间。"""
    current_rooms = await gtRoomManager.get_rooms_by_team(team_id)
    next_names = {room.name for room in rooms}
    next_ids = {room.id for room in rooms if room.id is not None}

    obsolete_room_ids = [
        room.id
        for room in current_rooms
        if room.id not in next_ids and room.name not in next_names and not room.biz_id
    ]
    for room_id in obsolete_room_ids:
        await gtRoomManager.delete_room(room_id)

    for room_input in rooms:
        room = await gtRoomManager.get_room_by_team_and_id_or_name(team_id, room_input.id, room_input.name)
        if room is None:
            room = GtRoom(
                team_id=team_id,
                name="",
                type=RoomType.GROUP,
                initial_topic="",
                max_turns=10,
                agent_ids=[],
                biz_id=None,
                tags=[],
            )

        room.team_id = team_id
        room.name = room_input.name
        room.type = room_input.type
        room.initial_topic = room_input.initial_topic
        room.max_turns = room_input.max_turns
        room.biz_id = room_input.biz_id
        room.tags = list(room_input.tags or [])
        room.agent_ids = list(room_input.agent_ids or [])
        await gtRoomManager.save_room(room)


def get_rooms_for_agent(team_id: int | None, agent_id: int) -> List[int]:
    """返回指定参与者所在的房间 room_id 列表。可选按 team 过滤。

    Args:
        team_id: Team ID，为 None 时不过滤
        agent_id: Agent ID
    """
    results = []
    for room in _rooms.values():
        if agent_id in room._agent_ids:
            if team_id is None or room.team_id == team_id:
                results.append(room.room_id)
    return results


async def activate_rooms(team_name: str | None = None) -> None:
    """统一激活入口：对目标房间调用 activate_scheduling（可按 team 过滤）。"""
    for room in _rooms.values():
        if team_name is not None and room.team_name != team_name:
            continue
        await room.activate_scheduling()

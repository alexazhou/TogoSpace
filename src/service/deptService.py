from __future__ import annotations

import logging
from typing import List

from pydantic import BaseModel, Field

from constants import EmployStatus, RoomType
from dal.db import gtDeptManager, gtAgentManager, gtRoomManager
from exception import TeamAgentException
from model.dbModel.gtDept import GtDept
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtRoom import GtRoom
from service import roomService
from util.configTypes import DeptNodeConfig

logger = logging.getLogger(__name__)


class DeptTreeNode(BaseModel):
    """部门树节点（API 请求/响应用）。

    - 请求时：dept_id 可选，用于增量更新匹配现有部门
    - 响应时：dept_id 必有值
    """
    dept_id: int | None = None
    dept_name: str
    dept_responsibility: str = ""
    manager: str
    members: List[str] = Field(default_factory=list)
    children: List["DeptTreeNode"] = Field(default_factory=list)


DeptTreeNode.model_rebuild()


async def import_dept_tree(team_id: int, node: DeptNodeConfig) -> None:
    """递归将 dept_tree 配置写入数据库（首次导入；根节点已存在时整棵树跳过）。"""
    existing = await gtDeptManager.get_dept_by_name(team_id, node.dept_name)
    if existing is not None:
        logger.info(f"dept_tree 已存在（team_id={team_id}），跳过导入")
        return

    await _import_node(team_id, node, parent_id=None)
    logger.info(f"dept_tree 导入完成（team_id={team_id}，根节点={node.dept_name}）")


async def set_dept_tree(team_id: int, root: DeptTreeNode) -> None:
    """增量更新部门树，同步部门房间，更新成员 employ_status。"""
    # 校验整棵树
    _validate_dept_update_tree(root)

    # 收集树中所有成员名
    all_member_names = _collect_update_member_names(root)

    # 收集新树中所有部门 ID（非空）
    new_dept_ids = _collect_update_dept_ids(root)

    # 获取现有部门
    existing_depts = await gtDeptManager.get_all_depts(team_id)

    # 删除不在新树中的部门（按 ID）
    to_delete = [d.id for d in existing_depts if d.id not in new_dept_ids]
    if to_delete:
        await GtDept.delete().where(GtDept.id.in_(to_delete)).aio_execute()  # type: ignore[attr-defined]

    # 增量更新/创建部门，并收集 ID 映射
    dept_ids_map: dict[str, int] = {}
    await _upsert_dept_update_node(team_id, root, parent_id=None, dept_ids_map=dept_ids_map)

    # 同步部门房间
    await _save_dept_update_room(team_id, root, dept_ids_map)

    # 清理不再需要的部门房间
    all_dept_biz_ids = [f"DEPT:{did}" for did in dept_ids_map.values()]
    await gtRoomManager.delete_rooms_by_biz_ids_not_in(team_id, all_dept_biz_ids)

    # 更新成员 employ_status
    all_agents = await gtAgentManager.get_agents_by_team(team_id)

    # 在树中的成员设为 ON_BOARD
    on_board_ids = [a.id for a in all_agents if a.name in all_member_names]
    if on_board_ids:
        await (
            GtAgent.update(employ_status=EmployStatus.ON_BOARD)
            .where(GtAgent.id.in_(on_board_ids))  # type: ignore[attr-defined]
            .aio_execute()
        )

    # 不在树中的成员设为 OFF_BOARD
    off_board_ids = [a.id for a in all_agents if a.name not in all_member_names]
    if off_board_ids:
        await (
            GtAgent.update(employ_status=EmployStatus.OFF_BOARD)
            .where(GtAgent.id.in_(off_board_ids))  # type: ignore[attr-defined]
            .aio_execute()
        )

    logger.info(f"部门树已更新（team_id={team_id}，on_board={len(on_board_ids)}，off_board={len(off_board_ids)}）")


def _validate_dept_update_tree(node: DeptTreeNode) -> None:
    """递归校验部门更新树，成员不足 2 人时报错。"""
    if len(node.members) < 2:
        raise TeamAgentException(
            f"部门 '{node.dept_name}' 成员不足 2 人，无法创建房间",
            error_code="DEPT_MEMBERS_TOO_FEW",
        )
    for child in node.children:
        _validate_dept_update_tree(child)


def _collect_update_member_names(node: DeptTreeNode) -> set[str]:
    """递归收集更新树中所有成员名。"""
    names = set(node.members)
    for child in node.children:
        names.update(_collect_update_member_names(child))
    return names


def _collect_update_dept_ids(node: DeptTreeNode) -> set[int]:
    """递归收集更新树中所有部门 ID（非空）。"""
    ids: set[int] = set()
    if node.dept_id is not None:
        ids.add(node.dept_id)
    for child in node.children:
        ids.update(_collect_update_dept_ids(child))
    return ids


async def _import_node(team_id: int, node: DeptNodeConfig, parent_id: int | None) -> GtDept:
    """递归导入单个节点，返回写入后的 GtDept 对象。"""
    # 校验：manager 必须出现在 members 中
    if node.manager not in node.members:
        raise TeamAgentException(
            f"部门 '{node.dept_name}' 的主管 '{node.manager}' 不在成员名单中",
            error_code="DEPT_MANAGER_NOT_IN_MEMBERS",
        )

    # 解析 agent_ids 和 manager_id
    agent_ids: list[int] = []
    manager_id: int | None = None
    for member_name in node.members:
        row = await gtAgentManager.get_agent(team_id, member_name)
        if row is None:
            raise TeamAgentException(
                f"部门 '{node.dept_name}' 的成员 '{member_name}' 在 team_members 中不存在",
                error_code="DEPT_MEMBER_NOT_FOUND",
            )
        agent_ids.append(row.id)
        if member_name == node.manager:
            manager_id = row.id

    assert manager_id is not None  # 前置校验已确保 manager in members

    dept = await gtDeptManager.upsert_dept(
        team_id=team_id,
        name=node.dept_name,
        responsibility=node.dept_responsibility,
        parent_id=parent_id,
        manager_id=manager_id,
        agent_ids=agent_ids,
    )

    for child in node.children:
        await _import_node(team_id, child, parent_id=dept.id)

    return dept


async def _upsert_dept_update_node(
    team_id: int,
    node: DeptTreeNode,
    parent_id: int | None,
    dept_ids_map: dict[str, int],
) -> GtDept:
    """增量更新/创建单个部门节点，返回 GtDept 对象。"""
    # 校验：manager 必须出现在 members 中
    if node.manager not in node.members:
        raise TeamAgentException(
            f"部门 '{node.dept_name}' 的主管 '{node.manager}' 不在成员名单中",
            error_code="DEPT_MANAGER_NOT_IN_MEMBERS",
        )

    # 解析 agent_ids 和 manager_id
    agent_ids: list[int] = []
    manager_id: int | None = None
    for member_name in node.members:
        row = await gtAgentManager.get_agent(team_id, member_name)
        if row is None:
            raise TeamAgentException(
                f"部门 '{node.dept_name}' 的成员 '{member_name}' 在 team_members 中不存在",
                error_code="DEPT_MEMBER_NOT_FOUND",
            )
        agent_ids.append(row.id)
        if member_name == node.manager:
            manager_id = row.id

    assert manager_id is not None

    # 按 dept_id 或 dept_name 匹配现有部门
    if node.dept_id is not None:
        # 优先按 ID 匹配
        existing = await gtDeptManager.get_dept_by_id(node.dept_id)
    else:
        # 按 name 匹配
        existing = await gtDeptManager.get_dept_by_name(team_id, node.dept_name)

    if existing:
        # 更新现有部门
        dept = await gtDeptManager.upsert_dept(
            team_id=team_id,
            name=node.dept_name,
            responsibility=node.dept_responsibility,
            parent_id=parent_id,
            manager_id=manager_id,
            agent_ids=agent_ids,
            dept_id=existing.id,
        )
    else:
        # 创建新部门
        dept = await gtDeptManager.upsert_dept(
            team_id=team_id,
            name=node.dept_name,
            responsibility=node.dept_responsibility,
            parent_id=parent_id,
            manager_id=manager_id,
            agent_ids=agent_ids,
        )

    # 收集部门名称到 ID 的映射
    dept_ids_map[node.dept_name] = dept.id

    # 递归处理子部门
    for child in node.children:
        await _upsert_dept_update_node(team_id, child, parent_id=dept.id, dept_ids_map=dept_ids_map)

    return dept


async def _save_dept_update_room(team_id: int, node: DeptTreeNode, dept_ids_map: dict[str, int]) -> None:
    """递归同步部门房间。"""
    dept_id = dept_ids_map[node.dept_name]
    biz_id = f"DEPT:{dept_id}"

    existing = await gtRoomManager.get_room_by_biz_id(team_id, biz_id)

    if existing:
        # 更新已有部门房间的名称/话题/标签，并同步成员
        existing.name = node.dept_name
        existing.type = RoomType.GROUP
        existing.initial_topic = node.dept_responsibility or f"{node.dept_name} 部门群聊"
        existing.max_turns = 10
        existing.biz_id = biz_id
        existing.tags = ["DEPT"]
        await gtRoomManager.save_room(existing)
        await roomService.save_room_members(existing.id, node.members)
    else:
        # 创建新房间，并添加部门成员
        room = await gtRoomManager.save_room(GtRoom(
            team_id=team_id,
            name=node.dept_name,
            type=RoomType.GROUP,
            initial_topic=node.dept_responsibility or f"{node.dept_name} 部门群聊",
            max_turns=10,
            agent_ids=[],
            biz_id=biz_id,
            tags=["DEPT"],
        ))
        await roomService.save_room_members(room.id, node.members)

    # 递归处理子部门
    for child in node.children:
        await _save_dept_update_room(team_id, child, dept_ids_map)


async def get_dept_tree_async(team_id: int) -> DeptTreeNode | None:
    """从 DB 重建树形结构，返回根节点；无部门时返回 None。"""
    return await _get_dept_tree_async(team_id)


async def _get_dept_tree_async(team_id: int) -> DeptTreeNode | None:
    all_depts = await gtDeptManager.get_all_depts(team_id)
    if not all_depts:
        return None

    # 建立 id -> GtDept 映射
    dept_map: dict[int, GtDept] = {d.id: d for d in all_depts}

    # 建立 id -> member names 映射（通过 agent_ids 反查 team_members）
    all_agents = await gtAgentManager.get_agents_by_team(team_id)
    agent_id_to_name: dict[int, str] = {m.id: m.name for m in all_agents}

    def build_node(dept: GtDept) -> DeptTreeNode:
        manager_name = agent_id_to_name.get(dept.manager_id, "")
        member_names = [agent_id_to_name[aid] for aid in dept.agent_ids if aid in agent_id_to_name]
        children = [
            build_node(dept_map[d.id])
            for d in all_depts
            if d.parent_id == dept.id
        ]
        return DeptTreeNode(
            dept_id=dept.id,
            dept_name=dept.name,
            dept_responsibility=dept.responsibility,
            manager=manager_name,
            members=member_names,
            children=children,
        )

    # 找根节点（parent_id 为 None）
    roots = [d for d in all_depts if d.parent_id is None]
    if not roots:
        return None
    return build_node(roots[0])


async def get_off_board_members(team_id: int) -> list[GtAgent]:
    """返回所有 employ_status=off_board 的成员。"""
    return await gtAgentManager.get_off_board_agents(team_id)


async def get_member_dept(team_id: int, member_name: str) -> GtDept | None:
    """查询成员所在部门；不在任何部门时返回 None。"""
    member = await gtAgentManager.get_agent(team_id, member_name)
    if member is None:
        return None
    all_depts = await gtDeptManager.get_all_depts(team_id)
    for dept in all_depts:
        if member.id in dept.agent_ids:
            return dept
    return None


async def move_member(
    team_id: int,
    member_name: str,
    target_dept_name: str,
    is_manager: bool = False,
) -> None:
    """将成员移入指定部门，可选设为主管。"""
    member = await gtAgentManager.get_agent(team_id, member_name)
    if member is None:
        raise TeamAgentException(
            f"成员 '{member_name}' 不存在",
            error_code="MEMBER_NOT_FOUND",
        )

    target_dept = await gtDeptManager.get_dept_by_name(team_id, target_dept_name)
    if target_dept is None:
        raise TeamAgentException(
            f"部门 '{target_dept_name}' 不存在",
            error_code="DEPT_NOT_FOUND",
        )

    all_depts = await gtDeptManager.get_all_depts(team_id)
    for dept in all_depts:
        if member.id in dept.agent_ids:
            new_ids = [mid for mid in dept.agent_ids if mid != member.id]
            await gtDeptManager.upsert_dept(
                team_id=dept.team_id,
                name=dept.name,
                responsibility=dept.responsibility,
                parent_id=dept.parent_id,
                manager_id=dept.manager_id,
                agent_ids=new_ids,
            )

    new_ids = list(target_dept.agent_ids)
    if member.id not in new_ids:
        new_ids.append(member.id)

    new_manager_id = member.id if is_manager else target_dept.manager_id
    await gtDeptManager.upsert_dept(
        team_id=target_dept.team_id,
        name=target_dept.name,
        responsibility=target_dept.responsibility,
        parent_id=target_dept.parent_id,
        manager_id=new_manager_id,
        agent_ids=new_ids,
    )

    await (
        GtAgent.update(employ_status=EmployStatus.ON_BOARD)
        .where((GtAgent.team_id == team_id) & (GtAgent.name == member_name))
        .aio_execute()
    )


async def remove_member(
    team_id: int,
    member_name: str,
    new_manager: str | None = None,
) -> None:
    """将成员从所在部门移除并设为 off_board。若其为主管，需指定新主管。"""
    member = await gtAgentManager.get_agent(team_id, member_name)
    if member is None:
        raise TeamAgentException(
            f"成员 '{member_name}' 不存在",
            error_code="MEMBER_NOT_FOUND",
        )

    member_dept = await get_member_dept(team_id, member_name)
    if member_dept is None:
        await (
            GtAgent.update(employ_status=EmployStatus.OFF_BOARD)
            .where((GtAgent.team_id == team_id) & (GtAgent.name == member_name))
            .aio_execute()
        )
        return

    is_manager = member.id == member_dept.manager_id
    if is_manager and new_manager is None:
        raise TeamAgentException(
            f"成员 '{member_name}' 是部门 '{member_dept.name}' 的主管，移除时必须指定新主管",
            error_code="MANAGER_REMOVAL_REQUIRES_NEW_MANAGER",
        )

    new_ids = [mid for mid in member_dept.agent_ids if mid != member.id]
    new_manager_id = member_dept.manager_id

    if is_manager and new_manager is not None:
        new_manager_row = await gtAgentManager.get_agent(team_id, new_manager)
        if new_manager_row is None:
            raise TeamAgentException(
                f"新主管 '{new_manager}' 不存在",
                error_code="MEMBER_NOT_FOUND",
            )
        if new_manager_row.id not in new_ids:
            raise TeamAgentException(
                f"新主管 '{new_manager}' 不在部门 '{member_dept.name}' 的成员名单中",
                error_code="NEW_MANAGER_NOT_IN_DEPT",
            )
        new_manager_id = new_manager_row.id

    await gtDeptManager.upsert_dept(
        team_id=member_dept.team_id,
        name=member_dept.name,
        responsibility=member_dept.responsibility,
        parent_id=member_dept.parent_id,
        manager_id=new_manager_id,
        agent_ids=new_ids,
    )
    await (
        GtAgent.update(employ_status=EmployStatus.OFF_BOARD)
        .where((GtAgent.team_id == team_id) & (GtAgent.name == member_name))
        .aio_execute()
    )


async def set_dept_manager(team_id: int, dept_name: str, manager_name: str) -> None:
    """变更部门主管，新主管必须已在该部门中。"""
    dept = await gtDeptManager.get_dept_by_name(team_id, dept_name)
    if dept is None:
        raise TeamAgentException(
            f"部门 '{dept_name}' 不存在",
            error_code="DEPT_NOT_FOUND",
        )

    manager_row = await gtAgentManager.get_agent(team_id, manager_name)
    if manager_row is None:
        raise TeamAgentException(
            f"成员 '{manager_name}' 不存在",
            error_code="MEMBER_NOT_FOUND",
        )

    if manager_row.id not in dept.agent_ids:
        raise TeamAgentException(
            f"成员 '{manager_name}' 不在部门 '{dept_name}' 的成员名单中",
            error_code="MEMBER_NOT_IN_DEPT",
        )

    await gtDeptManager.upsert_dept(
        team_id=dept.team_id,
        name=dept.name,
        responsibility=dept.responsibility,
        parent_id=dept.parent_id,
        manager_id=manager_row.id,
        agent_ids=dept.agent_ids,
    )

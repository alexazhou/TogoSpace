from __future__ import annotations

import logging
from typing import List

from constants import EmployStatus
from dal.db import gtDeptManager, gtAgentManager
from exception import TeamAgentException
from model.dbModel.gtDept import GtDept
from model.dbModel.gtAgent import GtAgent
from service import roomService, agentService

logger = logging.getLogger(__name__)


async def _hydrate_dept_ids(team_id: int, node: GtDept) -> None:
    if node.id is None:
        existing = await gtDeptManager.get_dept_by_name(team_id, node.name)
        if existing is not None:
            node.id = existing.id
    for child in node.children:
        await _hydrate_dept_ids(team_id, child)


async def overwrite_dept_tree(team_id: int, root: GtDept) -> None:
    """增量更新部门树，同步部门房间，更新成员 employ_status。"""
    await _hydrate_dept_ids(team_id, root)

    # 单次递归：校验整棵树 + 收集成员 ID 与部门 ID
    try:
        all_member_ids, new_dept_ids = root.validate_and_collect_tree_ids()
    except ValueError as exc:
        raise TeamAgentException(str(exc), error_code="DEPT_MEMBERS_TOO_FEW") from exc

    # 获取现有部门
    existing_depts = await gtDeptManager.get_all_depts(team_id)

    # 删除不在新树中的部门（按 ID）
    to_delete = [d.id for d in existing_depts if d.id not in new_dept_ids]
    if to_delete:
        await GtDept.delete().where(GtDept.id.in_(to_delete)).aio_execute()  # type: ignore[attr-defined]

    # 增量更新/创建部门
    saved_root = await _overwrite_dept_subtree(team_id, root, parent_id=None)

    # 同步部门房间（roomService 只接收房间信息，不感知部门树结构）
    await roomService.overwrite_dept_rooms(team_id, saved_root.collect_room_specs())

    # 更新成员 employ_status：树内成员 ON_BOARD，其他成员 OFF_BOARD
    on_board_count, off_board_count = await agentService.overwrite_team_agent_employ_status(team_id, all_member_ids)

    logger.info(f"部门树已更新（team_id={team_id}，on_board={on_board_count}，off_board={off_board_count}）")


async def _overwrite_dept_subtree(
    team_id: int,
    node: GtDept,
    parent_id: int | None,
) -> GtDept:
    """覆盖式保存部门子树：更新/创建当前节点，并递归处理子节点。"""
    # 校验：manager_id 必须出现在 agent_ids 中
    if node.manager_id not in node.agent_ids:
        raise TeamAgentException(
            f"部门 '{node.name}' 的主管 ID '{node.manager_id}' 不在成员名单中",
            error_code="DEPT_MANAGER_NOT_IN_MEMBERS",
        )

    agent_ids: list[int] = list(dict.fromkeys(node.agent_ids))
    member_rows = await gtAgentManager.get_team_agents_by_ids(team_id, agent_ids, include_special=False)
    existing_member_ids = {row.id for row in member_rows}
    missing_member_ids = sorted(set(agent_ids) - existing_member_ids)
    if missing_member_ids:
        raise TeamAgentException(
            f"部门 '{node.name}' 的成员 ID '{missing_member_ids}' 在 team_members 中不存在",
            error_code="DEPT_MEMBER_NOT_FOUND",
        )

    dept = await gtDeptManager.save_dept(
        team_id=team_id,
        name=node.name,
        responsibility=node.responsibility,
        parent_id=parent_id,
        manager_id=node.manager_id,
        agent_ids=agent_ids,
        dept_id=node.id,
    )

    # 递归处理子部门
    saved_children: list[GtDept] = []
    for child in node.children:
        saved_children.append(await _overwrite_dept_subtree(team_id, child, parent_id=dept.id))

    dept.children = saved_children

    return dept


async def get_dept_tree(team_id: int) -> GtDept | None:
    """从 DB 重建树形结构，返回根节点；无部门时返回 None。"""
    all_depts = await gtDeptManager.get_all_depts(team_id)
    if not all_depts:
        return None

    # 建立 parent_id -> children 映射，后续递归时 O(1) 获取子节点
    children_map: dict[int | None, list[GtDept]] = {}
    for dept in all_depts:
        children_map.setdefault(dept.parent_id, []).append(dept)

    def build_tree(dept: GtDept) -> GtDept:
        dept.children = [build_tree(child) for child in children_map.get(dept.id, [])]
        return dept

    # 找根节点（parent_id 为 None）
    roots = children_map.get(None, [])
    if not roots:
        return None
    return build_tree(roots[0])


async def get_off_board_members(team_id: int) -> list[GtAgent]:
    """返回所有 employ_status=off_board 的成员。"""
    return await gtAgentManager.get_agents_by_employ_status(team_id, EmployStatus.OFF_BOARD)


async def get_member_dept(team_id: int, agent_id: int) -> GtDept | None:
    """查询成员所在部门；不在任何部门时返回 None。"""
    all_depts = await gtDeptManager.get_all_depts(team_id)
    for dept in all_depts:
        if agent_id in dept.agent_ids:
            return dept
    return None


async def remove_member_from_dept(
    team_id: int,
    agent_id: int,
    new_manager_id: int | None = None,
) -> None:
    """将成员从所在部门移除并设为 off_board。若其为主管，需指定新主管。"""
    members = await gtAgentManager.get_team_agents_by_ids(team_id, [agent_id], include_special=False)
    if len(members) == 0:
        raise TeamAgentException(
            f"成员 ID '{agent_id}' 不存在",
            error_code="MEMBER_NOT_FOUND",
        )
    member = members[0]

    member_dept = await get_member_dept(team_id, agent_id)
    if member_dept is None:
        await (
            GtAgent.update(employ_status=EmployStatus.OFF_BOARD)
            .where((GtAgent.team_id == team_id) & (GtAgent.id == agent_id))
            .aio_execute()
        )
        return

    is_manager = member.id == member_dept.manager_id
    if is_manager and new_manager_id is None:
        raise TeamAgentException(
            f"成员 '{member.name}' 是部门 '{member_dept.name}' 的主管，移除时必须指定新主管",
            error_code="MANAGER_REMOVAL_REQUIRES_NEW_MANAGER",
        )

    new_ids = [mid for mid in member_dept.agent_ids if mid != member.id]
    next_manager_id = member_dept.manager_id

    if is_manager and new_manager_id is not None:
        new_manager_rows = await gtAgentManager.get_team_agents_by_ids(team_id, [new_manager_id], include_special=False)
        if len(new_manager_rows) == 0:
            raise TeamAgentException(
                f"新主管 ID '{new_manager_id}' 不存在",
                error_code="MEMBER_NOT_FOUND",
            )
        if new_manager_id not in new_ids:
            raise TeamAgentException(
                f"新主管 ID '{new_manager_id}' 不在部门 '{member_dept.name}' 的成员名单中",
                error_code="NEW_MANAGER_NOT_IN_DEPT",
            )
        next_manager_id = new_manager_id

    await gtDeptManager.save_dept(
        team_id=member_dept.team_id,
        name=member_dept.name,
        responsibility=member_dept.responsibility,
        parent_id=member_dept.parent_id,
        manager_id=next_manager_id,
        agent_ids=new_ids,
    )
    await (
        GtAgent.update(employ_status=EmployStatus.OFF_BOARD)
        .where((GtAgent.team_id == team_id) & (GtAgent.id == agent_id))
        .aio_execute()
    )


async def set_dept_manager(team_id: int, dept_name: str, manager_id: int) -> None:
    """变更部门主管，新主管必须已在该部门中。"""
    dept = await gtDeptManager.get_dept_by_name(team_id, dept_name)
    if dept is None:
        raise TeamAgentException(
            f"部门 '{dept_name}' 不存在",
            error_code="DEPT_NOT_FOUND",
        )

    managers = await gtAgentManager.get_team_agents_by_ids(team_id, [manager_id], include_special=False)
    if len(managers) == 0:
        raise TeamAgentException(
            f"成员 ID '{manager_id}' 不存在",
            error_code="MEMBER_NOT_FOUND",
        )

    if manager_id not in dept.agent_ids:
        raise TeamAgentException(
            f"成员 ID '{manager_id}' 不在部门 '{dept_name}' 的成员名单中",
            error_code="MEMBER_NOT_IN_DEPT",
        )

    await gtDeptManager.save_dept(
        team_id=dept.team_id,
        name=dept.name,
        responsibility=dept.responsibility,
        parent_id=dept.parent_id,
        manager_id=manager_id,
        agent_ids=dept.agent_ids,
    )

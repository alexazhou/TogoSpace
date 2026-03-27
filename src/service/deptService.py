from __future__ import annotations

import logging

from constants import EmployStatus
from dal.db import gtDeptManager, gtTeamMemberManager
from exception import TeamAgentException
from model.dbModel.gtDept import GtDept
from model.dbModel.gtTeamMember import GtTeamMember
from util.configTypes import DeptNodeConfig

logger = logging.getLogger(__name__)


async def import_dept_tree(team_id: int, node: DeptNodeConfig) -> None:
    """递归将 dept_tree 配置写入数据库（首次导入；根节点已存在时整棵树跳过）。"""
    existing = await gtDeptManager.get_dept_by_name(team_id, node.dept_name)
    if existing is not None:
        logger.info(f"dept_tree 已存在（team_id={team_id}），跳过导入")
        return

    await _import_node(team_id, node, parent_id=None)
    logger.info(f"dept_tree 导入完成（team_id={team_id}，根节点={node.dept_name}）")


async def _import_node(team_id: int, node: DeptNodeConfig, parent_id: int | None) -> GtDept:
    """递归导入单个节点，返回写入后的 GtDept 对象。"""
    # 校验：manager 必须出现在 members 中
    if node.manager not in node.members:
        raise TeamAgentException(
            f"部门 '{node.dept_name}' 的主管 '{node.manager}' 不在成员名单中",
            error_code="DEPT_MANAGER_NOT_IN_MEMBERS",
        )

    # 解析 member_ids 和 manager_id
    member_ids: list[int] = []
    manager_id: int | None = None
    for member_name in node.members:
        row = await gtTeamMemberManager.get_member(team_id, member_name)
        if row is None:
            raise TeamAgentException(
                f"部门 '{node.dept_name}' 的成员 '{member_name}' 在 team_members 中不存在",
                error_code="DEPT_MEMBER_NOT_FOUND",
            )
        member_ids.append(row.id)
        if member_name == node.manager:
            manager_id = row.id

    assert manager_id is not None  # 前置校验已确保 manager in members

    dept = await gtDeptManager.upsert_dept(
        team_id=team_id,
        name=node.dept_name,
        responsibility=node.dept_responsibility,
        parent_id=parent_id,
        manager_id=manager_id,
        member_ids=member_ids,
    )

    for child in node.children:
        await _import_node(team_id, child, parent_id=dept.id)

    return dept


async def get_dept_tree_async(team_id: int) -> DeptNodeConfig | None:
    """从 DB 重建树形结构，返回根节点；无部门时返回 None。"""
    return await _get_dept_tree_async(team_id)


async def _get_dept_tree_async(team_id: int) -> DeptNodeConfig | None:
    all_depts = await gtDeptManager.get_all_depts(team_id)
    if not all_depts:
        return None

    # 建立 id -> GtDept 映射
    dept_map: dict[int, GtDept] = {d.id: d for d in all_depts}

    # 建立 id -> member names 映射（通过 member_ids 反查 team_members）
    all_members = await gtTeamMemberManager.get_members_by_team(team_id)
    member_id_to_name: dict[int, str] = {m.id: m.name for m in all_members}

    def build_node(dept: GtDept) -> DeptNodeConfig:
        manager_name = member_id_to_name.get(dept.manager_id, "")
        member_names = [member_id_to_name[mid] for mid in dept.member_ids if mid in member_id_to_name]
        children = [
            build_node(dept_map[d.id])
            for d in all_depts
            if d.parent_id == dept.id
        ]
        return DeptNodeConfig(
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


async def move_member(
    team_id: int,
    member_name: str,
    target_dept_name: str,
    is_manager: bool = False,
) -> None:
    """将成员（含 off_board 成员）移入指定部门，可选设为主管。"""
    member = await gtTeamMemberManager.get_member(team_id, member_name)
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

    # 从现有部门中移除（若有）
    all_depts = await gtDeptManager.get_all_depts(team_id)
    for dept in all_depts:
        if member.id in dept.member_ids:
            new_ids = [mid for mid in dept.member_ids if mid != member.id]
            await gtDeptManager.upsert_dept(
                team_id=dept.team_id,
                name=dept.name,
                responsibility=dept.responsibility,
                parent_id=dept.parent_id,
                manager_id=dept.manager_id,
                member_ids=new_ids,
            )

    # 加入目标部门
    new_ids = list(target_dept.member_ids)
    if member.id not in new_ids:
        new_ids.append(member.id)

    new_manager_id = member.id if is_manager else target_dept.manager_id

    await gtDeptManager.upsert_dept(
        team_id=target_dept.team_id,
        name=target_dept.name,
        responsibility=target_dept.responsibility,
        parent_id=target_dept.parent_id,
        manager_id=new_manager_id,
        member_ids=new_ids,
    )

    # 将成员设为 on_board
    await (
        GtTeamMember.update(employ_status=EmployStatus.ON_BOARD)
        .where((GtTeamMember.team_id == team_id) & (GtTeamMember.name == member_name))
        .aio_execute()
    )

    logger.info(f"成员 '{member_name}' 已移入部门 '{target_dept_name}'（is_manager={is_manager}）")


async def remove_member(
    team_id: int,
    member_name: str,
    new_manager: str | None = None,
) -> None:
    """将成员从所在部门移除，设 employ_status=off_board。若成员为主管，new_manager 必须提供。"""
    member = await gtTeamMemberManager.get_member(team_id, member_name)
    if member is None:
        raise TeamAgentException(
            f"成员 '{member_name}' 不存在",
            error_code="MEMBER_NOT_FOUND",
        )

    # 找到成员所在部门
    all_depts = await gtDeptManager.get_all_depts(team_id)
    member_dept: GtDept | None = None
    for dept in all_depts:
        if member.id in dept.member_ids:
            member_dept = dept
            break

    if member_dept is None:
        # 不在任何部门，直接设为 off_board
        await (
            GtTeamMember.update(employ_status=EmployStatus.OFF_BOARD)
            .where((GtTeamMember.team_id == team_id) & (GtTeamMember.name == member_name))
            .aio_execute()
        )
        logger.info(f"成员 '{member_name}' 不在任何部门，直接设为 off_board")
        return

    is_manager = (member.id == member_dept.manager_id)
    if is_manager and new_manager is None:
        raise TeamAgentException(
            f"成员 '{member_name}' 是部门 '{member_dept.name}' 的主管，移除时必须指定新主管",
            error_code="MANAGER_REMOVAL_REQUIRES_NEW_MANAGER",
        )

    new_ids = [mid for mid in member_dept.member_ids if mid != member.id]
    new_manager_id = member_dept.manager_id

    if is_manager and new_manager is not None:
        new_manager_row = await gtTeamMemberManager.get_member(team_id, new_manager)
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

    # 原子更新：更新部门 + 设为 off_board
    await gtDeptManager.upsert_dept(
        team_id=member_dept.team_id,
        name=member_dept.name,
        responsibility=member_dept.responsibility,
        parent_id=member_dept.parent_id,
        manager_id=new_manager_id,
        member_ids=new_ids,
    )
    await (
        GtTeamMember.update(employ_status=EmployStatus.OFF_BOARD)
        .where((GtTeamMember.team_id == team_id) & (GtTeamMember.name == member_name))
        .aio_execute()
    )

    logger.info(
        f"成员 '{member_name}' 已从部门 '{member_dept.name}' 移除，设为 off_board"
        + (f"，新主管：'{new_manager}'" if is_manager else "")
    )


async def set_dept_manager(team_id: int, dept_name: str, manager_name: str) -> None:
    """变更部门主管（新主管必须已是该部门成员）。"""
    dept = await gtDeptManager.get_dept_by_name(team_id, dept_name)
    if dept is None:
        raise TeamAgentException(
            f"部门 '{dept_name}' 不存在",
            error_code="DEPT_NOT_FOUND",
        )

    manager_row = await gtTeamMemberManager.get_member(team_id, manager_name)
    if manager_row is None:
        raise TeamAgentException(
            f"成员 '{manager_name}' 不存在",
            error_code="MEMBER_NOT_FOUND",
        )

    if manager_row.id not in dept.member_ids:
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
        member_ids=dept.member_ids,
    )

    logger.info(f"部门 '{dept_name}' 主管已变更为 '{manager_name}'")


async def get_off_board_members(team_id: int) -> list[GtTeamMember]:
    """返回所有 employ_status=off_board 的成员。"""
    return await gtTeamMemberManager.get_off_board_members(team_id)


async def get_member_dept(team_id: int, member_name: str) -> GtDept | None:
    """查询成员所在部门；不在任何部门时返回 None。"""
    member = await gtTeamMemberManager.get_member(team_id, member_name)
    if member is None:
        return None
    all_depts = await gtDeptManager.get_all_depts(team_id)
    for dept in all_depts:
        if member.id in dept.member_ids:
            return dept
    return None

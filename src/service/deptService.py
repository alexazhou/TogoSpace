from __future__ import annotations

import logging
from typing import List

from pydantic import BaseModel, Field

from constants import EmployStatus
from dal.db import gtDeptManager, gtAgentManager
from exception import TeamAgentException
from model.dbModel.gtDept import GtDept
from model.dbModel.gtAgent import GtAgent
from service import roomService, agentService
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
    manager_id: int
    member_ids: List[int] = Field(default_factory=list)
    children: List["DeptTreeNode"] = Field(default_factory=list)

    def validate_and_collect(self) -> tuple[set[int], set[int]]:
        """递归校验节点并收集成员 ID 与非空 dept_id。"""
        if len(self.member_ids) < 2:
            raise TeamAgentException(
                f"部门 '{self.dept_name}' 成员不足 2 人，无法创建房间",
                error_code="DEPT_MEMBERS_TOO_FEW",
            )

        member_ids: set[int] = set(self.member_ids)
        dept_ids: set[int] = {self.dept_id} if self.dept_id is not None else set()

        for child in self.children:
            child_member_ids, child_dept_ids = child.validate_and_collect()
            member_ids.update(child_member_ids)
            dept_ids.update(child_dept_ids)

        return member_ids, dept_ids


DeptTreeNode.model_rebuild()


async def import_dept_tree(team_id: int, node: DeptNodeConfig) -> None:
    """递归将 dept_tree 配置写入数据库（首次导入；根节点已存在时整棵树跳过）。"""
    existing = await gtDeptManager.get_dept_by_name(team_id, node.dept_name)
    if existing is not None:
        logger.info(f"dept_tree 已存在（team_id={team_id}），跳过导入")
        return

    await _import_node(team_id, node, parent_id=None)
    logger.info(f"dept_tree 导入完成（team_id={team_id}，根节点={node.dept_name}）")


async def save_dept_tree(team_id: int, root: DeptTreeNode) -> None:
    """增量更新部门树，同步部门房间，更新成员 employ_status。"""
    # 单次递归：校验整棵树 + 收集成员 ID 与部门 ID
    all_member_ids, new_dept_ids = root.validate_and_collect()

    # 获取现有部门
    existing_depts = await gtDeptManager.get_all_depts(team_id)

    # 删除不在新树中的部门（按 ID）
    to_delete = [d.id for d in existing_depts if d.id not in new_dept_ids]
    if to_delete:
        await GtDept.delete().where(GtDept.id.in_(to_delete)).aio_execute()  # type: ignore[attr-defined]

    # 增量更新/创建部门，并收集 ID 映射
    dept_ids_map: dict[str, int] = {}
    await _save_dept_update_node(team_id, root, parent_id=None, dept_ids_map=dept_ids_map)

    # 同步部门房间（roomService 只接收房间信息，不感知部门树结构）
    dept_rooms: list[roomService.DeptRoomSpec] = []
    _collect_dept_room_specs(root, dept_ids_map, dept_rooms)
    await roomService.sync_dept_rooms(team_id, dept_rooms)

    # 更新成员 employ_status：树内成员 ON_BOARD，其他成员 OFF_BOARD
    on_board_count, off_board_count = await agentService.sync_team_agent_employ_status(team_id, all_member_ids)

    logger.info(f"部门树已更新（team_id={team_id}，on_board={on_board_count}，off_board={off_board_count}）")

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

    dept = await gtDeptManager.save_dept(
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


async def _save_dept_update_node(
    team_id: int,
    node: DeptTreeNode,
    parent_id: int | None,
    dept_ids_map: dict[str, int],
) -> GtDept:
    """增量更新/创建单个部门节点，返回 GtDept 对象。"""
    # 校验：manager_id 必须出现在 member_ids 中
    if node.manager_id not in node.member_ids:
        raise TeamAgentException(
            f"部门 '{node.dept_name}' 的主管 ID '{node.manager_id}' 不在成员名单中",
            error_code="DEPT_MANAGER_NOT_IN_MEMBERS",
        )

    agent_ids: list[int] = list(dict.fromkeys(node.member_ids))
    member_rows = await gtAgentManager.get_team_agents_by_ids(team_id, agent_ids, include_special=False)
    existing_member_ids = {row.id for row in member_rows}
    missing_member_ids = [member_id for member_id in agent_ids if member_id not in existing_member_ids]
    if missing_member_ids:
        raise TeamAgentException(
            f"部门 '{node.dept_name}' 的成员 ID '{missing_member_ids[0]}' 在 team_members 中不存在",
            error_code="DEPT_MEMBER_NOT_FOUND",
        )

    # 按 dept_id 或 dept_name 匹配现有部门
    if node.dept_id is not None:
        # 优先按 ID 匹配
        existing = await GtDept.aio_get_or_none(GtDept.id == node.dept_id)
    else:
        # 按 name 匹配
        existing = await gtDeptManager.get_dept_by_name(team_id, node.dept_name)

    if existing:
        # 更新现有部门
        dept = await gtDeptManager.save_dept(
            team_id=team_id,
            name=node.dept_name,
            responsibility=node.dept_responsibility,
            parent_id=parent_id,
            manager_id=node.manager_id,
            agent_ids=agent_ids,
            dept_id=existing.id,
        )
    else:
        # 创建新部门
        dept = await gtDeptManager.save_dept(
            team_id=team_id,
            name=node.dept_name,
            responsibility=node.dept_responsibility,
            parent_id=parent_id,
            manager_id=node.manager_id,
            agent_ids=agent_ids,
        )

    # 收集部门名称到 ID 的映射
    dept_ids_map[node.dept_name] = dept.id

    # 递归处理子部门
    for child in node.children:
        await _save_dept_update_node(team_id, child, parent_id=dept.id, dept_ids_map=dept_ids_map)

    return dept


def _collect_dept_room_specs(
    node: DeptTreeNode,
    dept_ids_map: dict[str, int],
    rooms: list[roomService.DeptRoomSpec],
) -> None:
    dept_id = dept_ids_map[node.dept_name]
    rooms.append(roomService.DeptRoomSpec(
        biz_id=f"DEPT:{dept_id}",
        name=node.dept_name,
        initial_topic=node.dept_responsibility or f"{node.dept_name} 部门群聊",
        member_ids=list(dict.fromkeys(node.member_ids)),
    ))
    for child in node.children:
        _collect_dept_room_specs(child, dept_ids_map, rooms)


async def get_dept_tree(team_id: int) -> DeptTreeNode | None:
    """从 DB 重建树形结构，返回根节点；无部门时返回 None。"""
    all_depts = await gtDeptManager.get_all_depts(team_id)
    if not all_depts:
        return None

    # 建立 parent_id -> children 映射，后续递归时 O(1) 获取子节点
    children_map: dict[int | None, list[GtDept]] = {}
    for dept in all_depts:
        children_map.setdefault(dept.parent_id, []).append(dept)

    def build_node(dept: GtDept) -> DeptTreeNode:
        children = [build_node(child) for child in children_map.get(dept.id, [])]
        return DeptTreeNode(
            dept_id=dept.id,
            dept_name=dept.name,
            dept_responsibility=dept.responsibility,
            manager_id=dept.manager_id,
            member_ids=list(dept.agent_ids),
            children=children,
        )

    # 找根节点（parent_id 为 None）
    roots = children_map.get(None, [])
    if not roots:
        return None
    return build_node(roots[0])


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
    manager_row = managers[0]

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
        manager_id=manager_row.id,
        agent_ids=dept.agent_ids,
    )

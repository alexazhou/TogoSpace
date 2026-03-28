from __future__ import annotations

import logging

from constants import EmployStatus
from dal.db import gtDeptManager, gtAgentManager
from exception import TeamAgentException
from model.dbModel.gtDept import GtDept
from model.dbModel.gtAgent import GtAgent
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


async def set_dept_tree(team_id: int, root: DeptNodeConfig) -> None:
    """完全替换部门树：删除现有部门，导入新树，更新成员 employ_status。"""
    # 收集树中所有成员名
    all_member_names = _collect_member_names(root)

    # 删除现有部门
    await gtDeptManager.delete_all_depts(team_id)

    # 导入新树
    await _import_node(team_id, root, parent_id=None)

    # 更新成员 employ_status
    all_agents = await gtAgentManager.get_agents_by_team(team_id)
    agent_id_to_name = {a.id: a.name for a in all_agents}

    # 在树中的成员设为 ON_BOARD
    on_board_ids = [a.id for a in all_agents if a.name in all_member_names]
    if on_board_ids:
        await (
            GtAgent.update(employ_status=EmployStatus.ON_BOARD)
            .where(GtAgent.id.in_(on_board_ids))
            .aio_execute()
        )

    # 不在树中的成员设为 OFF_BOARD
    off_board_ids = [a.id for a in all_agents if a.name not in all_member_names]
    if off_board_ids:
        await (
            GtAgent.update(employ_status=EmployStatus.OFF_BOARD)
            .where(GtAgent.id.in_(off_board_ids))
            .aio_execute()
        )

    logger.info(f"部门树已更新（team_id={team_id}，on_board={len(on_board_ids)}，off_board={len(off_board_ids)}）")


def _collect_member_names(node: DeptNodeConfig) -> set[str]:
    """递归收集树中所有成员名。"""
    names = set(node.members)
    for child in node.children:
        names.update(_collect_member_names(child))
    return names


async def _import_node(team_id: int, node: DeptNodeConfig, parent_id: int | None) -> GtDept:
    """递归导入单个节点，返回写入后的 GtDept 对象。"""
    # 校验：manager 必须出现在 members 中
    if node.manager not in node.members:
        raise TeamAgentException(
            f"部门 '{node.dept_name}' 的主管 '{node.manager}' 不在成员名单中",
            error_code="DEPT_MANAGER_NOT_IN_MEMBERS",
        )

    # 解析 member_ids 和 manager_id
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


async def get_dept_tree_async(team_id: int) -> DeptNodeConfig | None:
    """从 DB 重建树形结构，返回根节点；无部门时返回 None。"""
    return await _get_dept_tree_async(team_id)


async def _get_dept_tree_async(team_id: int) -> DeptNodeConfig | None:
    all_depts = await gtDeptManager.get_all_depts(team_id)
    if not all_depts:
        return None

    # 建立 id -> GtDept 映射
    dept_map: dict[int, GtDept] = {d.id: d for d in all_depts}

    # 建立 id -> member names 映射（通过 agent_ids 反查 team_members）
    all_agents = await gtAgentManager.get_agents_by_team(team_id)
    agent_id_to_name: dict[int, str] = {m.id: m.name for m in all_agents}

    def build_node(dept: GtDept) -> DeptNodeConfig:
        manager_name = agent_id_to_name.get(dept.manager_id, "")
        member_names = [agent_id_to_name[aid] for aid in dept.agent_ids if aid in agent_id_to_name]
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
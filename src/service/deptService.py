from __future__ import annotations

import logging
from typing import List

from constants import EmployStatus
from dal.db import gtDeptManager, gtAgentManager, gtTeamManager
from exception import TogoException
from model.dbModel.gtDept import GtDept
from model.dbModel.gtAgent import GtAgent
from service import roomService, agentService
from util import assertUtil

logger = logging.getLogger(__name__)


async def overwrite_dept_tree(team_id: int, root: GtDept) -> None:
    """增量更新部门树，同步部门房间，更新 Agent employ_status。"""
    team = await gtTeamManager.get_team_by_id(team_id)
    assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")
    assertUtil.assertFalse(team.enabled, error_message="团队必须处于停用状态才能编辑组织树", error_code="team_not_stopped")

    # 单次递归：校验整棵树 + 收集 Agent ID。
    try:
        all_agent_ids, _ = root.validate_and_collect_tree_ids()
    except ValueError as exc:
        raise TogoException(str(exc), error_code="DEPT_MEMBERS_TOO_FEW") from exc
    input_dept_ids = root.collect_dept_ids()

    # 先删除不在传入 id 集合中的旧部门，再执行覆盖保存。
    existing_depts = await gtDeptManager.get_all_depts(team_id)
    to_delete = [d.id for d in existing_depts if d.id not in input_dept_ids]
    if to_delete:
        await GtDept.delete().where(GtDept.id.in_(to_delete)).aio_execute()  # type: ignore[attr-defined]

    # 增量更新/创建部门（有 id 更新、无 id 新建）
    saved_root = await _overwrite_dept_subtree(team_id, root, parent_id=None)

    # 同步部门房间（roomService 只接收房间信息，不感知部门树结构）
    await roomService.overwrite_dept_rooms(team_id, saved_root.collect_room_specs())

    # 更新 Agent employ_status：树内 Agent ON_BOARD，其他 Agent OFF_BOARD
    on_board_count, off_board_count = await agentService.overwrite_team_agent_employ_status(team_id, all_agent_ids)

    logger.info(f"部门树已更新（team_id={team_id}，on_board={on_board_count}，off_board={off_board_count}）")


async def _overwrite_dept_subtree(
    team_id: int,
    node: GtDept,
    parent_id: int | None,
) -> GtDept:
    """覆盖式保存部门子树：更新/创建当前节点，并递归处理子节点。"""
    # 校验：manager_id 必须出现在 agent_ids 中
    if node.manager_id not in node.agent_ids:
        raise TogoException(
            f"部门 '{node.name}' 的主管 ID '{node.manager_id}' 不在 Agent 名单中",
            error_code="DEPT_MANAGER_NOT_IN_AGENTS",
        )

    agent_ids: list[int] = list(dict.fromkeys(node.agent_ids))
    gt_agents = await gtAgentManager.get_team_agents_by_ids(team_id, agent_ids, include_special=False)
    existing_agent_ids = {row.id for row in gt_agents}
    missing_agent_ids = sorted(set(agent_ids) - existing_agent_ids)
    if missing_agent_ids:
        raise TogoException(
            f"部门 '{node.name}' 的 Agent ID '{missing_agent_ids}' 在 team_agents 中不存在",
            error_code="DEPT_AGENT_NOT_FOUND",
        )

    dept = await gtDeptManager.save_dept(
        team_id=team_id,
        name=node.name,
        responsibility=node.responsibility,
        parent_id=parent_id,
        manager_id=node.manager_id,
        agent_ids=agent_ids,
        dept_id=node.id,
        i18n=node.i18n,
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


async def get_off_board_agents(team_id: int) -> list[GtAgent]:
    """返回所有 employ_status=off_board 的 Agent。"""
    return await gtAgentManager.get_team_agents(team_id, EmployStatus.OFF_BOARD)


async def get_agent_dept(team_id: int, agent_id: int) -> GtDept | None:
    """查询 Agent 所在部门；不在任何部门时返回 None。"""
    all_depts = await gtDeptManager.get_all_depts(team_id)
    for dept in all_depts:
        if agent_id in dept.agent_ids:
            return dept
    return None


async def set_dept_manager(team_id: int, dept_name: str, manager_id: int) -> None:
    """变更部门主管，新主管必须已在该部门中。"""
    dept = await gtDeptManager.get_dept_by_name(team_id, dept_name)
    if dept is None:
        raise TogoException(
            f"部门 '{dept_name}' 不存在",
            error_code="DEPT_NOT_FOUND",
        )

    managers = await gtAgentManager.get_team_agents_by_ids(team_id, [manager_id], include_special=False)
    if len(managers) == 0:
        raise TogoException(
            f"Agent ID '{manager_id}' 不存在",
            error_code="AGENT_NOT_FOUND",
        )

    if manager_id not in dept.agent_ids:
        raise TogoException(
            f"Agent ID '{manager_id}' 不在部门 '{dept_name}' 的 Agent 名单中",
            error_code="AGENT_NOT_IN_DEPT",
        )

    await gtDeptManager.save_dept(
        team_id=dept.team_id,
        name=dept.name,
        responsibility=dept.responsibility,
        parent_id=dept.parent_id,
        manager_id=manager_id,
        agent_ids=dept.agent_ids,
        dept_id=dept.id,
        i18n=dept.i18n,
    )

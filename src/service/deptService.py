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

    # 单次递归：校验整棵树。
    try:
        root.validate_tree()
    except ValueError as exc:
        raise TogoException(str(exc), error_code="DEPT_MEMBERS_TOO_FEW") from exc
    all_agent_ids, input_dept_ids = root.collect_dept_and_agent_ids()

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
    gt_agents = await gtAgentManager.get_team_agents_by_ids(team_id, agent_ids)
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


async def upsert_dept(
    team_id: int,
    name: str,
    responsibility: str,
    manager_id: int,
    agent_ids: list[int],
    parent_id: int | None,
    dept_id: int | None = None,
    i18n: dict | None = None,
) -> GtDept:
    """创建或更新单个部门，并处理跨部门成员冲突：

    - 将当前成员从其他部门移除（负责人可保留在父部门中）
    - 确保负责人出现在父部门成员列表中
    """
    agent_rows = await gtAgentManager.get_team_agents_by_ids(team_id, agent_ids)
    agent_map = {a.id: a for a in agent_rows}
    missing = [aid for aid in agent_ids if aid not in agent_map]
    if missing:
        raise TogoException(
            f"以下成员 ID 不存在于团队中: {missing}",
            error_code="DEPT_AGENT_NOT_FOUND",
        )

    all_depts = await gtDeptManager.get_all_depts(team_id)
    dept_map: dict[int, GtDept] = {d.id: d for d in all_depts if d.id is not None}
    members_set = set(agent_ids)

    # 指定的 leader 不能已经是其他部门的 leader
    for dept in all_depts:
        if dept.id == dept_id:
            continue
        if dept.manager_id == manager_id:
            raise TogoException(
                f"成员 ID '{manager_id}' 已是部门 '{dept.name}' 的负责人，一个成员只能担任一个部门的负责人",
                error_code="DEPT_MANAGER_ALREADY_LEADS",
            )

    # ── 成员冲突检测 ──
    # 约束：一个成员只能属于一个部门（子部门主管可同时属于父部门）。
    # 遍历所有部门（= 原部门），检查新部门成员是否还在原部门中：
    depts_to_update: dict[int, list[int]] = {}
    for dept in all_depts:
        if dept.id == dept_id:
            continue
        new_ids: list[int] = []
        changed = False
        for aid in dept.agent_ids:
            # 成员不在新部门名单 → 留在原部门
            if aid not in members_set:
                new_ids.append(aid)
            else:
                # 成员在新部门名单中，判断是否从原部门移除

                # 分支一：该成员是新部门的 manager
                if aid == manager_id:
                    if dept.id == parent_id:
                        new_ids.append(aid)             # 放行：新部门 manager 可留在父部门（原部门）
                    else:
                        changed = True                   # 移除：新部门 manager 不能留在其他原部门

                # 分支二：该成员是新部门的普通员工
                elif aid == dept.manager_id:
                    if dept_id is not None and dept.parent_id == dept_id:
                        new_ids.append(aid)             # 放行：原部门主管（子部门）可留在新部门（父部门）
                    else:
                        raise TogoException(             # 阻止：不能将其他部门主管移入新部门
                            f"成员 ID '{aid}' 是部门 '{dept.name}' 的负责人，无法将其移入新部门，请先更换 '{dept.name}' 的负责人",
                            error_code="DEPT_MANAGER_CONFLICT",
                        )
                else:
                    changed = True                       # 移除：普通员工归属新部门，从原部门移除

        if changed:
            depts_to_update[dept.id] = new_ids

    # 确保负责人在父部门成员中
    if parent_id is not None and parent_id in dept_map:
        parent_ids = depts_to_update.get(parent_id, list(dept_map[parent_id].agent_ids))
        if manager_id not in parent_ids:
            depts_to_update[parent_id] = [manager_id] + parent_ids

    # 批量保存受影响的其他部门
    for affected_id, new_ids in depts_to_update.items():
        affected = dept_map[affected_id]
        await gtDeptManager.save_dept(
            team_id=team_id,
            name=affected.name,
            responsibility=affected.responsibility,
            parent_id=affected.parent_id,
            manager_id=affected.manager_id,
            agent_ids=new_ids,
            dept_id=affected_id,
            i18n=affected.i18n or None,
        )

    saved: GtDept = await gtDeptManager.save_dept(
        team_id=team_id,
        name=name,
        responsibility=responsibility,
        parent_id=parent_id,
        manager_id=manager_id,
        agent_ids=agent_ids,
        dept_id=dept_id,
        i18n=i18n,
    )

    # 同步部门房间（与 overwrite_dept_tree 保持一致）
    tree: GtDept = await get_dept_tree(team_id)
    assert tree is not None, "upsert_dept 之后部门树不应为空"
    await roomService.overwrite_dept_rooms(team_id, tree.collect_room_specs())

    return saved


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
    return await gtAgentManager.get_team_all_agents(team_id, EmployStatus.OFF_BOARD)


async def get_agent_dept(team_id: int, agent_id: int) -> GtDept | None:
    """查询 Agent 所在部门；不在任何部门时返回 None。"""
    all_depts = await gtDeptManager.get_all_depts(team_id)
    for dept in all_depts:
        if agent_id in dept.agent_ids:
            return dept
    return None


async def delete_dept(team_id: int, dept_id: int, recursive: bool = False) -> None:
    """删除指定部门。

    - 不能删除根部门。
    - recursive=False 时若有子部门则报错；recursive=True 时递归删除所有子孙部门。
    - 删除后同步部门房间。
    """
    all_depts = await gtDeptManager.get_all_depts(team_id)
    dept_map: dict[int, GtDept] = {d.id: d for d in all_depts if d.id is not None}

    target = dept_map.get(dept_id)
    if target is None:
        raise TogoException(f"部门 ID '{dept_id}' 不存在", error_code="DEPT_NOT_FOUND")

    # 不能删除根部门
    if target.parent_id is None:
        raise TogoException("不能删除根部门", error_code="DEPT_DELETE_ROOT_FORBIDDEN")

    # 收集所有子孙部门 ID
    def _collect_subtree_ids(pid: int) -> list[int]:
        ids: list[int] = [pid]
        for d in all_depts:
            if d.id is not None and d.parent_id == pid:
                ids.extend(_collect_subtree_ids(d.id))
        return ids

    children = [d for d in all_depts if d.parent_id == dept_id]
    if children and not recursive:
        raise TogoException(
            f"部门 '{target.name}' 下还有子部门，请先删除子部门或使用 recursive=true 递归删除",
            error_code="DEPT_HAS_CHILDREN",
        )

    ids_to_delete = _collect_subtree_ids(dept_id)
    await gtDeptManager.delete_depts_by_ids(ids_to_delete)

    # 同步部门房间
    tree = await get_dept_tree(team_id)
    if tree is not None:
        await roomService.overwrite_dept_rooms(team_id, tree.collect_room_specs())
    else:
        # 树已为空，清除所有 DEPT 房间
        await roomService.overwrite_dept_rooms(team_id, [])

    logger.info(f"部门已删除（team_id={team_id}，dept_id={dept_id}，recursive={recursive}，共删除 {len(ids_to_delete)} 个）")


async def set_dept_manager(team_id: int, dept_name: str, manager_id: int) -> None:
    """变更部门主管，新主管必须已在该部门中。"""
    dept = await gtDeptManager.get_dept_by_name(team_id, dept_name)
    if dept is None:
        raise TogoException(
            f"部门 '{dept_name}' 不存在",
            error_code="DEPT_NOT_FOUND",
        )

    managers = await gtAgentManager.get_team_agents_by_ids(team_id, [manager_id])
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


async def get_sub_agent_ids(team_id: int, agent_id: int) -> set[int]:
    """返回 agent_id 在其所在部门的所有直接/间接下属 Agent ID 集合（不含自身）。

    仅当 agent_id 是该部门 manager 时才有下属；否则返回空集合。
    """
    tree: GtDept | None = await get_dept_tree(team_id)
    if tree is None:
        return set()

    def _find_agent_dept(node: GtDept) -> GtDept | None:
        if agent_id in (node.agent_ids or []):
            return node
        for child in node.children or []:
            found = _find_agent_dept(child)
            if found is not None:
                return found
        return None

    dept = _find_agent_dept(tree)
    if dept is None or dept.manager_id != agent_id:
        return set()

    all_ids, _ = dept.collect_dept_and_agent_ids()
    all_ids.discard(agent_id)
    return all_ids

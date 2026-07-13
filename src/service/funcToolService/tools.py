from __future__ import annotations
from typing import Any, Optional
import asyncio
import datetime
import logging
from zoneinfo import ZoneInfo

from constants import AgentStatus, AgentTaskType, DriverType, EmployStatus, RoleTemplateType, RoomState, SpecialAgent, TaskStatus
from dal.db import gtAgentManager, gtRoomManager, gtRoleTemplateManager, gtTeamManager, gtAgentTaskManager
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtDept import GtDept
from model.dbModel.gtRoleTemplate import GtRoleTemplate
from service.roomService import ToolCallContext
import service.roomService as roomService
import service.skillService as skillService
import service.thirdPartyService as thirdPartyService
from service.agentService.toolRegistry import validate_tool_allow_specs
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


async def web_search(query: str, _context: ToolCallContext = None) -> dict:
    """通过已配置的三方服务执行网页搜索。

    Args:
        query: 要搜索的关键词或问题。
    """
    return await thirdPartyService.search("deepseek", query)


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
                "tags": list(room_config.tags or []),
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
        "current_turn": _resolve_agent_name(runtime_room.get_current_turn_agent_id(), id_to_name) if runtime_room is not None and runtime_room.state == RoomState.SCHEDULING else None,
        "total_messages": len(runtime_room.messages) if runtime_room is not None else 0,
        "tags": list(room_config.tags or []),
    }
    return {"success": True, "room": room_dict}


async def start_chat(
    agent_name: str,
    _context: ToolCallContext = None,
) -> dict:
    """与指定 Agent 发起单聊（私聊）。若两人之间已有房间则直接返回，不重复创建。

    Args:
        agent_name: 要发起对话的目标 Agent 名称（内部标识符，即 name 字段，非 display_name）。
    """
    ok, team_id = _require_team_context(_context)
    if not ok:
        return {"success": False, "message": "当前没有可用的团队上下文。"}

    if _context is None or not _context.agent_id:
        return {"success": False, "message": "无法获取当前 Agent 身份。"}

    normalized = agent_name.strip()
    if not normalized:
        return {"success": False, "message": "目标 Agent 名称不能为空。"}

    all_agents = await gtAgentManager.get_team_all_agents(team_id)
    name_to_agent: dict[str, Any] = {a.name: a for a in all_agents}
    id_to_name: dict[int, str] = {a.id: a.name for a in all_agents}

    target = name_to_agent.get(normalized)
    if target is None:
        return {"success": False, "message": f"未找到成员: {normalized}"}

    self_id = _context.agent_id
    if target.id == self_id:
        return {"success": False, "message": "不能与自己发起单聊。"}

    member_ids = [self_id, target.id]
    member_set = set(member_ids)

    # 若已存在成员相同的房间，直接返回
    existing_rooms = await gtRoomManager.get_rooms_by_team(team_id)
    for existing_room in existing_rooms:
        if set(existing_room.agent_ids or []) == member_set:
            if roomService.get_room(existing_room.id) is None:
                await roomService.load_and_activate_room(existing_room.id)
            return {
                "success": True,
                "message": f"已存在与 {normalized} 的单聊房间 {existing_room.name}，无需重复创建。",
                "room": {
                    "room_id": existing_room.id,
                    "name": existing_room.name,
                    "members": [id_to_name.get(aid, str(aid)) for aid in (existing_room.agent_ids or [])],
                },
                "is_new_created": False,
            }

    # 按名称字母序生成房间名，保证唯一且可预测
    self_name = id_to_name.get(self_id, str(self_id))
    room_name = "_".join(sorted([self_name, normalized]))

    saved = await roomService.create_room(
        team_id=team_id,
        name=room_name,
        agent_ids=member_ids,
    )

    return {
        "success": True,
        "message": f"已创建与 {normalized} 的单聊房间 {saved.name}。",
        "room": {
            "room_id": saved.id,
            "name": saved.name,
            "members": [id_to_name.get(aid, str(aid)) for aid in (saved.agent_ids or [])],
        },
        "is_new_created": True,
    }


async def get_agent_info(agent_name: Optional[str] = None, _context: ToolCallContext = None) -> dict:
    """查询 Agent 信息。不传 agent_name 时返回团队成员列表，传入时返回指定成员详情。

    Args:
        agent_name: Agent 名称（内部标识符，即 name 字段，非 display_name），省略时返回所有 Agent
    """
    ok, team_id = _require_team_context(_context)
    if not ok:
        return {"success": False, "message": "当前没有可用的团队上下文。"}

    from service import agentService, deptService
    from dal.db import gtScheculeTaskManager

    team_agents = agentService.get_team_agents(team_id)

    async def _build_agent_dict(agent: Any, *, detail: bool) -> dict[str, Any]:
        agent_id = agent.gt_agent.id
        depts_with_paths: list[tuple[GtDept, str]] = await deptService.get_agent_depts(team_id, agent_id)
        first_task = await gtScheculeTaskManager.get_first_unfinish_task(agent_id) if agent.status == AgentStatus.FAILED else None
        info: dict[str, Any] = {
            "id": agent_id,
            "name": agent.gt_agent.name,
            "display_name": agent.gt_agent.display_name,
            "status": agent.status.name,
            "departments": [
                {
                    "name": path,
                    "position": "manager" if dept.manager_id == agent_id else "member"
                }
                for dept, path in depts_with_paths
            ] if depts_with_paths else None,
        }
        if first_task is not None:
            info["error_summary"] = _truncate_error_message(first_task.error_message)
        if detail:
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

    target_agent = next((agent for agent in team_agents if agent.gt_agent.name == agent_name or agent.gt_agent.display_name == agent_name), None)
    if target_agent is None:
        return {"success": False, "message": f"未找到成员: {agent_name}"}

    return {"success": True, "agent": await _build_agent_dict(target_agent, detail=True)}


async def wake_up_agent(agent_name: str, _context: ToolCallContext = None) -> dict:
    """唤醒处于 FAILED 状态的 Agent，使其重新进入调度循环。

    Args:
        agent_name: 要唤醒的 Agent 名称（内部标识符，即 name 字段，非 display_name）
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

    target_agent.start_consumer_task()
    return {"success": True, "message": f"已成功唤醒 {agent_name}，该成员将重新进入调度循环。"}


async def reload_team(_context: ToolCallContext = None) -> dict:
    """重载当前团队的运行时。

    注意：该操作会重启当前团队的运行时，可能中断团队内正在执行的任务。
    """
    ok, team_id = _require_team_context(_context)
    if not ok:
        return {"success": False, "message": "当前没有可用的团队上下文。"}

    from service import teamService

    team = await gtTeamManager.get_team_by_id(team_id)
    if team is None:
        return {"success": False, "message": f"未找到团队: team_id={team_id}"}

    # 在独立 task 里执行 hot_reload，使其不受当前 consumer task 取消的影响。
    # hot_reload 内部会调用 stop_team_runtime（取消当前 consumer），
    # 若直接 await，stop_team_runtime 的取消信号会打断自身，导致 restore_team 永远无法执行。
    asyncio.create_task(teamService.hot_reload_team(team.name))

    # 等待被 stop_team_runtime 取消，代码正常情况下不会走到 return。
    # 真正的成功结果由重启后的自中断恢复逻辑（self_interrupt）写入。
    await asyncio.get_event_loop().create_future()

    return {"success": False, "message": f"团队 {team.name} 重载已触发，等待 agent 重启后确认。"}

async def list_role_templates(keywords: list[str] | None = None, _context: ToolCallContext = None) -> dict:
    """查询全部角色模板列表。

    返回精简字段，不包含 soul；display_name 为当前语言下的名称。

    Args:
        keywords: 可选，关键词搜索列表。若提供，则仅返回名称或 soul 中包含这些词的模板。
    """
    if keywords:
        templates = await gtRoleTemplateManager.search_role_templates(keywords)
    else:
        templates = await gtRoleTemplateManager.get_all_role_templates()

    # 转换为 JSON 字典并剔除 soul 以节省 Token
    role_templates = []

    for t in templates:
        data = t.to_json()
        data.pop("soul", None)
        role_templates.append(data)

    return {
        "success": True,
        "role_templates": role_templates,
    }


async def get_role_template(role_name: str, _context: ToolCallContext = None) -> dict:
    """按名称查询单个角色模板详情。

    Args:
        role_name: 角色模板名称
    """
    template = await gtRoleTemplateManager.get_role_template_by_name(role_name.strip())
    if template is None:
        return {"success": False, "message": f"未找到角色模板: {role_name}"}
    return {"success": True, "role_template": template.to_json()}


async def save_role_template(
    name: str,
    type: str,
    soul: str,
    i18n: dict | None = None,
    overwrite_existing: bool = False,
    _context: ToolCallContext = None,
) -> dict:
    """创建或更新角色模板。若指定的 name 不存在则新建（必须设为 USER 类型），若已存在则更新该模板（注意：SYSTEM 类型的内置模板不可通过此工具修改）。

    Args:
        name: 角色模板名称。作为系统唯一标识符，建议使用英文小写字母和下划线。对应的多语言显示名称请通过 i18n 参数设置。
        type: 角色模板类型。SYSTEM 代表系统内置模版（随系统发布，只读）；USER 代表用户自定义模版（可增删改）。通过此工具操作时，请统一指定为 USER。
        soul: 角色模板的核心提示词。应包含角色的身份定位、职责边界和行为准则，是 Agent 运行的"灵魂"。该内容会作为核心指令注入到对应角色的 System Prompt 中。
        i18n: 可选多语言数据。示例：{"display_name": {"zh-CN": "高级写手", "en": "Senior Writer"}}
        overwrite_existing: 是否允许覆盖同名模板。默认 false；为 true 时，若同名模板已存在则执行更新。
    """
    from service import roleTemplateService

    normalized_name = name.strip()
    if not normalized_name:
        return {"success": False, "message": "角色模板名称不能为空。"}

    role_type = RoleTemplateType.value_of(type)
    if role_type is None:
        return {"success": False, "message": "角色模板 type 只允许 SYSTEM 或 USER。"}

    existing = await gtRoleTemplateManager.get_role_template_by_name(normalized_name)
    if existing is None and role_type == RoleTemplateType.SYSTEM:
        return {"success": False, "message": "SYSTEM 角色模板不允许通过工具创建。"}
    if existing is not None and existing.type == RoleTemplateType.SYSTEM:
        return {"success": False, "message": f"SYSTEM 角色模板 {normalized_name} 不允许通过工具修改。"}
    if existing is not None and overwrite_existing is False:
        return {
            "success": False,
            "message": f"角色模板 {normalized_name} 已存在；如需覆盖请将 overwrite_existing 设为 true。",
        }

    saved = await roleTemplateService.save_role_template(
        GtRoleTemplate(
            name=normalized_name,
            soul=soul,
            type=role_type,
            i18n=i18n or {},
        )
    )
    action = "更新" if existing is not None else "创建"
    return {
        "success": True,
        "message": f"已{action}角色模板 {normalized_name}。",
        "role_template": saved.to_json(),
    }


async def save_agent(
    name: str,
    role_template_name: str,
    model: str | None = None,
    driver: str = DriverType.TSP.value,
    allow_tools: list[str] | None = None,
    allow_skills: list[str] | None = None,
    i18n: dict | None = None,
    overwrite_existing: bool = False,
    agent_id: int | None = None,
    _context: ToolCallContext = None,
) -> dict:
    """在当前团队中创建或更新成员。

    Args:
        name: 成员名称。作为当前团队内的稳定标识符，建议使用英文小写字母和下划线。
        role_template_name: 要绑定的角色模板名称。工具会按名称解析为 role_template_id。
        model: 可选模型覆盖。留空（None）表示不覆盖模板/系统默认模型。
        driver: 驱动类型。可选值为 native、claude_sdk、tsp。无特别需要（如操作者明确指定）时建议省略，默认使用 tsp。
        allow_tools: 可见工具列表。支持具体工具名（如 "read_file"）或类别语法（如 "Category:Read"）。系统会自动合并类别和具体工具名。基础协作工具（Basic 类别）默认总是开启，无需显式包含。通常情况下此列表留空即可，系统会自动授予 Admin 以外的所有常规类别权限。
                     可用类别：Read, Write, Execute, Admin。注意：Admin 类别属于团队管理功能，严禁分配给除团队根主管以外的普通成员。
        allow_skills: 可见技能列表。指定该成员可以加载的技能名称列表，如 ["frontend-design"]。调用 load_skill 工具时仅允许加载已授权的技能。留空表示不授权任何技能。
        i18n: 可选多语言数据。示例：{"display_name": {"zh-CN": "Alice", "en": "Alice"}}
        overwrite_existing: 是否允许覆盖当前团队中已存在的同名成员。默认 false；为 true 时，若同名成员已存在则执行更新。当传入 agent_id 时，此参数不生效。
        agent_id: 可选成员 ID。传入后按 ID 精确定位成员（忽略 overwrite_existing），此时 name 可用于重命名。
    """
    ok, team_id = _require_team_context(_context)
    if not ok:
        return {"success": False, "message": "当前没有可用的团队上下文。"}

    normalized_name = name.strip()
    if not normalized_name:
        return {"success": False, "message": "成员名称不能为空。"}

    special_agent = SpecialAgent.value_of(normalized_name)
    if special_agent is not None:
        return {"success": False, "message": f"保留成员 {special_agent.name} 不允许通过工具创建或修改。"}

    normalized_role_template_name = role_template_name.strip()
    if not normalized_role_template_name:
        return {"success": False, "message": "角色模板名称不能为空。"}

    driver_type = DriverType.value_of(driver)
    if driver_type is None:
        return {"success": False, "message": "成员 driver 只允许 native、claude_sdk 或 tsp。"}

    error_msg = validate_tool_allow_specs(allow_tools or [])
    if error_msg is not None:
        return {"success": False, "message": error_msg}

    role_template = await gtRoleTemplateManager.get_role_template_by_name(normalized_role_template_name)
    if role_template is None:
        return {"success": False, "message": f"未找到角色模板: {normalized_role_template_name}"}

    if agent_id is not None:
        existing = await gtAgentManager.get_agent_by_id(agent_id)
        if existing is None or existing.team_id != team_id:
            return {"success": False, "message": f"未找到 ID 为 {agent_id} 的成员。"}
    else:
        existing = await gtAgentManager.get_agent(team_id, normalized_name, status=None)
        if existing is not None and overwrite_existing is False:
            return {
                "success": False,
                "message": f"成员 {normalized_name} 已存在；如需覆盖请将 overwrite_existing 设为 true。",
            }

    if existing is not None and existing.team_id == -1:
        return {"success": False, "message": f"保留成员 {existing.name} 不允许通过工具创建或修改。"}

    agent = existing or GtAgent(
        team_id=team_id,
        name=normalized_name,
        employ_status=EmployStatus.OFF_BOARD,
    )
    agent.name = normalized_name
    agent.role_template_id = role_template.id
    agent.model = model or ""
    agent.driver = driver_type
    agent.allow_tools = allow_tools
    agent.allow_skills = allow_skills
    agent.i18n = i18n or {}

    await gtAgentManager.batch_save_agents(team_id, [agent])
    saved = await gtAgentManager.get_agent_by_id(agent.id) if agent.id else await gtAgentManager.get_agent(team_id, normalized_name, status=None)
    if saved is None:
        return {"success": False, "message": f"成员保存失败: {normalized_name}"}

    action = "更新" if existing is not None else "创建"
    payload = saved.to_json()
    payload["driver"] = saved.driver.value
    payload["employ_status"] = saved.employ_status.name
    payload["role_template_name"] = role_template.name
    return {
        "success": True,
        "message": f"已{action}成员 {normalized_name}。配置已保存，需要 reload_team 后生效。",
        "agent": payload,
    }


def _collect_descendant_ids(node: GtDept) -> set[int]:
    """递归收集节点的所有后代 dept id（不含自身）。"""
    ids: set[int] = set()
    for child in node.children:
        if child.id is not None:
            ids.add(child.id)
        ids |= _collect_descendant_ids(child)
    return ids


async def save_dept(
    name: str,
    responsibility: str,
    manager_name: str,
    member_names: list[str],
    parent_name: str | None = None,
    i18n: dict | None = None,
    overwrite_existing: bool = False,
    dept_id: int | None = None,
    _context: ToolCallContext = None,
) -> dict:
    """在当前团队中创建或更新组织（部门）。全量覆盖模式：每次调用会完整替换成员列表。

    Args:
        name: 组织名称。作为当前团队内的稳定标识符，建议使用英文小写字母和下划线。
        responsibility: 组织职责描述。
        manager_name: 负责人成员名。负责人若不在 member_names 中，将自动加入。
        member_names: 成员名称列表。全量覆盖，每次调用将完整替换现有成员列表。
        parent_name: 父组织名称。新建组织时必须指定；不能设置为自身或自身的子组织。更新已有根组织时可省略或传 null 以保持其根节点状态。
        i18n: 可选多语言数据。示例：{"dept_name": {"zh-CN": "研发部", "en": "R&D"}, "responsibility": {"zh-CN": "..."}}
        overwrite_existing: 是否允许覆盖已存在的同名组织。默认 false；为 true 时执行更新。当传入 dept_id 时，此参数不生效。
        dept_id: 可选组织 ID。传入后按 ID 精确定位组织（忽略 overwrite_existing），此时 name 可用于重命名。
    """
    ok, team_id = _require_team_context(_context)
    if not ok:
        return {"success": False, "message": "当前没有可用的团队上下文。"}

    normalized_name = name.strip()
    if not normalized_name:
        return {"success": False, "message": "组织名称不能为空。"}

    # 解析 manager
    normalized_manager = manager_name.strip()
    if not normalized_manager:
        return {"success": False, "message": "负责人名称不能为空。"}

    from dal.db import gtDeptManager
    from service import deptService

    all_agents = await gtAgentManager.get_team_all_agents(team_id)
    name_to_agent: dict[str, Any] = {a.name: a for a in all_agents}

    manager_agent = name_to_agent.get(normalized_manager)
    if manager_agent is None:
        return {"success": False, "message": f"未找到成员: {normalized_manager}"}

    # 解析 member_names → agent_ids，遇到找不到的立即报错
    resolved_ids: list[int] = []
    for mname in member_names:
        mname = mname.strip()
        agent = name_to_agent.get(mname)
        if agent is None:
            return {"success": False, "message": f"未找到成员: {mname}"}
        resolved_ids.append(agent.id)

    # 负责人自动加入成员列表
    if manager_agent.id not in resolved_ids:
        resolved_ids.insert(0, manager_agent.id)

    # 按 ID 或名称定位已有组织
    if dept_id is not None:
        existing = await gtDeptManager.get_dept_by_id(dept_id)
        if existing is None or existing.team_id != team_id:
            return {"success": False, "message": f"未找到 ID 为 {dept_id} 的组织。"}
    else:
        existing = await gtDeptManager.get_dept_by_name(team_id, normalized_name)
        if existing is not None and not overwrite_existing:
            return {
                "success": False,
                "message": f"组织 {normalized_name} 已存在；如需覆盖请将 overwrite_existing 设为 true。",
            }

    # 解析 parent_name → parent_id，并校验循环引用
    parent_id: int | None = None
    if parent_name is not None:
        normalized_parent = parent_name.strip()
        parent_dept = await gtDeptManager.get_dept_by_name(team_id, normalized_parent)
        if parent_dept is None:
            return {"success": False, "message": f"未找到父组织: {normalized_parent}"}
        if existing is not None:
            # 不能把自身设为父组织
            if parent_dept.id == existing.id:
                return {"success": False, "message": "父组织不能设置为当前组织自身。"}
            # 不能把子孙组织设为父组织（会产生环）
            dept_tree = await deptService.get_dept_tree(team_id)
            current_node = _find_dept_node(dept_tree, existing.id)
            if current_node is not None:
                descendant_ids = _collect_descendant_ids(current_node)
                if parent_dept.id in descendant_ids:
                    return {"success": False, "message": f"父组织 {normalized_parent} 是当前组织的子组织，不能形成循环引用。"}
        parent_id = parent_dept.id
    else:
        # parent_name=None：仅允许更新已是根节点的现有组织；新建时必须指定父组织
        if existing is None:
            return {"success": False, "message": "新建组织时必须指定父组织（parent_name）。如需创建根组织，请联系管理员通过组织树编辑器操作。"}
        if existing.parent_id is not None:
            return {"success": False, "message": f"组织 {normalized_name} 当前不是根组织，不能将父组织设为空。"}

    saved = await deptService.upsert_dept(
        team_id=team_id,
        name=normalized_name,
        responsibility=responsibility,
        manager_id=manager_agent.id,
        agent_ids=resolved_ids,
        parent_id=parent_id,
        dept_id=existing.id if existing is not None else None,
        i18n=i18n,
    )

    id_to_name = {a.id: a.name for a in all_agents}
    action = "更新" if existing is not None else "创建"
    return {
        "success": True,
        "message": f"已{action}组织 {normalized_name}。配置已保存，需要 reload_team 后生效。",
        "dept": _serialize_dept_node(saved, id_to_name),
    }


async def save_room(
    name: str,
    member_names: list[str],
    initial_topic: str = "",
    max_rounds: int | None = None,
    overwrite_existing: bool = False,
    room_id: int | None = None,
    _context: ToolCallContext = None,
) -> dict:
    """在当前团队中创建或更新房间。房间类型按成员数量自动判断：2人为单聊，3人及以上为群聊。

    Args:
        name: 房间名称。团队内唯一标识，建议使用英文小写字母和下划线。
        member_names: 成员名称列表，至少 2 人。全量覆盖，每次调用将完整替换现有成员列表。
        initial_topic: 房间初始话题，可选。
        max_rounds: 最大轮次。不传则使用系统默认值；<=0 表示不限轮次。
        overwrite_existing: 同名房间已存在时是否允许覆盖。默认 false；为 true 时执行更新。当传入 room_id 时此参数不生效。
        room_id: 可选房间 ID。传入后按 ID 精确定位房间（忽略 overwrite_existing），此时 name 可用于重命名。
    """
    ok, team_id = _require_team_context(_context)
    if not ok:
        return {"success": False, "message": "当前没有可用的团队上下文。"}

    normalized_name = name.strip()
    if not normalized_name:
        return {"success": False, "message": "房间名称不能为空。"}

    if len(member_names) < 2:
        return {"success": False, "message": f"房间成员不足 2 人（当前 {len(member_names)} 人）。"}

    all_agents = await gtAgentManager.get_team_all_agents(team_id)
    name_to_agent: dict[str, Any] = {a.name: a for a in all_agents}

    resolved_ids: list[int] = []
    for mname in member_names:
        mname = mname.strip()
        agent = name_to_agent.get(mname)
        if agent is None:
            return {"success": False, "message": f"未找到成员: {mname}"}
        resolved_ids.append(agent.id)

    # 按 ID 或名称定位已有房间
    if room_id is not None:
        existing = await gtRoomManager.get_room_by_id(room_id)
        if existing is None or existing.team_id != team_id:
            return {"success": False, "message": f"未找到 ID 为 {room_id} 的房间。"}
    else:
        existing = await gtRoomManager.get_room_by_team_and_name(team_id, normalized_name)
        if existing is not None and not overwrite_existing:
            return {
                "success": False,
                "message": f"房间 {normalized_name} 已存在；如需覆盖请将 overwrite_existing 设为 true。",
            }

    try:
        saved = await roomService.upsert_room(
            team_id=team_id,
            name=normalized_name,
            agent_ids=resolved_ids,
            initial_topic=initial_topic,
            max_rounds=max_rounds,
            room_id=existing.id if existing is not None else None,
        )
    except Exception as exc:
        return {"success": False, "message": str(exc)}

    id_to_name = {a.id: a.name for a in all_agents}
    action = "更新" if existing is not None else "创建"
    room_type_label = "单聊" if len(resolved_ids) == 2 else "群聊"
    return {
        "success": True,
        "message": f"已{action}{room_type_label}房间 {saved.name}。配置已保存，需要 reload_team 后生效。",
        "room": {
            "room_id": saved.id,
            "name": saved.name,
            "type": room_type_label,
            "members": [id_to_name.get(aid, str(aid)) for aid in (saved.agent_ids or [])],
            "max_rounds": saved.max_rounds,
            "initial_topic": saved.initial_topic,
        },
    }


async def delete_room(
    name: str,
    room_id: int | None = None,
    _context: ToolCallContext = None,
) -> dict:
    """删除当前团队中的指定房间。DEPT 房间不允许删除；运行中的房间不允许删除。

    Args:
        name: 要删除的房间名称。
        room_id: 可选房间 ID。传入后按 ID 精确定位，此时 name 仅用于确认提示。
    """
    ok, team_id = _require_team_context(_context)
    if not ok:
        return {"success": False, "message": "当前没有可用的团队上下文。"}

    if room_id is not None:
        target = await gtRoomManager.get_room_by_id(room_id)
        if target is None or target.team_id != team_id:
            return {"success": False, "message": f"未找到 ID 为 {room_id} 的房间。"}
    else:
        normalized_name = name.strip()
        if not normalized_name:
            return {"success": False, "message": "房间名称不能为空。"}
        target = await gtRoomManager.get_room_by_team_and_name(team_id, normalized_name)
        if target is None:
            return {"success": False, "message": f"未找到房间: {normalized_name}"}

    try:
        await roomService.delete_managed_room(team_id, target.id)
    except Exception as exc:
        return {"success": False, "message": str(exc)}

    return {
        "success": True,
        "message": f"已删除房间 {target.name}。配置已保存，需要 reload_team 后生效。",
    }


async def delete_dept(
    name: str,
    dept_id: int | None = None,
    recursive: bool = False,
    _context: ToolCallContext = None,
) -> dict:
    """删除当前团队中的指定组织（部门）。

    Args:
        name: 要删除的组织名称。
        dept_id: 可选组织 ID。传入后按 ID 精确定位组织，此时 name 仅用于确认提示。
        recursive: 是否递归删除子组织。默认 false；为 true 时会一并删除所有子孙组织。
    """
    ok, team_id = _require_team_context(_context)
    if not ok:
        return {"success": False, "message": "当前没有可用的团队上下文。"}

    from dal.db import gtDeptManager
    from service import deptService

    if dept_id is not None:
        target = await gtDeptManager.get_dept_by_id(dept_id)
        if target is None or target.team_id != team_id:
            return {"success": False, "message": f"未找到 ID 为 {dept_id} 的组织。"}
    else:
        normalized_name = name.strip()
        if not normalized_name:
            return {"success": False, "message": "组织名称不能为空。"}
        target = await gtDeptManager.get_dept_by_name(team_id, normalized_name)
        if target is None:
            return {"success": False, "message": f"未找到组织: {normalized_name}"}

    try:
        await deptService.delete_dept(team_id, target.id, recursive=recursive)
    except Exception as exc:
        return {"success": False, "message": str(exc)}

    return {
        "success": True,
        "message": f"已删除组织 {target.name}{'（含子组织）' if recursive else ''}。配置已保存，需要 reload_team 后生效。",
    }


async def delete_role_template(role_name: str, _context: ToolCallContext = None) -> dict:
    """删除指定的角色模板。注意：仅能删除由用户创建（USER 类型）且当前未被任何成员使用的模板。

    Args:
        role_name: 要删除的角色模板名称
    """

    normalized_name = role_name.strip()
    template = await gtRoleTemplateManager.get_role_template_by_name(normalized_name)
    if template is None:
        return {"success": False, "message": f"未找到角色模板: {role_name}"}
    if template.type == RoleTemplateType.SYSTEM:
        return {"success": False, "message": f"SYSTEM 角色模板 {template.name} 不允许通过工具删除。"}

    referenced_agents = list(
        await GtAgent.select()
        .where(GtAgent.role_template_id == template.id)
        .order_by(GtAgent.team_id, GtAgent.name)
        .aio_execute()
    )
    if referenced_agents:
        agents = [{"name": agent.name, "team_id": agent.team_id} for agent in referenced_agents]
        agent_names = ", ".join(agent["name"] for agent in agents)
        return {
            "success": False,
            "message": f"角色模板 {template.name} 正在被以下 Agent 使用，无法删除: {agent_names}",
            "agents": agents,
        }

    await gtRoleTemplateManager.delete_role_template(template.id)
    return {
        "success": True,
        "message": f"已删除角色模板 {template.name}。",
        "role_template": {"id": template.id, "name": template.name},
    }


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

    if not target_room.can_post_message(_context.agent_id):
        logger.warning(
            "send_chat_msg: 发言者不在目标房间 agents 中 sender_id=%s room=%s team_id=%s agents=%s",
            _context.agent_id,
            room_name,
            _context.team_id,
            target_room.get_agent_ids(),
        )
        return {"success": False, "message": f"你不在房间 {target_room.name} 中，发送失败。"}

    await target_room.add_message(_context.agent_id, msg)

    if _context.chat_room is not None and target_room.room_id == _context.chat_room.room_id:
        return {"success": True, "message": "消息已送达房间。如果你还有其他工具需要调用，请继续；如果本轮操作已全部完成，请调用 finish_action 结束行动。"}

    return {"success": True, "message": f"消息已送达 {target_room.name}。如果你还有其他工具需要调用，请继续；如果本轮操作已全部完成，请调用 finish_action 结束行动。"}


async def finish_action(_context: ToolCallContext = None, confirm_no_need_talk: bool = False) -> dict:
    """结束行动。当你完成所有发言和工具调用后（或者无需行动时），必须调用此工具来把行动机会让给下一位成员。

    参数：
    - confirm_no_need_talk (bool)：确认本轮无需在收到消息通知的房间发言。
      - 本轮未在对应房间发言时：若确认不需要发言，须设置为 true 才能结束行动。
      - 本轮已在对应房间发言时：不得设置此参数，直接调用 finish_action 即可。
      ⚠️ 注意：直接输出（非 send_chat_msg）的文字用户看不到，不算发言。"""
    if _context is None:
        logger.warning("结束行动失败，上下文未设置")
        return {"success": False, "message": "当前没有激活的上下文。"}

    task_type = _context.schedule_task.task_type if _context.schedule_task is not None else AgentTaskType.ROOM_MESSAGE

    if task_type == AgentTaskType.TODO_TASK:
        agent_task_id = _context.schedule_task.task_data.get("agent_task_id")
        agent_task = await gtAgentTaskManager.get_task(agent_task_id) if agent_task_id else None
        if agent_task is not None:
            is_assignee = agent_task.assignee_id == _context.agent_id
            is_manager  = agent_task.manager_id == _context.agent_id

            if is_assignee and agent_task.status in (TaskStatus.TODO, TaskStatus.IN_PROGRESS):
                logger.warning(f"finish_action 被拒绝，协作任务仍为 {agent_task.status.value}: agent_id={_context.agent_id}, agent_task_id={agent_task_id}")
                return {
                    "success": False,
                    "message": f"""\
finish 失败，你负责执行的任务【{agent_task.title}】状态仍为 {agent_task.status.value}，尚未处理完毕。

请先完成任务处理：
- 若已完成，请调用 `update_task` 将状态改为 REVIEWING（有验收人时）或 DONE 并填写结果。
- 若需暂缓（本轮无法完成），请调用 `update_task` 将状态改为 ON_HOLD。
- 若需取消，请调用 `update_task` 将状态改为 CANCELLED。
完成后再调用 finish_action。""",
                }
            elif is_manager and agent_task.status == TaskStatus.REVIEWING:
                logger.warning(f"finish_action 被拒绝，协作任务待验收: agent_id={_context.agent_id}, agent_task_id={agent_task_id}")
                return {
                    "success": False,
                    "message": f"""\
finish 失败，你负责验收的任务【{agent_task.title}】依然为 reviewing状态，未完成审核。

请先完成验收：
- 验收通过：请调用 `update_task` 将状态改为 DONE。
- 打回重做：请调用 `update_task` 将状态改为 IN_PROGRESS。
完成后再调用 finish_action。""",
                }
        logger.info(f"Agent 结束协作任务行动: agent_id={_context.agent_id}")
        return {"success": True, "message": "已结束了本轮行动."}

    elif task_type == AgentTaskType.ROOM_MESSAGE:
        if _context.chat_room is None:
            logger.warning("结束行动失败，聊天室上下文未设置")
            return {"success": False, "message": "当前没有激活的房间上下文。"}

        if confirm_no_need_talk and _context.chat_room.current_turn_has_content:
            return {
                "success": False,
                "message": "finish_action 失败：你本轮已经通过 send_chat_msg 发过消息了，不需要设置 confirm_no_need_talk=true。请直接调用 finish_action（不带任何参数）结束行动。",
            }

        if not confirm_no_need_talk and not _context.chat_room.current_turn_has_content:
            room_name = _context.chat_room.name
            return {
                "success": False,
                "message": f"""\
finish 失败，你本次行动中，未在收到消息的房间【{room_name}】发言。

1. 如果你忘记发言（或者是不小心用直接输出替代了向房间发言），那么请调用 send_chat_msg 发送消息。
2. 如果你确认不需要发言，请设置 confirm_no_need_talk=true 重新调用 finish_action。""",
            }

        logger.info(f"Agent 结束行动: agent_id={_context.agent_id}")
        ok = await _context.chat_room.handle_finish_request(_context.agent_id)

        if not ok:
            current_id = _context.chat_room.get_current_turn_agent_id()
            logger.warning(f"finish_turn 被房间拒绝（发言位不匹配），但仍视为行动结束: agent_id={_context.agent_id}, current_turn_id={current_id}, room={_context.chat_room.key}")

        return {"success": True, "message": "已结束了本轮行动."}



# ---------------------------------------------------------------------------
# Task 工具：协作任务管理（委托给 taskService）
# ---------------------------------------------------------------------------

async def create_task(
    title: str,
    assignee_id: int,
    description: str = '',
    manager_id: Optional[int] = None,
    priority: str = 'NORMAL',
    parent_id: Optional[int] = None,
    depends_on: Optional[list[int]] = None,
    room_id: Optional[int] = None,
    _context: Optional[ToolCallContext] = None,
) -> dict:
    """创建协作任务。

    Args:
        title: 任务标题
        assignee_id: 执行人 Agent ID
        description: 任务描述，含上下文、约束和交付标准
        manager_id: 完成时若需要他人验收，则设置验收人 Agent ID，不填则无需验收流程；不能与 assignee_id 相同（同一人无需设置验收）
        priority: 优先级，HIGH / NORMAL / LOW，默认 NORMAL
        parent_id: 父任务 ID，用于子任务拆解
        depends_on: 依赖的任务 ID 列表，全部完成后才允许开始
        room_id: 关联房间 ID，便于溯源
    """
    import service.taskService as taskService

    ok, team_id = _require_team_context(_context)
    if not ok:
        return {"success": False, "message": "无法获取团队上下文"}

    return await taskService.create_task(
        team_id=team_id,
        creator_id=_context.agent_id,  # type: ignore[union-attr]
        title=title,
        assignee_id=assignee_id,
        description=description,
        manager_id=manager_id,
        priority=priority,
        parent_id=parent_id,
        depends_on=[int(x) for x in (depends_on or [])],
        room_id=room_id,
    )


async def update_task(
    task_id: int,
    status: str,
    result: str = '',
    block_reason: str = '',
    _context: Optional[ToolCallContext] = None,
) -> dict:
    """更新协作任务状态或附加信息。

    Args:
        task_id: 任务 ID
        status: 新状态（TODO / PENDING / IN_PROGRESS / ON_HOLD / REVIEWING / DONE / CANCELLED）
        result: 完成/提交摘要，在 status=REVIEWING 或 DONE 时填写
        block_reason: 搁置原因，在 status=ON_HOLD 时填写
    """
    import service.taskService as taskService

    ok, team_id = _require_team_context(_context)
    if not ok:
        return {"success": False, "message": "无法获取团队上下文"}

    return await taskService.update_task(
        team_id=team_id,
        caller_id=_context.agent_id,  # type: ignore[union-attr]
        task_id=task_id,
        status=status,
        result=result,
        block_reason=block_reason,
    )


async def get_task(task_id: int, _context: Optional[ToolCallContext] = None) -> dict:
    """查询单个协作任务详情，包含依赖任务状态摘要。

    Args:
        task_id: 任务 ID
    """
    import service.taskService as taskService

    ok, team_id = _require_team_context(_context)
    if not ok:
        return {"success": False, "message": "无法获取团队上下文"}

    return await taskService.get_task(team_id=team_id, task_id=task_id)


async def list_tasks(
    assignee_id: Optional[int] = None,
    manager_id: Optional[int] = None,
    status: Optional[str] = None,
    limit: int = 20,
    _context: Optional[ToolCallContext] = None,
) -> dict:
    """查询协作任务列表。

    Args:
        assignee_id: 按执行人 Agent ID 过滤
        manager_id: 按验收人 Agent ID 过滤
        status: 按状态过滤（TODO / PENDING / IN_PROGRESS / ON_HOLD / REVIEWING / DONE / CANCELLED）
        limit: 最多返回条数，默认 20
    """
    import service.taskService as taskService

    ok, team_id = _require_team_context(_context)
    if not ok:
        return {"success": False, "message": "无法获取团队上下文"}

    return await taskService.list_tasks(
        team_id=team_id,
        assignee_id=assignee_id,
        manager_id=manager_id,
        status=status,
        limit=limit,
    )


async def load_skill(
    skill_name: str,
    _context: Optional[ToolCallContext] = None,
) -> dict:
    """加载指定技能的完整信息，包含详细操作指引和文件清单。

    Args:
        skill_name: 要加载的技能名称。仅能加载当前 Agent 已授权的技能，未授权的技能会被拒绝。
    """
    if not _context or not _context.agent_id:
        return {"success": False, "message": "无法获取当前 Agent 上下文"}

    agent = await gtAgentManager.get_agent_by_id(_context.agent_id)
    if agent is None:
        return {"success": False, "message": f"未找到 Agent（ID: {_context.agent_id}）"}

    allow_skills = agent.allow_skills

    # None 表示自动（允许所有技能），空列表表示不允许任何技能
    if allow_skills is not None and skill_name not in allow_skills:
        return {"success": False, "message": f"技能 {skill_name} 未对当前 Agent 开放"}

    skill_info = skillService.get_skill(skill_name)
    if skill_info is None:
        return {"success": False, "message": f"技能 {skill_name} 不存在"}

    content = skill_info.content
    if not content:
        return {"success": False, "message": f"读取技能 {skill_name} 内容失败"}

    files = skill_info.files

    return {
        "success": True,
        "skill_name": skill_info.name,
        "skill_dir": skill_info.skill_dir,
        "is_builtin": skill_info.is_builtin,
        "description": skill_info.description,
        "content": content,
        "files": files,
    }

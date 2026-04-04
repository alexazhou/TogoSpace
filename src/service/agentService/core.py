import asyncio
import logging
import os
from typing import Any, List

from util import configUtil
from model.dbModel.gtAgent import GtAgent
from service.agentService.agent import Agent
from service.agentService.driver import normalize_driver_config
from service.agentService.promptBuilder import build_agent_system_prompt
from service import llmService, roomService, persistenceService
from dal.db import gtTeamManager, gtAgentManager, gtRoleTemplateManager
from peewee import IntegrityError
from exception import TeamAgentException
from constants import AgentStatus, DriverType, EmployStatus

logger = logging.getLogger(__name__)

_agents: dict[int, "Agent"] = {}


def _resolve_max_function_calls(team_config: dict | None) -> int:
    value = (team_config or {}).get("max_function_calls", 5)
    if isinstance(value, int):
        return max(1, value)
    return 5

async def startup() -> None:
    global _agents
    _agents = {}


async def restore_state() -> None:
    """从数据库恢复所有 Agent 的历史消息，并重置 RUNNING 任务为 PENDING。"""
    for agent in _agents.values():
        # 加载历史消息
        items = await persistenceService.load_agent_history_message(agent.gt_agent.id)
        if items:
            agent._history.replace(items)

        # 重置 RUNNING 任务为 PENDING（上次未完成）
        await persistenceService.reset_running_tasks(agent.gt_agent.id)


async def _load_team(team_id: int, workspace_root: str | None = None) -> None:
    gt_team = await gtTeamManager.get_team_by_id(team_id)
    if gt_team is None:
        logger.warning(f"加载 Team Agent 失败: team_id={team_id} 不存在于配置中")
        return

    gt_agents = await gtAgentManager.get_team_agents(team_id)
    gt_role_templates = await gtRoleTemplateManager.get_role_templates_by_ids(
        [agent.role_template_id for agent in gt_agents]
    )
    templates_by_id = {template.id: template for template in gt_role_templates}

    app_config = configUtil.get_app_config()
    base_prompt_tmpl = app_config.group_chat_prompt
    identity_prompt_tmpl = app_config.agent_identity_prompt
    default_model = llmService.get_default_model()
    resolved_workspace_root = workspace_root or app_config.setting.workspace_root
    assert resolved_workspace_root is not None, "workspace_root 未配置"
    max_function_calls = _resolve_max_function_calls(gt_team.config)

    team_name = gt_team.name
    team_workdir = os.path.join(resolved_workspace_root, team_name)
    team_id = gt_team.id

    for gt_agent in gt_agents:
        assert gt_agent.role_template_id in templates_by_id, (
            f"角色模版不存在: agent={gt_agent.name}, role_template_id={gt_agent.role_template_id}"
        )
        gt_role_template = templates_by_id[gt_agent.role_template_id]

        agent_name = gt_agent.name
        template_name = gt_role_template.name
        model_name = gt_agent.model or gt_role_template.model or default_model
        gt_agent.model = model_name
        driver_config = normalize_driver_config(
            {
                "driver": gt_agent.driver or gt_role_template.driver,
                "allowed_tools": gt_role_template.allowed_tools,
            }
        )
        full_prompt = await build_agent_system_prompt(
            team_id=team_id,
            agent_name=agent_name,
            template_name=template_name,
            template_soul=gt_role_template.soul,
            base_prompt_tmpl=base_prompt_tmpl,
            identity_prompt_tmpl=identity_prompt_tmpl,
        )

        assert gt_agent.id is not None and gt_agent.id > 0, f"invalid agent id: {gt_agent.id}"
        agent = Agent(
            gt_agent=gt_agent,
            system_prompt=full_prompt,
            driver_config=driver_config,
            agent_workdir=team_workdir,
            max_function_calls=max_function_calls,
        )
        _agents[gt_agent.id] = agent
        logger.info(
            f"创建 Agent 实例: agent_id={gt_agent.id}, template={template_name}, model={model_name}, driver={driver_config.driver_type}"
        )
        await agent.startup()


async def _unload_team(team_id: int) -> None:
    keys_to_remove = [agent_id for agent_id, agent in _agents.items() if agent.gt_agent.team_id == team_id]
    close_tasks: list[Any] = []
    for agent_id in keys_to_remove:
        close_tasks.append(_agents[agent_id].close())
    if close_tasks:
        await asyncio.gather(*close_tasks, return_exceptions=True)
    for agent_id in keys_to_remove:
        _agents.pop(agent_id, None)


async def load_all_team(workspace_root: str | None = None) -> None:
    for gt_team in await gtTeamManager.get_all_teams():
        await _load_team(gt_team.id, workspace_root=workspace_root)


async def reload_team(team_id: int, workspace_root: str | None = None) -> None:
    """按 Team 维度重建运行时 Agent 实例。"""
    await _unload_team(team_id)
    await _load_team(team_id, workspace_root=workspace_root)


def get_agent(agent_id: int) -> "Agent":
    agent = _agents.get(agent_id)
    if agent is None:
        raise KeyError(f"agent not found: agent_id={agent_id}")
    return agent


def get_all_agents() -> list["Agent"]:
    return list(_agents.values())


def get_team_agents(team_id: int) -> list["Agent"]:
    return [agent for agent in _agents.values() if agent.gt_agent.team_id == team_id]


def get_team_runtime_status_map(team_id: int) -> dict[int, AgentStatus]:
    return {
        agent.gt_agent.id: agent.status
        for agent in _agents.values()
        if agent.gt_agent.id > 0 and agent.gt_agent.team_id == team_id
    }

def get_room_agents(room_id: int) -> List["Agent"]:
    room = roomService.get_room(room_id)
    if room is None:
        return []
    agent_names: List[str] = roomService.get_agent_names(room_id)
    return [_agents[agent_id] for name in agent_names if (agent_id := room.get_agent_id(name)) in _agents]


async def overwrite_team_agents(team_id: int, agents_data: list[GtAgent]) -> list[GtAgent]:
    """全量覆盖成员列表：有 id 更新，无 id 创建，不在列表的设为离职状态。返回在职成员列表。"""
    existing_agents = await gtAgentManager.get_team_agents(team_id)
    existing_ids = {a.id for a in existing_agents}
    existing_by_id = {a.id: a for a in existing_agents}
    request_ids = {agent.id for agent in agents_data if agent.id is not None}

    # 1. 离职处理
    ids_to_offboard = existing_ids - request_ids
    if len(ids_to_offboard) > 0:
        await gtAgentManager.batch_update_agent_status(list(ids_to_offboard), EmployStatus.OFF_BOARD)

    # 2. 转换为 GtAgent 对象列表
    agents_to_save: list[GtAgent] = []
    for data in agents_data:
        agent_id = data.id

        if agent_id is not None:
            existing = existing_by_id.get(agent_id)
            if existing is None:
                raise TeamAgentException(
                    error_message=f"成员 ID 不存在于当前 team: {agent_id}",
                    error_code="agent_not_found",
                )
            agent = existing
            agent.name = data.name
            agent.role_template_id = data.role_template_id
            agent.model = data.model or ""
            agent.driver = data.driver or DriverType.NATIVE
            agent.employ_status = EmployStatus.ON_BOARD
        else:
            agent = GtAgent(
                team_id=team_id,
                name=data.name,
                role_template_id=data.role_template_id,
                model=data.model or "",
                driver=data.driver or DriverType.NATIVE,
                employ_status=EmployStatus.ON_BOARD,
            )

        agents_to_save.append(agent)

    # 3. 批量保存
    try:
        await gtAgentManager.batch_save_agents(team_id, agents_to_save)
    except IntegrityError as e:
        raise TeamAgentException(
            error_message="成员保存失败，名称可能已存在或工号重复",
            error_code="MEMBER_SAVE_FAILED",
        ) from e

    return await gtAgentManager.get_agents_by_employ_status(team_id, EmployStatus.ON_BOARD)


async def overwrite_team_agent_employ_status(team_id: int, on_board_agent_ids: list[int] | set[int]) -> tuple[int, int]:
    """按团队成员全集同步在岗/离岗状态，返回 (on_board_count, off_board_count)。"""
    all_agents = await gtAgentManager.get_team_agents(team_id)
    on_board_set = set(on_board_agent_ids)
    on_board_ids = [agent.id for agent in all_agents if agent.id in on_board_set]
    off_board_ids = [agent.id for agent in all_agents if agent.id not in on_board_set]

    await gtAgentManager.batch_update_agent_status(on_board_ids, EmployStatus.ON_BOARD)
    await gtAgentManager.batch_update_agent_status(off_board_ids, EmployStatus.OFF_BOARD)

    return len(on_board_ids), len(off_board_ids)


async def shutdown() -> None:
    global _agents
    close_tasks: List[Any] = [a.close() for a in _agents.values()]
    if close_tasks:
        await asyncio.gather(*close_tasks, return_exceptions=True)
    _agents = {}

import asyncio
import logging
import os
from typing import Any, List

from service.agentService.prompts import BASE_PROMPT, AGENT_IDENTITY_PROMPT
from util import configUtil, i18nUtil
from model.dbModel.gtAgent import GtAgent
from service.agentService.agent import Agent
from service.agentService.driver import normalize_driver_config
from service.agentService.promptBuilder import build_agent_system_prompt
from service import llmService, roomService, persistenceService
from dal.db import gtTeamManager, gtAgentManager, gtRoleTemplateManager, gtAgentTaskManager
from peewee import IntegrityError
from exception import TeamAgentException
from constants import AgentStatus, AgentTaskStatus, DriverType, EmployStatus

logger = logging.getLogger(__name__)

_agents: dict[int, "Agent"] = {}


def _resolve_team_workdir(gt_team: Any, workspace_root: str | None) -> str:
    team_config = gt_team.config or {}
    configured_workdir = ""
    if isinstance(team_config, dict):
        configured_workdir = str(team_config.get("working_directory") or "").strip()

    if configured_workdir:
        return configured_workdir

    assert workspace_root is not None, "workspace_root 未配置"
    return os.path.join(workspace_root, gt_team.name)

async def startup() -> None:
    global _agents
    _agents = {}


async def _load_team_agents(team_id: int, workspace_root: str | None = None) -> None:
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
    default_model = llmService.get_default_model_or_none()
    resolved_workspace_root = workspace_root or app_config.setting.workspace_root

    team_name = gt_team.name
    team_workdir = _resolve_team_workdir(gt_team, resolved_workspace_root)
    os.makedirs(team_workdir, exist_ok=True)
    team_id = gt_team.id

    if default_model is None:
        logger.warning(
            "当前未配置可用的 LLM 服务，Team 运行时仅恢复基础能力，推理任务需等待完成初始化配置: team=%s, team_id=%s",
            team_name,
            team_id,
        )

    for gt_agent in gt_agents:
        assert gt_agent.role_template_id in templates_by_id, (
            f"角色模版不存在: agent={gt_agent.name}, role_template_id={gt_agent.role_template_id}"
        )
        gt_role_template = templates_by_id[gt_agent.role_template_id]

        agent_name = gt_agent.name
        template_name = gt_role_template.name

        # 解析 i18n display_name
        lang = configUtil.get_language()
        agent_i18n = getattr(gt_agent, "i18n", None)
        template_i18n = getattr(gt_role_template, "i18n", None)
        agent_display_name = i18nUtil.extract_i18n_str(
            agent_i18n.get("display_name") if agent_i18n else None,
            default=agent_name,
            lang=lang,
        ) or agent_name
        template_display_name = i18nUtil.extract_i18n_str(
            template_i18n.get("display_name") if template_i18n else None,
            default=template_name,
            lang=lang,
        ) or template_name

        # model 用于日志记录，推理时如果 gt_agent.model 为空则使用配置中的 model
        model_name = gt_agent.model or gt_role_template.model or default_model or ""
        driver_config = normalize_driver_config(
            {
                "driver": gt_agent.driver,
                "allowed_tools": gt_role_template.allowed_tools,
            }
        )
        full_prompt = await build_agent_system_prompt(
            team_id=team_id,
            agent_name=agent_name,
            agent_display_name=agent_display_name,
            template_name=template_name,
            template_display_name=template_display_name,
            template_soul=gt_role_template.soul,
            workdir=team_workdir,
            base_prompt_tmpl=BASE_PROMPT.strip(),
            identity_prompt_tmpl=AGENT_IDENTITY_PROMPT.strip(),
        )

        assert gt_agent.id is not None and gt_agent.id > 0, f"invalid agent id: {gt_agent.id}"
        agent = Agent(
            gt_agent=gt_agent,
            system_prompt=full_prompt,
            driver_config=driver_config,
            agent_workdir=team_workdir,
        )
        _agents[gt_agent.id] = agent
        logger.info(
            "创建 Agent 实例: agent_id=%s, template=%s, model=%s, driver=%s",
            gt_agent.id,
            template_name,
            model_name or "<unconfigured>",
            driver_config.driver_type,
        )
        await agent.startup()


async def load_team_agents(team_id: int, workspace_root: str | None = None) -> None:
    """从数据库读取指定 Team 的 Agent 配置，并创建对应的内存 Agent 实例。"""
    await _load_team_agents(team_id, workspace_root=workspace_root)


async def load_all_team_agents(workspace_root: str | None = None) -> None:
    """从数据库读取所有 Team 的 Agent 配置，并创建对应的内存 Agent 实例。"""
    for gt_team in await gtTeamManager.get_all_teams():
        await load_team_agents(gt_team.id, workspace_root=workspace_root)


async def _unload_team_agents(team_id: int) -> None:
    keys_to_remove = [agent_id for agent_id, agent in _agents.items() if agent.gt_agent.team_id == team_id]
    close_tasks: list[Any] = []
    for agent_id in keys_to_remove:
        close_tasks.append(_agents[agent_id].close())
    if close_tasks:
        await asyncio.gather(*close_tasks, return_exceptions=True)
    for agent_id in keys_to_remove:
        _agents.pop(agent_id, None)


async def unload_team(team_id: int) -> None:
    """关闭并移除指定 Team 的内存 Agent 实例。"""
    await _unload_team_agents(team_id)


async def _restore_agent_runtime_state(
    agent: Agent,
    *,
    running_task_error_message: str,
) -> None:
    """恢复单个 Agent 的 history，并将遗留 RUNNING task 标记为 FAILED。"""
    items = await persistenceService.load_agent_history_message(agent.gt_agent.id)
    agent.inject_history_messages(items)

    if not configUtil.get_app_config().setting.demo_mode.read_only:
        await persistenceService.fail_running_tasks(
            agent.gt_agent.id,
            error_message=running_task_error_message,
        )

    first_task = await gtAgentTaskManager.get_first_unfinish_task(agent.gt_agent.id)
    agent.task_consumer.status = AgentStatus.FAILED if (
        first_task is not None and first_task.status == AgentTaskStatus.FAILED
    ) else AgentStatus.IDLE


async def restore_team_agents_runtime_state(
    team_id: int,
    *,
    running_task_error_message: str = "task interrupted by team runtime restart",
) -> None:
    """恢复指定 Team 下所有内存 Agent 的 history 和 task 状态。"""
    for agent in get_team_agents(team_id):
        await _restore_agent_runtime_state(
            agent,
            running_task_error_message=running_task_error_message,
        )


async def restore_all_agents_runtime_state() -> None:
    """恢复所有内存 Agent 的 history，并将遗留 RUNNING task 标记为 FAILED。"""
    for agent in _agents.values():
        await _restore_agent_runtime_state(
            agent,
            running_task_error_message="task interrupted by process restart",
        )


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
    return [_agents[aid] for aid in room.agents if aid in _agents]


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
            agent.i18n = data.i18n or {}
        else:
            agent = GtAgent(
                team_id=team_id,
                name=data.name,
                role_template_id=data.role_template_id,
                model=data.model or "",
                driver=data.driver or DriverType.NATIVE,
                employ_status=EmployStatus.ON_BOARD,
                i18n=data.i18n or {},
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

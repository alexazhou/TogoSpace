import asyncio
import logging
import os
from typing import Any, List

from model.dbModel.base import atomic
from model.dbModel.gtDept import GtDept
from model.coreModel.agentModel import (
    AgentRuntimeInfo,
    ExecEnvState,
)
from model.coreModel.containerModel import ContainerInfo, ContainerTarget
from model.coreModel.serviceStatus import ServiceStatusInfo
from service.agentService.prompts import BASE_PROMPT, AGENT_IDENTITY_PROMPT
from util import configUtil, i18nUtil, assertUtil, workspaceUtil
from model.dbModel.gtAgent import GtAgent
from service.agentService.agent import Agent
from service.agentService.driver import normalize_driver_config
from service.agentService.promptBuilder import build_agent_system_prompt
from service import llmService, roomService, persistenceService, deptService, agentActivityService, serviceUtil, containerService
from dal.db import gtTeamManager, gtAgentManager, gtRoleTemplateManager, gtScheculeTaskManager
from peewee import IntegrityError
from exception import TogoException
from constants import (
    ContainerState,
    AgentStatus,
    AgentTaskStatus,
    DriverType,
    EmployStatus,
    SpecialAgent,
)

logger = logging.getLogger(__name__)

_agents: dict[int, "Agent"] = {}

s_status = ServiceStatusInfo(name="agentService")


async def rename_agent_workdir(
    team_id: int,
    old_name: str,
    new_name: str,
) -> list[tuple[str, str]]:
    """按 team_id 重命名单个 Agent 的私有目录，返回实际执行的移动。

    只做目录移动。改名前的容器清理由调用方负责（见 `save_agent`）：容器的
    bind mount 在创建时固化了 `<team>/agent/<old_name>` 这个绝对路径，目录一
    改名旧容器就指向不存在的路径，所以调用方必须在移动前清掉旧容器（§7）。
    """
    if old_name == new_name:
        return []
    gt_team = await gtTeamManager.get_team_by_id(team_id)
    if gt_team is None:
        return []
    return workspaceUtil.rename_agent_workdirs(gt_team.name, gt_team.config, [(old_name, new_name)])


# ---------------------------------------------------------------------------
# 执行环境切换（§5.1 / §5.2）——执行环境属于 Agent，容器侧只提供原语
# ---------------------------------------------------------------------------


async def enter_container(agent_id: int, container_id: str) -> tuple[ContainerTarget, ContainerState]:
    """把 Agent 的执行环境切换到指定容器，返回 `(目标, 进入前状态)`。

    编排由 `Agent.enter_container` 负责；本层只按 `agent_id` 解析已加载实例。
    执行环境是 Agent 的状态，调用方不应持有 Driver。
    """
    agent = _agents.get(agent_id)
    if agent is None:
        raise TogoException(
            "当前 Agent 未加载，无法切换执行环境",
            containerService.ERROR_CONTAINER_INVALID_PARAMS,
        )
    return await agent.enter_container(container_id)


async def exit_to_host(agent_id: int) -> None:
    """把 Agent 的执行环境切回宿主（§5.2）；编排由 `Agent.exit_to_host` 负责。"""
    agent = _agents.get(agent_id)
    if agent is None:
        raise TogoException(
            "当前 Agent 未加载，无法切换执行环境",
            containerService.ERROR_CONTAINER_INVALID_PARAMS,
        )
    await agent.exit_to_host()


# ---------------------------------------------------------------------------
# 容器清理（§7）——目录改名/删除前必须先把 bind mount 固化的旧容器清掉
# ---------------------------------------------------------------------------


async def switch_agent_to_host(agent_id: int) -> bool:
    """把已加载的 Agent 切回宿主环境；返回是否处于宿主（best-effort，不抛异常）。"""
    agent = _agents.get(agent_id)
    if agent is None:
        return True
    return await agent.switch_to_host()


async def switch_team_agents_to_host(team_id: int) -> None:
    """容器清理前把该 Team **已加载**的 Agent 切回宿主（best-effort）。

    只处理内存里的成员：切回宿主对未加载的 Agent 没有意义，所以这里不查库，
    Team 改名这条路径因此完全不依赖 DB 可用性。
    """
    for agent_id in _loaded_agent_ids_of_team(team_id):
        await switch_agent_to_host(agent_id)


def _loaded_agent_ids_of_team(team_id: int) -> list[int]:
    """该 Team 当前已加载到内存的 Agent id 列表。"""
    return [
        agent_id for agent_id, agent in _agents.items()
        if agent.gt_agent.team_id == team_id
    ]


async def _reset() -> None:
    """纯资源清理：正常 shutdown 与启动失败回滚共用；幂等。"""
    global _agents
    close_tasks: List[Any] = [a.close() for a in _agents.values()]
    if close_tasks:
        await asyncio.gather(*close_tasks, return_exceptions=True)
    _agents.clear()


@serviceUtil.service_start_wrapper(s_status, on_error=_reset)
async def startup() -> None:
    global _agents
    _agents = {}
    await _ensure_special_agents_exist()


async def _ensure_special_agents_exist() -> None:
    """确保数据库中存在 SpecialAgent 记录（跨团队 Agent，team_id=-1）。"""
    special_agent_i18n = {
        SpecialAgent.SYSTEM: {"zh-CN": "系统提醒", "en": "SYSTEM"},
        SpecialAgent.OPERATOR: {"zh-CN": "操作者", "en": "OPERATOR"},
    }
    for special in SpecialAgent:
        agent_id = int(special.value)
        existing = await gtAgentManager.get_agents_by_ids([agent_id])
        if not existing:
            await GtAgent.insert_many([
                {
                    "id": agent_id,
                    "team_id": -1,
                    "name": special.name,
                    "role_template_id": 0,
                    "employ_status": EmployStatus.ON_BOARD,
                    "employee_number": agent_id,  # 使用负数避免与正常 agent 冲突
                    "i18n": {"display_name": special_agent_i18n[special]},
                }
            ]).aio_execute()
            logger.info(f"创建 SpecialAgent 记录: id={agent_id}, name={special.name}")


async def _init_and_start_agent(
    gt_agent: GtAgent,
    gt_role_template: Any,
    team_id: int,
    team_name: str,
    agent_workdir: str,
    shared_workdir: str,
    default_model: str | None,
    is_root_leader: bool
) -> Agent:
    agent_name = gt_agent.name
    template_name = gt_role_template.name

    agent_display_name = gt_agent.display_name
    template_display_name = gt_role_template.display_name

    # model 用于日志记录，推理时如果 gt_agent.model 为空则使用配置中的 model
    model_name = gt_agent.model or default_model or ""
    driver_config = normalize_driver_config(
        {
            "driver": gt_agent.driver,
        }
    )
    driver_config.options["tool_allow_specs"] = gt_agent.allow_tools
    driver_config.options["is_root_leader"] = is_root_leader
    os.makedirs(agent_workdir, exist_ok=True)
    full_prompt = await build_agent_system_prompt(
        team_id=team_id,
        agent_id=gt_agent.id,
        agent_name=agent_name,
        agent_display_name=agent_display_name,
        template_name=template_name,
        template_display_name=template_display_name,
        template_soul=gt_role_template.soul,
        workdir=agent_workdir,
        shared_workdir=shared_workdir,
        base_prompt_tmpl=BASE_PROMPT.strip(),
        identity_prompt_tmpl=AGENT_IDENTITY_PROMPT.strip(),
        is_root_leader=is_root_leader,
        allow_skills=gt_agent.allow_skills,
    )

    assert gt_agent.id is not None and gt_agent.id > 0, f"invalid agent id: {gt_agent.id}"
    agent = Agent(
        gt_agent=gt_agent,
        system_prompt=full_prompt,
        driver_config=driver_config,
        agent_workdir=agent_workdir,
        is_root_leader=is_root_leader,
    )
    
    logger.info(
        "创建 Agent 实例: agent_id=%s, name=%s, template=%s, model=%s, driver=%s, is_root_leader=%s",
        gt_agent.id,
        gt_agent.name,
        template_name,
        model_name or "<unconfigured>",
        driver_config.driver_type,
        is_root_leader,
    )
    await agent.startup()
    return agent


async def _load_team_agents(team_id: int, workspace_root: str | None = None) -> None:
    gt_team = await gtTeamManager.get_team_by_id(team_id)
    if gt_team is None:
        logger.warning(f"加载 Team Agent 失败: team_id={team_id} 不存在于配置中")
        return

    # 只加载在职 agent
    gt_agents = await gtAgentManager.get_team_all_agents(team_id, EmployStatus.ON_BOARD)
    gt_role_templates = await gtRoleTemplateManager.get_role_templates_by_ids(
        [agent.role_template_id for agent in gt_agents]
    )
    templates_by_id = {template.id: template for template in gt_role_templates}
    dept_root:GtDept = await deptService.get_dept_tree(team_id)
    top_manager_id = dept_root.manager_id if dept_root is not None else None

    app_config = configUtil.get_app_config()
    default_model = llmService.get_default_model_or_none()
    resolved_workspace_root = workspace_root or app_config.setting.workspace_root

    team_name = gt_team.name
    team_workdir = workspaceUtil.resolve_team_workdir(gt_team.name, gt_team.config, resolved_workspace_root)
    os.makedirs(team_workdir, exist_ok=True)
    shared_workdir = workspaceUtil.resolve_shared_workdir(team_workdir)
    os.makedirs(shared_workdir, exist_ok=True)
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
        is_root_leader = top_manager_id is not None and gt_agent.id == top_manager_id

        agent = await _init_and_start_agent(
            gt_agent=gt_agent,
            gt_role_template=gt_role_template,
            team_id=team_id,
            team_name=team_name,
            agent_workdir=workspaceUtil.resolve_agent_workdir(team_workdir, gt_agent.name),
            shared_workdir=shared_workdir,
            default_model=default_model,
            is_root_leader=is_root_leader,
        )
        _agents[gt_agent.id] = agent


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


async def hot_reload_agent(agent_id: int) -> None:
    """无缝热更新单体 Agent"""
    gt_agent = await gtAgentManager.get_agent_by_id(agent_id)
    if not gt_agent or gt_agent.employ_status != EmployStatus.ON_BOARD:
        return

    team_id = gt_agent.team_id
    gt_team = await gtTeamManager.get_team_by_id(team_id)
    if not gt_team:
        return

    # 获取依赖的模版、部门树等上下文
    gt_role_template = await gtRoleTemplateManager.get_role_template_by_id(gt_agent.role_template_id)
    assertUtil.assertNotNull(
        gt_role_template,
        error_message=f"角色模版不存在: role_template_id={gt_agent.role_template_id}",
        error_code="template_not_found"
    )

    dept_root = await deptService.get_dept_tree(team_id)
    top_manager_id = dept_root.manager_id if dept_root is not None else None
    is_root_leader = top_manager_id is not None and gt_agent.id == top_manager_id

    app_config = configUtil.get_app_config()
    default_model = llmService.get_default_model_or_none()
    team_workdir = workspaceUtil.resolve_team_workdir(gt_team.name, gt_team.config, app_config.setting.workspace_root)
    os.makedirs(team_workdir, exist_ok=True)
    shared_workdir = workspaceUtil.resolve_shared_workdir(team_workdir)
    os.makedirs(shared_workdir, exist_ok=True)

    # 1. 构建新 Agent（不影响旧的运行）
    new_agent = await _init_and_start_agent(
        gt_agent=gt_agent,
        gt_role_template=gt_role_template,
        team_id=team_id,
        team_name=gt_team.name,
        agent_workdir=workspaceUtil.resolve_agent_workdir(team_workdir, gt_agent.name),
        shared_workdir=shared_workdir,
        default_model=default_model,
        is_root_leader=is_root_leader,
    )

    # 2. 拿到旧 Agent 并优先停止（防止它继续向 DB 写入脏状态）
    old_agent = _agents.get(agent_id)
    if old_agent:
        old_agent.stop_consumer_task()
        await old_agent.close()

    # 3. 在完全干净的状态下恢复新 Agent 的上下文
    await _restore_agent_runtime_state(new_agent, running_task_error_message="Agent attributes reloaded")

    # 4. 最后暴露新 Agent 并启动消费循环（原子操作）
    _agents[agent_id] = new_agent
    new_agent.start_consumer_task()
    
    logger.info("单体 Agent '%s' (%d) 热更新完成", gt_agent.name, agent_id)


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
        await agentActivityService.fail_started_activities(
            agent.gt_agent.id,
            error_message=running_task_error_message,
        )

    first_task = await gtScheculeTaskManager.get_first_unfinish_task(agent.gt_agent.id)
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


def get_agent_or_none(agent_id: int) -> "Agent | None":
    return _agents.get(agent_id)


def get_all_agents() -> list["Agent"]:
    return list(_agents.values())


def get_team_agents(team_id: int) -> list["Agent"]:
    return [agent for agent in _agents.values() if agent.gt_agent.team_id == team_id]


def get_gt_agent_by_id(agent_id: int) -> GtAgent | None:
    """根据 agent_id 返回 GtAgent 数据对象；未加载时返回 None。"""
    agent = _agents.get(agent_id)
    return agent.gt_agent if agent is not None else None


def get_agent_display_name(agent_id: int) -> str:
    """返回 agent_id 对应的显示名（i18n display_name），未加载时 fallback 到 SpecialAgent 名或 str(id)。"""
    agent = _agents.get(agent_id)
    if agent is not None:
        return agent.gt_agent.display_name
    special = SpecialAgent.value_of(agent_id)
    return special.name if special is not None else str(agent_id)


def get_agent_stable_name(agent_id: int) -> str:
    """返回 agent_id 对应的稳定标识名（name 字段）。未加载时 fallback 到 SpecialAgent 名或 str(id)。"""
    agent = _agents.get(agent_id)
    if agent is not None:
        return agent.gt_agent.name
    special = SpecialAgent.value_of(agent_id)
    return special.name if special is not None else str(agent_id)


def get_agent_id_by_stable_name(team_id: int, name: str) -> int | None:
    """按 team_id + 稳定名查找 agent_id（仅在已加载 Agent 中查找），SpecialAgent 名直接返回其 value。"""
    special = SpecialAgent.value_of(name)
    if special is not None:
        return int(special.value)
    for agent in _agents.values():
        if agent.gt_agent.team_id == team_id and agent.gt_agent.name == name:
            return agent.gt_agent.id
    return None


def get_agent_i18n(agent_id: int) -> dict:
    """返回 agent_id 对应的 i18n 字典。未加载时返回空字典。"""
    agent = _agents.get(agent_id)
    if agent is not None:
        return agent.gt_agent.i18n or {}
    return {}


def get_team_runtime_status_map(team_id: int) -> dict[int, AgentStatus]:
    return {
        agent.gt_agent.id: agent.status
        for agent in _agents.values()
        if agent.gt_agent.id > 0 and agent.gt_agent.team_id == team_id
    }


def get_exec_env_state(agent_id: int) -> ExecEnvState:
    """读取某个 Agent 的执行环境（§8 列表页字段）。

    未加载的 Agent 恒为 `host`：执行环境只存在于 Driver 内存，后端重启后一律
    回到宿主，所以「不在内存里」与「在宿主」是同一个事实，不能凭库里有什么去猜。
    """
    agent = _agents.get(agent_id)
    if agent is None:
        return ExecEnvState()
    return agent.get_exec_env_state() or ExecEnvState()


def get_team_exec_env_map(team_id: int) -> dict[int, ExecEnvState]:
    """批量读取该 Team 已加载 Agent 的执行环境（列表页用，避免逐个查）。"""
    result: dict[int, ExecEnvState] = {}
    for agent in _agents.values():
        if agent.gt_agent.team_id != team_id or agent.gt_agent.id is None:
            continue
        state = agent.get_exec_env_state()
        if state is None:
            continue
        result[agent.gt_agent.id] = state
    return result


async def get_agent_detail_info(agent: GtAgent) -> AgentRuntimeInfo:
    """组装详情页响应：Agent 字段 + 运行时状态 + 执行环境 + 容器数。"""
    runtime_status_map = get_team_runtime_status_map(agent.team_id)
    exec_state = get_exec_env_state(agent.id)
    # 容器列表走独立接口，详情只保留数量
    container_count_map = await containerService.count_containers_by_agent_ids([agent.id])

    return AgentRuntimeInfo(
        agent=agent,
        status=runtime_status_map.get(agent.id, AgentStatus.IDLE),
        exec_env_state=exec_state,
        container_count=container_count_map.get(agent.id, 0),
    )


async def get_agent_error_message(agent_id: int) -> str | None:
    """返回 Agent 最早未完成任务的失败信息；没有失败任务时为 None。"""
    first_task = await gtScheculeTaskManager.get_first_unfinish_task(agent_id)
    if first_task is not None and first_task.status == AgentTaskStatus.FAILED:
        return first_task.error_message
    return None


async def get_agent_containers(agent_id: int) -> list[ContainerInfo]:
    """查询 Agent 的自建容器列表（含预记录），并标注当前进入的容器。"""
    exec_state = get_exec_env_state(agent_id)
    return await containerService.list_agent_containers(
        agent_id,
        current_container_id=exec_state.current_container_id,
    )


async def get_team_agent_list_info(
    team_id: int,
    *,
    include_special: bool = False,
) -> list[AgentRuntimeInfo]:
    """组装列表页数据：成员配置 + 运行时状态 + 执行环境 + 容器数。"""
    agents = await gtAgentManager.get_team_all_agents(
        team_id,
        include_cross_team=include_special,
    )
    runtime_status_map = get_team_runtime_status_map(team_id)
    exec_env_map = get_team_exec_env_map(team_id)
    # 容器数只查 DB，列表页不能因为 docker 慢而卡住
    container_count_map = await containerService.count_containers_by_agent_ids(
        [agent.id for agent in agents if agent.id is not None]
    )

    items: list[AgentRuntimeInfo] = []
    for agent in agents:
        exec_state = exec_env_map.get(agent.id, ExecEnvState())
        items.append(
            AgentRuntimeInfo(
                agent=agent,
                status=runtime_status_map.get(agent.id, AgentStatus.IDLE),
                exec_env_state=exec_state,
                container_count=container_count_map.get(agent.id, 0),
            )
        )
    return items


def get_room_agents(room_id: int) -> List["Agent"]:
    room = roomService.get_room(room_id)
    if room is None:
        return []
    return [_agents[aid] for aid in room.get_agent_ids() if aid in _agents]


async def overwrite_team_agents(team_id: int, agents_data: list[GtAgent]) -> list[GtAgent]:
    """全量覆盖成员列表：有 id 更新，无 id 创建，不在列表的设为离职状态。返回在职成员列表。"""
    team = await gtTeamManager.get_team_by_id(team_id)
    assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")
    assertUtil.assertFalse(team.enabled, error_message="团队必须处于停用状态才能编辑成员", error_code="team_not_stopped")

    existing_agents = await gtAgentManager.get_team_all_agents(team_id)
    existing_ids = {a.id for a in existing_agents}
    existing_by_id = {a.id: a for a in existing_agents}
    request_ids = {agent.id for agent in agents_data if agent.id is not None}

    # 名称同时是 Agent 私有目录名，先校验字符与重名，避免路径问题或共用目录
    workspaceUtil.assert_valid_dir_names(
        (data.name for data in agents_data), error_code="invalid_agent_name",
    )

    final_names = [agent.name for agent in agents_data]
    duplicate_names = sorted({name for name in final_names if final_names.count(name) > 1})
    if duplicate_names:
        raise TogoException(
            error_message=f"agent name 重复: {duplicate_names}",
            error_code="duplicate_agent_name",
        )

    # 1. 转换为 GtAgent 对象列表（同时校验 ID，不产生任何写入）
    agents_to_save: list[GtAgent] = []
    agent_renames: list[tuple[str, str]] = []
    renamed_agent_ids: list[int] = []
    for data in agents_data:
        agent_id = data.id

        if agent_id is not None:
            existing = existing_by_id.get(agent_id)
            if existing is None:
                raise TogoException(
                    error_message=f"成员 ID 不存在于当前 team: {agent_id}",
                    error_code="agent_not_found",
                )
            agent = existing
            if agent.name != data.name:
                agent_renames.append((agent.name, data.name))
                # 必须在此处收集 id：下面几行会把 existing.name 就地改成新名，
                # 之后再按旧名反查就查不到这个 Agent 了。
                renamed_agent_ids.append(agent_id)
            agent.name = data.name
            agent.role_template_id = data.role_template_id
            agent.model = data.model or ""
            agent.driver = data.driver or DriverType.NATIVE
            agent.employ_status = EmployStatus.ON_BOARD
            agent.i18n = data.i18n or {}
            agent.allow_tools = data.allow_tools
        else:
            agent = GtAgent(
                team_id=team_id,
                name=data.name,
                role_template_id=data.role_template_id,
                model=data.model or "",
                driver=data.driver or DriverType.NATIVE,
                employ_status=EmployStatus.ON_BOARD,
                i18n=data.i18n or {},
                allow_tools=data.allow_tools,
            )

        agents_to_save.append(agent)

    # 2. 先同步 Agent 私有目录；目录冲突会在写库前报错，不会产生数据库变更
    #    改名会改变私有目录的绝对路径，而容器 bind mount 在创建时已固化该路径，
    #    所以必须在移动目录**之前**清掉旧容器（§7）。清理是 best-effort，
    #    失败不阻塞改名（rm 不掉的容器下次 enter_container 会报 container_lost）。
    for renaming_id in renamed_agent_ids:
        await switch_agent_to_host(renaming_id)
        await containerService.delete_agent_containers_safely(renaming_id)

    moved_dirs = workspaceUtil.rename_agent_workdirs(team.name, team.config, agent_renames)

    # 3. 离职处理 + 批量保存放在同一事务里，任一步失败都整体回滚；
    #    写库失败时再把目录改回去，保证库和目录都回到请求前的状态
    ids_to_offboard = existing_ids - request_ids
    try:
        async with atomic():
            if len(ids_to_offboard) > 0:
                await gtAgentManager.batch_update_agent_status(list(ids_to_offboard), EmployStatus.OFF_BOARD)
            await gtAgentManager.batch_save_agents(team_id, agents_to_save)
    except IntegrityError as e:
        workspaceUtil.rollback_agent_workdirs(team.name, moved_dirs)
        raise TogoException(
            error_message="成员保存失败，名称可能已存在或工号重复",
            error_code="MEMBER_SAVE_FAILED",
        ) from e
    except Exception:
        workspaceUtil.rollback_agent_workdirs(team.name, moved_dirs)
        raise

    # 4. 离职成员的容器要在删目录前清掉（§7）：DB 已提交，这些 Agent 不会再被
    #    加载，它们的容器只挂着自己的私有目录，目录一删就是死容器。
    #    名字被在岗成员复用时仍要清——容器属于离职者本人，不是名字。
    for offboard_id in ids_to_offboard:
        await switch_agent_to_host(offboard_id)
        await containerService.delete_agent_containers_safely(offboard_id)

    # 5. 写库成功后再删离职成员的个人目录；名字仍被在岗成员使用时保留
    workspaceUtil.remove_agent_workdirs(
        team.name,
        team.config,
        [existing_by_id[agent_id].name for agent_id in ids_to_offboard],
        [agent.name for agent in agents_to_save],
    )

    return await gtAgentManager.get_team_all_agents(team_id, EmployStatus.ON_BOARD)


async def overwrite_team_agent_employ_status(team_id: int, on_board_agent_ids: list[int] | set[int]) -> tuple[int, int]:
    """按团队成员全集同步在岗/离岗状态，返回 (on_board_count, off_board_count)。"""
    all_agents = await gtAgentManager.get_team_all_agents(team_id)
    on_board_set = set(on_board_agent_ids)
    on_board_ids = [agent.id for agent in all_agents if agent.id in on_board_set]
    off_board_ids = [agent.id for agent in all_agents if agent.id not in on_board_set]

    await gtAgentManager.batch_update_agent_status(on_board_ids, EmployStatus.ON_BOARD)
    await gtAgentManager.batch_update_agent_status(off_board_ids, EmployStatus.OFF_BOARD)

    return len(on_board_ids), len(off_board_ids)


@serviceUtil.service_stop_wrapper(s_status)
async def shutdown() -> None:
    await _reset()

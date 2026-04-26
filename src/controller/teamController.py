# 标准库
from typing import Any

from pydantic import BaseModel, Field

# 内部包
from constants import DriverType, RoomType, SpecialAgent
from controller.baseController import BaseHandler
from dal.db import gtRoomManager, gtTeamManager, gtAgentManager, gtRoleTemplateManager, gtDeptManager
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtRoom import GtRoom
from model.dbModel.gtTeam import GtTeam
from service import roomService, teamService, agentService
from util import assertUtil
from util.configTypes import TeamRoomConfig


def _split_team_config(config: dict | None) -> tuple[str, dict]:
    if not config:
        return "", {}
    copied = config.copy()
    working_directory = copied.pop("working_directory", "")
    return working_directory, copied


def _infer_room_type(agent_names: list[str]) -> RoomType:
    ai_count = len([agent_name for agent_name in agent_names if SpecialAgent.value_of(agent_name) != SpecialAgent.OPERATOR])
    if any(SpecialAgent.value_of(agent_name) == SpecialAgent.OPERATOR for agent_name in agent_names) and ai_count == 1:
        return RoomType.PRIVATE
    return RoomType.GROUP


async def _resolve_room_agent_ids(team_id: int, agent_names: list[str]) -> list[int]:
    normal_names = [agent_name for agent_name in agent_names if SpecialAgent.value_of(agent_name) is None]
    normal_agents = await gtAgentManager.get_team_agents_by_names(team_id, normal_names)
    normal_name_to_id = {agent.name: agent.id for agent in normal_agents}
    agent_ids: list[int] = []
    for agent_name in agent_names:
        special = SpecialAgent.value_of(agent_name)
        if special is not None:
            agent_ids.append(int(special.value))
            continue
        agent_id = normal_name_to_id.get(agent_name)
        if agent_id is not None:
            agent_ids.append(agent_id)
    return agent_ids


async def _to_gt_room(team_id: int, room: TeamRoomConfig) -> GtRoom:
    gt_agent_ids = await _resolve_room_agent_ids(team_id, list(room.agents))
    return GtRoom(
        id=room.id,
        team_id=team_id,
        name=room.name,
        type=_infer_room_type(room.agents),
        initial_topic=room.initial_topic,
        max_turns=roomService.resolve_room_max_turns(room.max_turns),
        agent_ids=gt_agent_ids,
        biz_id=room.biz_id,
        tags=list(room.tags),
    )



async def _get_room_agent_names(team_id: int, agent_ids: list[int]) -> list[str]:
    return [
        agent.name
        for agent in await gtAgentManager.get_team_agents_by_ids(
            team_id,
            agent_ids,
        )
    ]


def _to_gt_agent(agent: "TeamAgentUpdateItem") -> GtAgent:
    return GtAgent(
        id=agent.id,
        name=agent.name,
        role_template_id=agent.role_template_id,
        model=agent.model,
        driver=agent.driver,
    )


# Request Models
class CreateTeamRequest(BaseModel):
    name: str
    working_directory: str = ""
    config: dict = Field(default_factory=dict)


class TeamAgentUpdateItem(BaseModel):
    id: int | None = None
    name: str
    role_template_id: int
    model: str = ""
    driver: DriverType = DriverType.NATIVE


class UpdateTeamRequest(BaseModel):
    working_directory: str | None = None
    config: dict | None = None
    agents: list[TeamAgentUpdateItem] | None = None
    preset_rooms: list[TeamRoomConfig] | None = None


class SetEnabledRequest(BaseModel):
    enabled: bool


def _team_to_dict(team: GtTeam) -> dict[str, Any]:
    working_directory, config = _split_team_config(team.config)
    return {
        "id": team.id,
        "name": team.name,
        "i18n": team.i18n or {},
        "working_directory": working_directory,
        "config": config,
        "enabled": bool(team.enabled),
        "deleted": team.deleted,
        "created_at": team.created_at,
        "updated_at": team.updated_at,
    }


class TeamListHandler(BaseHandler):
    """GET /teams/list.json - 获取所有 Team 列表"""

    async def get(self) -> None:
        enabled_param = self.get_argument("enabled", default=None)
        enabled = None
        if enabled_param is not None:
            enabled = enabled_param.lower() in ("true", "1", "yes")

        teams = await gtTeamManager.get_all_teams(enabled)
        self.return_json({"teams": [_team_to_dict(team) for team in teams]})


class TeamCreateHandler(BaseHandler):
    """POST /teams/create.json - 创建新 Team（自动触发热更新）"""

    async def post(self) -> None:
        request = self.parse_request(CreateTeamRequest)
        config = dict(request.config or {})
        working_directory = request.working_directory
        if working_directory:
            config["working_directory"] = working_directory

        # 调用 service 创建 team
        team_id = await teamService.create_team(
            name=request.name,
            config=config,
        )

        self.return_json({"status": "created", "id": team_id, "name": request.name})


class TeamDetailHandler(BaseHandler):
    """GET /teams/{id}.json - 获取指定 Team 详情"""

    async def get(self, team_id_str: str) -> None:
        team_id = int(team_id_str)
        team = await gtTeamManager.get_team_by_id(team_id)
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")

        rooms = await gtRoomManager.get_rooms_by_team(team_id)
        agents = await gtAgentManager.get_team_all_agents(team_id)
        agent_id_to_name = {agent.id: agent.name for agent in agents}
        agents_data = [
            {
                "id": agent.id,
                "name": agent.name,
                "i18n": agent.i18n or {},
                "role_template_id": agent.role_template_id,
            }
            for agent in agents
        ]
        room_items = []
        for room in rooms:
            agent_ids = list(room.agent_ids or [])
            agent_names = []
            for agent_id in agent_ids:
                special = SpecialAgent.value_of(agent_id)
                if special is not None:
                    agent_names.append(special.name)
                elif agent_id in agent_id_to_name:
                    agent_names.append(agent_id_to_name[agent_id])
                else:
                    agent_names.append(str(agent_id))
            room_items.append(
                {
                    "id": room.id,
                    "name": room.name,
                    "i18n": room.i18n or {},
                    "type": room.type.name,
                    "initial_topic": room.initial_topic,
                    "max_turns": room.max_turns,
                    "agent_ids": agent_ids,
                    "agents": agent_names,
                    "biz_id": room.biz_id,
                    "tags": list(room.tags or []),
                }
            )

        self.return_json(
            {
                "id": team.id,
                "name": team.name,
                "i18n": team.i18n or {},
                "working_directory": _split_team_config(team.config)[0],
                "config": _split_team_config(team.config)[1],
                "enabled": bool(team.enabled),
                "deleted": team.deleted,
                "created_at": team.created_at,
                "updated_at": team.updated_at,
                "agents": agents_data,
                "rooms": room_items,
            }
        )


class TeamModifyHandler(BaseHandler):
    """POST /teams/{id}/modify.json - 更新 Team 配置（自动触发热更新）"""

    async def post(self, team_id_str: str) -> None:
        request = self.parse_request(UpdateTeamRequest)

        # 通过 ID 获取 Team
        team_id = int(team_id_str)
        team = await gtTeamManager.get_team_by_id(team_id)
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")

        team_name = team.name

        if request.working_directory is not None or request.config is not None:
            await teamService.update_team_base_info(
                team_id=team_id,
                working_directory=request.working_directory,
                config_updates=request.config,
            )
        if request.agents is not None:
            _tpl_ids = list({agent.role_template_id for agent in request.agents})
            _fetched = await gtRoleTemplateManager.get_role_templates_by_ids(_tpl_ids)
            assertUtil.assertEqual(len(_fetched), len(_tpl_ids), error_message="部分角色模板不存在", error_code="role_template_not_found")
            await agentService.overwrite_team_agents(
                team_id,
                [_to_gt_agent(agent) for agent in request.agents],
            )
        if request.preset_rooms is not None:
            await roomService.overwrite_team_rooms(
                team_id,
                [await _to_gt_room(team_id, room) for room in request.preset_rooms],
            )
        has_depts = len(await gtDeptManager.get_all_depts(team_id)) > 0
        if has_depts:
            await teamService.hot_reload_team(team_name)

        self.return_json({"status": "updated", "name": team_name})


class TeamDeleteHandler(BaseHandler):
    """POST /teams/{id}/delete.json - 删除 Team（自动触发热更新）"""

    async def post(self, team_id_str: str) -> None:
        # 通过 ID 获取 Team
        team_id = int(team_id_str)
        team = await gtTeamManager.get_team_by_id(team_id)
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")

        team_name = team.name

        # 调用 service 删除 team
        await teamService.delete_team(team_name)

        self.return_json({"status": "deleted", "name": team_name})


class TeamSetEnabledHandler(BaseHandler):
    """POST /teams/{id}/set_enabled.json - 设置 Team 启用状态"""

    async def post(self, team_id_str: str) -> None:
        body = self.parse_request(SetEnabledRequest)
        await teamService.set_team_enabled(int(team_id_str), body.enabled)

        self.return_json({"status": "ok", "enabled": body.enabled})


class TeamClearDataHandler(BaseHandler):
    """POST /teams/{id}/clear_data.json - 清空团队运行数据"""

    async def post(self, team_id_str: str) -> None:
        team_id = int(team_id_str)
        team = await gtTeamManager.get_team_by_id(team_id)
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")

        result = await teamService.clear_team_data(team_id)

        # 清空后触发热更新，重建运行态
        await teamService.hot_reload_team(team.name)

        self.return_json({
            "status": "cleared",
            "team_id": team_id,
            "deleted": result,
        })

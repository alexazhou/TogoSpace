# 标准库
from typing import Any

from pydantic import BaseModel, Field

# 内部包
from constants import DriverType, RoomType, SpecialAgent
from controller.baseController import BaseHandler
from dal.db import gtRoomManager, gtTeamManager, gtAgentManager
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


def _infer_room_type(members: list[str]) -> RoomType:
    ai_count = len([member for member in members if SpecialAgent.value_of(member) != SpecialAgent.OPERATOR])
    if any(SpecialAgent.value_of(member) == SpecialAgent.OPERATOR for member in members) and ai_count == 1:
        return RoomType.PRIVATE
    return RoomType.GROUP


async def _to_gt_room(team_id: int, room: TeamRoomConfig) -> GtRoom:
    member_ids = [
        agent.id
        for agent in await gtAgentManager.get_team_agents_by_names(
            team_id,
            room.members,
            include_special=True,
        )
    ]
    return GtRoom(
        id=room.id,
        team_id=team_id,
        name=room.name,
        type=_infer_room_type(room.members),
        initial_topic=room.initial_topic,
        max_turns=roomService.resolve_room_max_turns(room.max_turns),
        agent_ids=member_ids,
        biz_id=room.biz_id,
        tags=list(room.tags),
    )


def _to_gt_agent(member: "TeamMemberUpdateItem") -> GtAgent:
    return GtAgent(
        id=member.id,
        name=member.name,
        role_template_id=member.role_template_id,
        model=member.model,
        driver=member.driver,
    )


# Request Models
class CreateTeamRequest(BaseModel):
    name: str
    working_directory: str = ""
    config: dict = Field(default_factory=dict)


class TeamMemberUpdateItem(BaseModel):
    id: int | None = None
    name: str
    role_template_id: int
    model: str = ""
    driver: DriverType = DriverType.NATIVE


class UpdateTeamRequest(BaseModel):
    working_directory: str | None = None
    config: dict | None = None
    members: list[TeamMemberUpdateItem] | None = None
    preset_rooms: list[TeamRoomConfig] | None = None


class SetEnabledRequest(BaseModel):
    enabled: bool


def _team_to_dict(team: GtTeam) -> dict[str, Any]:
    working_directory, config = _split_team_config(team.config)
    return {
        "id": team.id,
        "name": team.name,
        "working_directory": working_directory,
        "config": config,
        "enabled": team.enabled,
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
        members = [
            {
                "name": member.name,
                "role_template_id": member.role_template_id,
            }
            for member in await gtAgentManager.get_team_agents(team_id)
        ]
        room_items = []
        for room in rooms:
            room_items.append(
                {
                    "id": room.id,
                    "name": room.name,
                    "initial_topic": room.initial_topic,
                    "max_turns": room.max_turns,
                    "agent_ids": room.agent_ids or [],
                }
            )

        self.return_json(
            {
                "id": team.id,
                "name": team.name,
                "working_directory": _split_team_config(team.config)[0],
                "config": _split_team_config(team.config)[1],
                "enabled": team.enabled,
                "deleted": team.deleted,
                "created_at": team.created_at,
                "updated_at": team.updated_at,
                "members": members,
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
        if request.members is not None:
            await agentService.overwrite_team_agents(
                team_id,
                [_to_gt_agent(member) for member in request.members],
            )
        if request.preset_rooms is not None:
            await roomService.overwrite_team_rooms(
                team_id,
                [await _to_gt_room(team_id, room) for room in request.preset_rooms],
            )
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

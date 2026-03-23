# 标准库
from pydantic import BaseModel

# 内部包
from controller.baseController import BaseHandler
from dal.db import gtTeamManager, gtTeamMemberManager
from service import teamService
from util import assertUtil
from util.configTypes import TeamConfig, TeamMemberConfig, TeamRoomConfig, normalize_team_config


class TeamMemberRequest(BaseModel):
    name: str
    agent: str


# Request Models
class CreateTeamRequest(BaseModel):
    name: str
    working_directory: str = ""
    config: dict = {}
    members: list[TeamMemberRequest]
    preset_rooms: list[TeamRoomConfig]


class UpdateTeamRequest(BaseModel):
    working_directory: str | None = None
    config: dict | None = None
    members: list[TeamMemberRequest] | None = None
    preset_rooms: list[TeamRoomConfig] | None = None


class TeamListHandler(BaseHandler):
    """GET /teams/list.json - 获取所有 Team 列表"""

    async def get(self) -> None:
        teams = await gtTeamManager.get_all_teams()
        self.return_json(
            {
                "teams": [
                    {
                        "id": team.id,
                        "name": team.name,
                        "working_directory": team.working_directory,
                        "config": team.get_config(),
                        "max_function_calls": team.max_function_calls,
                        "enabled": team.enabled,
                        "created_at": team.created_at,
                        "updated_at": team.updated_at,
                    }
                    for team in teams
                ]
            }
        )


class TeamCreateHandler(BaseHandler):
    """POST /teams/create.json - 创建新 Team（自动触发热更新）"""

    async def post(self) -> None:
        request = self.parse_request(CreateTeamRequest)

        team_config: TeamConfig = normalize_team_config({
            "name": request.name,
            "working_directory": request.working_directory,
            "config": request.config,
            "members": [member.model_dump(mode="json") for member in request.members],
            "preset_rooms": request.preset_rooms,
        })

        # 调用 service 创建 team
        await teamService.create_team(team_config)

        self.return_json({"status": "created", "name": request.name})


class TeamDetailHandler(BaseHandler):
    """GET /teams/{id}.json - 获取指定 Team 详情"""

    async def get(self, team_id_str: str) -> None:
        from dal.db import gtRoomManager, gtRoomMemberManager

        team_id = int(team_id_str)
        team = await gtTeamManager.get_team_by_id(team_id)
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")
        if team is None:
            return

        rooms = await gtRoomManager.get_rooms_by_team(team_id)
        members = [
            {
                "name": member.name,
                "agent": member.agent_name,
            }
            for member in await gtTeamMemberManager.get_members_by_team(team_id)
        ]
        room_items = []
        for room in rooms:
            room_members = await gtRoomMemberManager.get_members_by_room(room.id)
            room_items.append(
                {
                    "id": room.id,
                    "name": room.name,
                    "initial_topic": room.initial_topic,
                    "max_turns": room.max_turns,
                    "members": room_members,
                }
            )

        self.return_json(
            {
                "id": team.id,
                "name": team.name,
                "working_directory": team.working_directory,
                "config": team.get_config(),
                "max_function_calls": team.max_function_calls,
                "enabled": team.enabled,
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

        current_config = await gtTeamManager.get_team_config(team_name)
        assertUtil.assertNotNull(current_config, error_message=f"Team '{team_name}' config not found", error_code="team_config_not_found")
        if current_config is None:
            return

        # 构建完整配置，确保局部更新不会丢字段
        team_config: TeamConfig = {
            "name": team_name,
            "working_directory": current_config.get("working_directory", ""),
            "config": dict(current_config.get("config", {})),
            "members": list(current_config["members"]),
            "preset_rooms": list(current_config["preset_rooms"]),
        }
        if "max_function_calls" in current_config:
            team_config["max_function_calls"] = current_config["max_function_calls"]

        if request.working_directory is not None:
            team_config["working_directory"] = request.working_directory
        if request.config is not None:
            team_config["config"] = request.config
        if request.members is not None:
            team_config["members"] = [member.model_dump(mode="json") for member in request.members]
        if request.preset_rooms is not None:
            team_config["preset_rooms"] = request.preset_rooms

        # 调用 service 更新 team
        await teamService.update_team(normalize_team_config(team_config))

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

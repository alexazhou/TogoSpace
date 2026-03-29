# 标准库
from pydantic import BaseModel, Field

# 内部包
from controller.baseController import BaseHandler
from dal.db import gtRoomManager, gtTeamManager, gtAgentManager
from service import roomService, teamService
from util import assertUtil
from util.configTypes import TeamConfig, AgentConfig, TeamRoomConfig


def _split_team_config(config: dict) -> tuple[str, dict]:
    copied = dict(config)
    working_directory = copied.pop("working_directory", "")
    return working_directory, copied


# Request Models
class CreateTeamRequest(BaseModel):
    name: str
    working_directory: str = ""
    config: dict = Field(default_factory=dict)


class UpdateTeamRequest(BaseModel):
    working_directory: str | None = None
    config: dict | None = None
    members: list[AgentConfig] | None = None
    preset_rooms: list[TeamRoomConfig] | None = None


class SetEnabledRequest(BaseModel):
    enabled: bool


class TeamListHandler(BaseHandler):
    """GET /teams/list.json - 获取所有 Team 列表"""

    async def get(self) -> None:
        enabled_param = self.get_argument("enabled", default=None)
        enabled = None
        if enabled_param is not None:
            enabled = enabled_param.lower() in ("true", "1", "yes")

        teams = await gtTeamManager.get_all_teams(enabled)
        self.return_json(
            {
                "teams": [
                    {
                        "id": team.id,
                        "name": team.name,
                        "working_directory": _split_team_config(team.get_config())[0],
                        "config": _split_team_config(team.get_config())[1],
                        "max_function_calls": team.max_function_calls,
                        "enabled": team.enabled,
                        "deleted": team.deleted,
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
        payload = request.model_dump()
        working_directory = payload.pop("working_directory", "")
        config = dict(payload.get("config") or {})
        if working_directory:
            config["working_directory"] = working_directory
        payload["config"] = config
        team_config = TeamConfig.model_validate(payload)

        # 调用 service 创建 team
        team_id = await teamService.create_team(team_config)

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
            for member in await gtAgentManager.get_agents_by_team(team_id)
        ]
        room_items = []
        for room in rooms:
            room_members = await roomService.get_db_room_member_names(room.id)
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
                "working_directory": _split_team_config(team.get_config())[0],
                "config": _split_team_config(team.get_config())[1],
                "max_function_calls": team.max_function_calls,
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

        current_config = await teamService.get_team_config(team_name)
        assertUtil.assertNotNull(current_config, error_message=f"Team '{team_name}' config not found", error_code="team_config_not_found")

        # 构建完整配置，确保局部更新不会丢字段
        updates = {k: v for k, v in request.model_dump(exclude_none=True).items()}
        config = dict(current_config.config)
        if "config" in updates:
            config.update(updates.pop("config") or {})
        working_directory = updates.pop("working_directory", None)
        if working_directory is not None:
            if working_directory:
                config["working_directory"] = working_directory
            else:
                config.pop("working_directory", None)
        if config != current_config.config:
            updates["config"] = config
        team_config = current_config.model_copy(update=updates)

        # 调用 service 更新 team
        await teamService.update_team(team_config)


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

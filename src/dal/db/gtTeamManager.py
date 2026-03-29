from __future__ import annotations

import json
import logging

from constants import DriverType
from . import gtRoomManager, gtAgentManager, gtRoleTemplateManager
from model.dbModel.gtTeam import GtTeam
from util.configTypes import TeamConfig, AgentConfig, TeamRoomConfig

logger = logging.getLogger(__name__)


def _iter_team_rooms(team_config: TeamConfig) -> list[TeamRoomConfig]:
    return team_config.preset_rooms


# Team CRUD
async def get_team(name: str) -> GtTeam | None:
    """获取指定 Team（未删除的）。"""
    return await GtTeam.aio_get_or_none((GtTeam.name == name) & (GtTeam.deleted == 0))


async def get_team_by_id(team_id: int) -> GtTeam | None:
    """通过 ID 获取指定 Team。"""
    return await GtTeam.aio_get_or_none(GtTeam.id == team_id)


async def get_all_teams(enabled: bool | None = None) -> list[GtTeam]:
    """获取所有未删除的 Team。可通过 enabled 参数过滤。"""
    query = GtTeam.select().where(GtTeam.deleted == 0).order_by(GtTeam.name)
    if enabled is not None:
        query = query.where(GtTeam.enabled == 1 if enabled else GtTeam.enabled == 0)
    return list(await query.aio_execute())


async def upsert_team(team_config: TeamConfig) -> GtTeam:
    """创建或更新 Team。"""
    name = team_config.name
    config_json = json.dumps(team_config.config, ensure_ascii=False, sort_keys=True)
    max_function_calls = team_config.max_function_calls if team_config.max_function_calls is not None else 5

    await (
        GtTeam.insert(
            name=name,
            config=config_json,
            max_function_calls=max_function_calls,
        )
        .on_conflict(
            conflict_target=[GtTeam.name],
            update={
                GtTeam.config: config_json,
                GtTeam.max_function_calls: max_function_calls,
                GtTeam.updated_at: GtTeam._now(),
            },
        )
        .aio_execute()
    )

    row = await GtTeam.aio_get_or_none(GtTeam.name == name)
    if row is None:
        raise RuntimeError(f"team upsert failed: {name}")
    return row


async def delete_team(name: str) -> None:
    """删除 Team（设置 deleted=1）。"""
    await (
        GtTeam.update(deleted=1, updated_at=GtTeam._now())
        .where(GtTeam.name == name)
        .aio_execute()
    )


async def set_team_enabled(team_id: int, enabled: bool) -> None:
    """设置 Team 的启用状态。"""
    await (
        GtTeam.update(enabled=1 if enabled else 0, updated_at=GtTeam._now())
        .where(GtTeam.id == team_id)
        .aio_execute()
    )


async def team_exists(name: str) -> bool:
    """检查 Team 是否存在且未删除且已启用。"""
    row = await GtTeam.aio_get_or_none((GtTeam.name == name) & (GtTeam.deleted == 0) & (GtTeam.enabled == 1))
    return row is not None


# 完整配置获取
async def get_team_config(name: str) -> TeamConfig | None:
    """获取指定 Team 的完整配置（类似 JSON 格式）。"""

    team = await get_team(name)
    if team is None:
        return None

    team_id = team.id

    agent_rows = await gtAgentManager.get_agents_by_team(team_id)
    template_rows = await gtRoleTemplateManager.get_role_templates_by_ids(
        [member.role_template_id for member in agent_rows]
    )
    templates_by_id = {template.id: template for template in template_rows}

    members: list[AgentConfig] = []
    for member in agent_rows:
        template = templates_by_id.get(member.role_template_id)
        if template is None:
            logger.warning(
                "Agent '%s' 引用的角色模板不存在: role_template_id=%s",
                member.name,
                member.role_template_id,
            )
            continue
        members.append(
            AgentConfig(
                name=member.name,
                role_template=template.template_name,
                model=member.model or None,
                driver=member.driver if isinstance(member.driver, DriverType) else DriverType.NATIVE,
            )
        )

    rooms: list[TeamRoomConfig] = []
    for room in await gtRoomManager.get_rooms_by_team(team_id):
        room_members = await gtRoomManager.get_members_by_room(room.id)
        rooms.append(TeamRoomConfig(
            name=room.name,
            initial_topic=room.initial_topic,
            max_turns=room.max_turns,
            members=room_members,
        ))

    return TeamConfig(
        name=team.name,
        config=team.get_config(),
        members=members,
        preset_rooms=rooms,
        max_function_calls=team.max_function_calls,
    )


async def get_all_team_configs() -> list[TeamConfig]:
    """获取所有 Team 的完整配置列表。"""
    result = []
    for team in await get_all_teams():
        config = await get_team_config(team.name)
        if config:
            result.append(config)
    return result


# JSON 到数据库的转换
async def import_team_from_config(team_config: TeamConfig) -> None:
    """从 TeamConfig 导入 Team 到数据库。"""

    name = team_config.name

    # 检查是否已存在
    existing = await get_team(name)
    if existing is not None:
        logger.info(f"Team '{name}' 已存在，跳过导入")
        return

    # 导入 Team
    team = await upsert_team(team_config)
    team_id = team.id
    await gtAgentManager.upsert_agents(team_id, team_config.members)

    # 导入 Rooms（upsert_rooms 会处理成员）
    rooms = _iter_team_rooms(team_config)
    await gtRoomManager.upsert_rooms(team_id, rooms)

    logger.info(f"Team '{name}' 已从 JSON 导入数据库")

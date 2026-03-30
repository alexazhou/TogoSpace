# 标准库
from collections import Counter
from typing import List

# 第三方包
from pydantic import BaseModel, Field

# 内部包
from controller.baseController import BaseHandler
from dal.db import gtTeamManager, gtRoomManager, gtAgentManager
from model.coreModel.gtCoreWebModel import (
    GtCoreMessageInfo,
    GtCoreRoomMessagesResponse,
)
from model.dbModel.gtRoom import GtRoom
from service import roomService as chat_room, teamService
from constants import SpecialAgent, RoomState, RoomType
from util import assertUtil
from util.configTypes import TeamRoomConfig


# Room Config Request Models
class CreateRoomRequest(BaseModel):
    name: str
    type: RoomType = RoomType.GROUP
    initial_topic: str | None = None
    max_turns: int = 100
    member_ids: List[int] = Field(default_factory=list)


class UpdateRoomRequest(BaseModel):
    type: str
    initial_topic: str | None = None
    max_turns: int | None = None


class UpdateMembersRequest(BaseModel):
    members: List[str]


class SendMessageRequest(BaseModel):
    content: str | None = None


async def _resolve_member_names_by_ids(team_id: int, member_ids: List[int]) -> List[str]:
    if len(member_ids) == 0:
        return []

    duplicate_ids = sorted([member_id for member_id, count in Counter(member_ids).items() if count > 1])
    assertUtil.assertEqual(
        len(duplicate_ids),
        0,
        error_message=f"member_ids duplicated: {duplicate_ids}",
        error_code="duplicate_member_ids",
    )

    agent_rows = await gtAgentManager.get_agents_by_ids(member_ids)
    id_to_agent = {agent.id: agent for agent in agent_rows}

    missing_ids = [member_id for member_id in member_ids if member_id not in id_to_agent]
    assertUtil.assertEqual(
        len(missing_ids),
        0,
        error_message=f"members not found: {missing_ids}",
        error_code="member_not_found",
    )

    out_of_team_ids = [member_id for member_id in member_ids if id_to_agent[member_id].team_id != team_id]
    assertUtil.assertEqual(
        len(out_of_team_ids),
        0,
        error_message=f"members not in team '{team_id}': {out_of_team_ids}",
        error_code="member_not_in_team",
    )

    return [id_to_agent[member_id].name for member_id in member_ids]


async def _get_team_room_or_404(team_id: int, room_id: int) -> GtRoom:
    room = await GtRoom.aio_get_or_none(
        (GtRoom.id == room_id) & (GtRoom.team_id == team_id)
    )
    assertUtil.assertNotNull(room, error_message=f"Room ID '{room_id}' not found", error_code="room_not_found")
    return room


class RoomListHandler(BaseHandler):
    async def get(self) -> None:
        team_id_raw = self.get_query_argument("team_id", None)
        team_name = self.get_query_argument("team_name", None)
        if team_id_raw:
            team = await gtTeamManager.get_team_by_id(int(team_id_raw))
            assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id_raw}' not found", error_code="team_not_found")
            team_name = team.name
        rooms: List[chat_room.ChatRoom] = chat_room.get_all_rooms()
        if team_name:
            rooms = [room for room in rooms if room.team_name == team_name]
        data = [r.to_dict() for r in rooms]
        self.return_json({"rooms": data})


class RoomMessagesHandler(BaseHandler):
    """GET /rooms/{id}/messages/list.json; POST /rooms/{id}/messages/send.json"""

    async def get(self, room_id_str: str) -> None:
        # 通过数据库 ID 获取内存中的 ChatRoom
        room_id = int(room_id_str)
        room = chat_room.get_room(room_id)
        assertUtil.assertNotNull(room, error_message=f"room_id '{room_id}' not found", error_code="room_not_found")
        messages = [
            GtCoreMessageInfo(sender=m.sender_name, content=m.content, time=m.send_time)
            for m in room.messages
        ]
        resp = GtCoreRoomMessagesResponse(
            room_id=room.room_id, room_key=room.key, room_name=room.name, team_name=room.team_name, messages=messages
        )
        self.return_json(resp)

    async def post(self, room_id_str: str) -> None:
        # 通过数据库 ID 获取内存中的 ChatRoom
        request = self.parse_request(SendMessageRequest)
        room_id = int(room_id_str)
        room = chat_room.get_room(room_id)
        assertUtil.assertNotNull(room, error_message=f"room_id '{room_id}' not found", error_code="room_not_found")
        assertUtil.assertTrue(
            room.state != RoomState.INIT,
            error_message="room is in init state, not activated by runtime services",
            error_code="room_not_ready",
        )
        content = request.content
        assertUtil.assertNotNull(content, error_message="content is required", error_code="invalid_request")

        await room.add_message(SpecialAgent.OPERATOR.name, content)
        room.finish_turn(SpecialAgent.OPERATOR.name)
        self.return_success()


# Team Room Management Handlers
class TeamRoomsHandler(BaseHandler):
    """GET /teams/{team_id}/rooms/list.json - 获取 Team 下的所有 Room"""

    async def get(self, team_id_str: str) -> None:
        team_id = int(team_id_str)
        assertUtil.assertNotNull(
            await gtTeamManager.get_team_by_id(team_id),
            error_message=f"Team ID '{team_id}' not found",
            error_code="team_not_found",
        )

        rooms = await gtRoomManager.get_rooms_by_team(team_id)
        self.return_json({"rooms": rooms})


class TeamRoomCreateHandler(BaseHandler):
    """POST /teams/{team_id}/rooms/create.json - 在 Team 下创建 Room"""

    async def post(self, team_id_str: str) -> None:
        request = self.parse_request(CreateRoomRequest)

        team_id = int(team_id_str)
        team = await gtTeamManager.get_team_by_id(team_id)
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")
        team_name = team.name

        # 检查房间是否已存在
        existing_rooms = await gtRoomManager.get_rooms_by_team(team_id)
        existing = next((r for r in existing_rooms if r.name == request.name), None)
        assertUtil.assertEqual(existing, None, error_message=f"Room '{request.name}' already exists", error_code="room_exists")

        member_names = await _resolve_member_names_by_ids(team_id, request.member_ids)

        # 构建房间配置
        room_config = TeamRoomConfig(
            name=request.name,
            members=member_names,
            initial_topic=request.initial_topic,
            max_turns=request.max_turns,
        )

        # upsert_rooms 会先删除该 team 下所有房间再重新插入，这在只添加一个房间时可能不太合适
        # 但目前 gtRoomManager 实现如此，暂且遵循。
        new_rooms_configs: list[TeamRoomConfig] = []
        for r in existing_rooms:
            members = await chat_room.get_db_room_member_names(r.id)
            new_rooms_configs.append(TeamRoomConfig(
                name=r.name,
                members=members,
                initial_topic=r.initial_topic,
                max_turns=r.max_turns,
            ))
        new_rooms_configs.append(room_config)

        await chat_room.save_team_rooms_from_config(team_id, new_rooms_configs)
        await teamService.hot_reload_team(team_name)

        self.return_json({"status": "created", "room_name": request.name})


class TeamRoomDetailHandler(BaseHandler):
    """GET /teams/{team_id}/rooms/{room_id}.json - 获取指定 Room 详情"""

    async def get(self, team_id_str: str, room_id_str: str) -> None:
        team_id = int(team_id_str)
        room_id = int(room_id_str)
        assertUtil.assertNotNull(
            await gtTeamManager.get_team_by_id(team_id),
            error_message=f"Team ID '{team_id}' not found",
            error_code="team_not_found",
        )
        room = await _get_team_room_or_404(team_id, room_id)

        members = await chat_room.get_db_room_member_names(room.id)
        data = {
            "id": room.id,
            "name": room.name,
            "type": room.type.name,
            "initial_topic": room.initial_topic,
            "max_turns": room.max_turns,
            "members": members,
        }
        self.return_json(data)


class TeamRoomModifyHandler(BaseHandler):
    """POST /teams/{team_id}/rooms/{room_id}/modify.json - 更新 Room"""

    async def post(self, team_id_str: str, room_id_str: str) -> None:
        request = self.parse_request(UpdateRoomRequest)

        team_id = int(team_id_str)
        room_id = int(room_id_str)
        team = await gtTeamManager.get_team_by_id(team_id)
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")
        team_name = team.name

        room = await _get_team_room_or_404(team_id, room_id)
        room_name = room.name

        initial_topic = request.initial_topic if request.initial_topic is not None else room.initial_topic
        max_turns = request.max_turns if request.max_turns is not None else room.max_turns

        existing_rooms = await gtRoomManager.get_rooms_by_team(team_id)
        all_rooms: list[TeamRoomConfig] = []
        for r in existing_rooms:
            members = await chat_room.get_db_room_member_names(r.id)
            if r.id == room_id:
                all_rooms.append(TeamRoomConfig(
                    name=r.name,
                    members=members,
                    initial_topic=initial_topic,
                    max_turns=max_turns,
                ))
            else:
                all_rooms.append(TeamRoomConfig(
                    name=r.name,
                    members=members,
                    initial_topic=r.initial_topic,
                    max_turns=r.max_turns,
                ))

        await chat_room.save_team_rooms_from_config(team_id, all_rooms)
        await teamService.hot_reload_team(team_name)

        self.return_json({"status": "updated", "room_name": room_name})


class TeamRoomDeleteHandler(BaseHandler):
    """POST /teams/{team_id}/rooms/{room_id}/delete.json - 删除 Room"""

    async def post(self, team_id_str: str, room_id_str: str) -> None:
        team_id = int(team_id_str)
        room_id = int(room_id_str)
        team = await gtTeamManager.get_team_by_id(team_id)
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")
        team_name = team.name

        room = await _get_team_room_or_404(team_id, room_id)
        room_name = room.name

        existing_rooms = await gtRoomManager.get_rooms_by_team(team_id)
        remaining_rooms = [r for r in existing_rooms if r.id != room_id]

        room_configs: list[TeamRoomConfig] = []
        for r in remaining_rooms:
            members = await chat_room.get_db_room_member_names(r.id)
            room_configs.append(
                TeamRoomConfig(
                    name=r.name,
                    members=members,
                    initial_topic=r.initial_topic,
                    max_turns=r.max_turns,
                )
            )

        await chat_room.save_team_rooms_from_config(team_id, room_configs)
        await teamService.hot_reload_team(team_name)

        self.return_json({"status": "deleted", "room_name": room_name})


class TeamRoomMembersHandler(BaseHandler):
    """GET /teams/{team_id}/rooms/{room_id}/members/list.json - 获取 Room 成员"""

    async def get(self, team_id_str: str, room_id_str: str) -> None:
        team_id = int(team_id_str)
        room_id = int(room_id_str)
        assertUtil.assertNotNull(
            await gtTeamManager.get_team_by_id(team_id),
            error_message=f"Team ID '{team_id}' not found",
            error_code="team_not_found",
        )
        room = await _get_team_room_or_404(team_id, room_id)

        members = await chat_room.get_db_room_member_names(room.id)
        self.return_json({"members": members})


class TeamRoomMembersModifyHandler(BaseHandler):
    """POST /teams/{team_id}/rooms/{room_id}/members/modify.json - 更新 Room 成员"""

    async def post(self, team_id_str: str, room_id_str: str) -> None:
        request = self.parse_request(UpdateMembersRequest)

        team_id = int(team_id_str)
        room_id = int(room_id_str)
        team = await gtTeamManager.get_team_by_id(team_id)
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")
        team_name = team.name

        room = await _get_team_room_or_404(team_id, room_id)

        await chat_room.save_room_members(room.id, request.members)
        await teamService.hot_reload_team(team_name)

        self.return_json({"status": "updated", "room_name": room.name})

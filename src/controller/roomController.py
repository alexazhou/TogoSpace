# 标准库
import json
from typing import List

# 第三方包
from pydantic import BaseModel

# 内部包
from controller.baseController import BaseHandler
from dal.db import gtTeamManager, gtRoomManager, gtRoomMemberManager, gtRoomMessageManager
from model.coreModel.gtCoreWebModel import RoomInfo, MessageInfo, RoomMessagesResponse
from model.dbModel.gtRoom import GtRoom
from service import roomService as chat_room, teamService
from constants import SpecialAgent, RoomState
from util import assertUtil
from util.configTypes import TeamRoomConfig


# Room Config Request Models
class CreateRoomRequest(BaseModel):
    name: str
    type: str
    initial_topic: str | None = None
    max_turns: int = 100


class UpdateRoomRequest(BaseModel):
    type: str
    initial_topic: str | None = None
    max_turns: int | None = None


class UpdateMembersRequest(BaseModel):
    members: List[str]


class RoomListHandler(BaseHandler):
    async def get(self) -> None:
        team_name = self.get_query_argument("team_name", None)
        rooms: List[chat_room.ChatRoom] = chat_room.get_all_rooms()
        if team_name:
            rooms = [room for room in rooms if room.team_name == team_name]
        data = []
        for r in rooms:
            data.append(RoomInfo(
                room_id=r.room_id,
                room_key=r.key,
                room_name=r.name,
                team_name=r.team_name,
                room_type=r.room_type.name,
                state=r.state.name,
                members=r.agents
            ).model_dump(mode="json"))
        self.return_json({"rooms": data})


class RoomMessagesHandler(BaseHandler):
    """GET /rooms/{id}/messages/list.json; POST /rooms/{id}/messages/send.json"""

    async def get(self, room_id_str: str) -> None:
        # 通过数据库 ID 获取内存中的 ChatRoom
        room_id = int(room_id_str)
        room: chat_room.ChatRoom | None = None
        try:
            room = chat_room.get_room(room_id)
        except RuntimeError:
            pass
        assertUtil.assertNotNull(room, error_message=f"room_id '{room_id}' not found", error_code="room_not_found")
        messages = [
            MessageInfo(sender=m.sender_name, content=m.content, time=m.send_time)
            for m in room.messages
        ]
        resp = RoomMessagesResponse(
            room_id=room.room_id, room_key=room.key, room_name=room.name, team_name=room.team_name, messages=messages
        )
        self.return_json(resp)

    async def post(self, room_id_str: str) -> None:
        # 通过数据库 ID 获取内存中的 ChatRoom
        room_id = int(room_id_str)
        room: chat_room.ChatRoom | None = None
        try:
            room = chat_room.get_room(room_id)
        except RuntimeError:
            pass
        assertUtil.assertNotNull(room, error_message=f"room_id '{room_id}' not found", error_code="room_not_found")
        assertUtil.assertTrue(
            room.state != RoomState.INIT,
            error_message="room is in init state, not activated by runtime services",
            error_code="room_not_ready",
        )
        body = json.loads(self.request.body)
        content = body.get("content")
        assertUtil.assertNotNull(content, error_message="content is required", error_code="invalid_request")

        await room.add_message(SpecialAgent.OPERATOR.name, content)
        room.finish_turn(SpecialAgent.OPERATOR.name)
        self.return_json({"status": "ok"})


# Team Room Management Handlers
class TeamRoomsHandler(BaseHandler):
    """GET /teams/{team_id}/rooms/list.json - 获取 Team 下的所有 Room"""

    async def get(self, team_id_str: str) -> None:
        team_id = int(team_id_str)
        team = await gtTeamManager.get_team_by_id(team_id)
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")

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

        # 构建房间配置
        room_config: TeamRoomConfig = {
            "name": request.name,
            "members": [],
            "initial_topic": request.initial_topic,
            "max_turns": request.max_turns,
        }

        # upsert_rooms 会先删除该 team 下所有房间再重新插入，这在只添加一个房间时可能不太合适
        # 但目前 gtRoomManager 实现如此，暂且遵循。
        new_rooms_configs: list[TeamRoomConfig] = []
        for r in existing_rooms:
            members = await gtRoomMemberManager.get_members_by_room(r.id)
            new_rooms_configs.append({
                "name": r.name,
                "members": members,
                "initial_topic": r.initial_topic,
                "max_turns": r.max_turns,
            })
        new_rooms_configs.append(room_config)

        await gtRoomManager.upsert_rooms(team_id, new_rooms_configs)
        await teamService.hot_reload_team(team_name)

        self.return_json({"status": "created", "room_name": request.name})


class TeamRoomDetailHandler(BaseHandler):
    """GET /teams/{team_id}/rooms/{room_id}.json - 获取指定 Room 详情"""

    async def get(self, team_id_str: str, room_id_str: str) -> None:
        team_id = int(team_id_str)
        room_id = int(room_id_str)
        team = await gtTeamManager.get_team_by_id(team_id)
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")

        room = await GtRoom.aio_get_or_none(
            (GtRoom.id == room_id) & (GtRoom.team_id == team_id)
        )
        assertUtil.assertNotNull(room, error_message=f"Room ID '{room_id}' not found", error_code="room_not_found")

        members = await gtRoomMemberManager.get_members_by_room(room.id)
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

        room = await GtRoom.aio_get_or_none(
            (GtRoom.id == room_id) & (GtRoom.team_id == team_id)
        )
        assertUtil.assertNotNull(room, error_message=f"Room ID '{room_id}' not found", error_code="room_not_found")
        room_name = room.name

        initial_topic = request.initial_topic if request.initial_topic is not None else room.initial_topic
        max_turns = request.max_turns if request.max_turns is not None else room.max_turns

        existing_rooms = await gtRoomManager.get_rooms_by_team(team_id)
        all_rooms: list[TeamRoomConfig] = []
        for r in existing_rooms:
            members = await gtRoomMemberManager.get_members_by_room(r.id)
            if r.id == room_id:
                all_rooms.append({
                    "name": room_name,
                    "members": members,
                    "initial_topic": initial_topic,
                    "max_turns": max_turns,
                })
            else:
                all_rooms.append({
                    "name": r.name,
                    "members": members,
                    "initial_topic": r.initial_topic,
                    "max_turns": r.max_turns,
                })

        await gtRoomManager.upsert_rooms(team_id, all_rooms)
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

        room = await GtRoom.aio_get_or_none(
            (GtRoom.id == room_id) & (GtRoom.team_id == team_id)
        )
        assertUtil.assertNotNull(room, error_message=f"Room ID '{room_id}' not found", error_code="room_not_found")
        room_name = room.name
        target_room_id = room.id

        existing_rooms = await gtRoomManager.get_rooms_by_team(team_id)
        remaining_rooms = [r for r in existing_rooms if r.id != room_id]

        room_configs: list[TeamRoomConfig] = []
        for r in remaining_rooms:
            members = await gtRoomMemberManager.get_members_by_room(r.id)
            room_configs.append(
                {
                    "name": r.name,
                    "members": members,
                    "initial_topic": r.initial_topic,
                    "max_turns": r.max_turns,
                }
            )

        await gtRoomManager.upsert_rooms(team_id, room_configs)

        await gtRoomMemberManager.delete_members_by_room(target_room_id)
        await teamService.hot_reload_team(team_name)

        self.return_json({"status": "deleted", "room_name": room_name})


class TeamRoomMembersHandler(BaseHandler):
    """GET /teams/{team_id}/rooms/{room_id}/members/list.json - 获取 Room 成员"""

    async def get(self, team_id_str: str, room_id_str: str) -> None:
        team_id = int(team_id_str)
        room_id = int(room_id_str)
        team = await gtTeamManager.get_team_by_id(team_id)
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")

        room = await GtRoom.aio_get_or_none(
            (GtRoom.id == room_id) & (GtRoom.team_id == team_id)
        )
        assertUtil.assertNotNull(room, error_message=f"Room ID '{room_id}' not found", error_code="room_not_found")

        members = await gtRoomMemberManager.get_members_by_room(room.id)
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

        room = await GtRoom.aio_get_or_none(
            (GtRoom.id == room_id) & (GtRoom.team_id == team_id)
        )
        assertUtil.assertNotNull(room, error_message=f"Room ID '{room_id}' not found", error_code="room_not_found")

        await gtRoomMemberManager.upsert_room_members(room.id, request.members)
        await teamService.hot_reload_team(team_name)

        self.return_json({"status": "updated", "room_name": room.name})

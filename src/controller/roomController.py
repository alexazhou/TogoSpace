# 标准库
import json
from typing import List

# 第三方包
from pydantic import BaseModel

# 内部包
from controller.baseController import BaseHandler
from dal.db import gtTeamManager, gtRoomManager, gtRoomMemberManager, gtRoomMessageManager
from model.coreModel.gtCoreWebModel import RoomInfo, MessageInfo, RoomMessagesResponse
from service import roomService as chat_room, teamService
from constants import SpecialAgent, RoomType
from util import assertUtil


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
        rooms: List[chat_room.ChatRoom] = chat_room.get_all_rooms()
        data = []
        for r in rooms:
            # 通过 room_id 字符串从数据库查询获取 db_id
            room_config = await gtRoomManager.get_room_config(r.key)
            db_id = room_config.id if room_config else 0
            data.append(RoomInfo(
                db_id=db_id,
                room_id=r.key,
                room_name=r.name,
                team_name=r.team_name,
                room_type=r.room_type.value,
                state=r.state.value,
                members=r.agents
            ).model_dump(mode="json"))
        self.return_json({"rooms": data})


class RoomMessagesHandler(BaseHandler):
    async def get(self, db_id: str) -> None:
        # 通过数据库 ID 查询房间配置
        room_config = await gtRoomManager.get_room_by_db_id(int(db_id))
        assertUtil.assertNotNull(room_config, error_message=f"Room with id {db_id} not found", error_code="room_not_found")

        # 使用 room_key (name@team_name) 获取内存中的 ChatRoom
        room_key = f"{room_config.name}@{room_config.team_id}"
        room: chat_room.ChatRoom = chat_room.get_room(room_key)
        messages = [
            MessageInfo(sender=m.sender_name, content=m.content, time=m.send_time)
            for m in room.messages
        ]
        resp = RoomMessagesResponse(
            room_id=room_key, room_name=room.name, team_name=room.team_name, messages=messages
        )
        self.return_json(resp)

    async def post(self, db_id: str) -> None:
        # 通过数据库 ID 查询房间配置
        room_config = await gtRoomManager.get_room_by_db_id(int(db_id))
        assertUtil.assertNotNull(room_config, error_message=f"Room with id {db_id} not found", error_code="room_not_found")

        # 使用 room_key (name@team_name) 获取内存中的 ChatRoom
        room_key = f"{room_config.name}@{room_config.team_id}"
        room: chat_room.ChatRoom = chat_room.get_room(room_key)
        body = json.loads(self.request.body)
        content = body.get("content")
        assertUtil.assertNotNull(content, error_message="content is required", error_code="invalid_request")

        await room.add_message(SpecialAgent.OPERATOR, content)
        room.finish_turn(SpecialAgent.OPERATOR)
        self.return_json({"status": "ok"})


# Team Room Management Handlers
class TeamRoomsHandler(BaseHandler):
    """GET /teams/{team_id}/rooms.json - 获取 Team 下的所有 Room"""

    async def get(self, team_id: str) -> None:
        team = await gtTeamManager.get_team_by_id(int(team_id))
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")

        rooms = await gtRoomManager.get_rooms_by_team(team.name)
        self.return_json({"rooms": rooms})


class TeamRoomCreateHandler(BaseHandler):
    """POST /teams/{team_id}/rooms.json - 在 Team 下创建 Room"""

    async def post(self, team_id: str) -> None:
        request = self.parse_request(CreateRoomRequest)

        team = await gtTeamManager.get_team_by_id(int(team_id))
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")
        team_name = team.name

        # 检查房间是否已存在
        existing_rooms = await gtRoomManager.get_rooms_by_team(team_name)
        existing = next((r for r in existing_rooms if r.name == request.name), None)
        assertUtil.assertEqual(existing, None, error_message=f"Room '{request.name}' already exists", error_code="room_exists")

        # 构建房间配置
        room_config = {
            "name": request.name,
            "type": RoomType.value_of(request.type).value if RoomType.value_of(request.type) else RoomType.GROUP.value,
            "initial_topic": request.initial_topic,
            "max_turns": request.max_turns,
        }

        # upsert_rooms 会先删除该 team 下所有房间再重新插入，这在只添加一个房间时可能不太合适
        # 但目前 gtRoomManager 实现如此，暂且遵循。
        new_rooms_configs = []
        for r in existing_rooms:
            new_rooms_configs.append({
                "name": r.name,
                "type": r.type.value,
                "initial_topic": r.initial_topic,
                "max_turns": r.max_turns,
            })
        new_rooms_configs.append(room_config)

        await gtRoomManager.upsert_rooms(team_name, new_rooms_configs)
        await teamService.hot_reload_team(team_name)

        self.return_json({"status": "created", "room_name": request.name})


class TeamRoomDetailHandler(BaseHandler):
    """GET /teams/{team_id}/rooms/{room_id}.json - 获取指定 Room 详情"""

    async def get(self, team_id: str, room_db_id: str) -> None:
        team = await gtTeamManager.get_team_by_id(int(team_id))
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")

        room = await gtRoomManager.get_room_by_db_id(int(room_db_id))
        assertUtil.assertNotNull(room, error_message=f"Room ID '{room_db_id}' not found", error_code="room_not_found")

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

    async def post(self, team_id: str, room_db_id: str) -> None:
        request = self.parse_request(UpdateRoomRequest)

        team = await gtTeamManager.get_team_by_id(int(team_id))
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")
        team_name = team.name

        room = await gtRoomManager.get_room_by_db_id(int(room_db_id))
        assertUtil.assertNotNull(room, error_message=f"Room ID '{room_db_id}' not found", error_code="room_not_found")
        room_name = room.name

        room_type = RoomType.value_of(request.type) or RoomType.GROUP
        initial_topic = request.initial_topic if request.initial_topic is not None else room.initial_topic
        max_turns = request.max_turns if request.max_turns is not None else room.max_turns

        existing_rooms = await gtRoomManager.get_rooms_by_team(team_name)
        all_rooms = []
        for r in existing_rooms:
            if r.id == int(room_db_id):
                all_rooms.append({
                    "name": room_name,
                    "type": room_type.name,
                    "initial_topic": initial_topic,
                    "max_turns": max_turns,
                })
            else:
                all_rooms.append({
                    "name": r.name,
                    "type": r.type.name,
                    "initial_topic": r.initial_topic,
                    "max_turns": r.max_turns,
                })

        await gtRoomManager.upsert_rooms(team_name, all_rooms)
        await teamService.hot_reload_team(team_name)

        self.return_json({"status": "updated", "room_name": room_name})


class TeamRoomDeleteHandler(BaseHandler):
    """POST /teams/{team_id}/rooms/{room_id}/delete.json - 删除 Room"""

    async def post(self, team_id: str, room_db_id: str) -> None:
        team = await gtTeamManager.get_team_by_id(int(team_id))
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")
        team_name = team.name

        room = await gtRoomManager.get_room_by_db_id(int(room_db_id))
        assertUtil.assertNotNull(room, error_message=f"Room ID '{room_db_id}' not found", error_code="room_not_found")
        room_name = room.name
        room_db_id_key = room.id

        existing_rooms = await gtRoomManager.get_rooms_by_team(team_name)
        remaining_rooms = [r for r in existing_rooms if r.id != int(room_db_id)]

        await gtRoomManager.upsert_rooms(team_name, [
            {
                "name": r.name,
                "type": r.type.name,
                "initial_topic": r.initial_topic,
                "max_turns": r.max_turns,
            }
            for r in remaining_rooms
        ])

        await gtRoomMemberManager.delete_members_by_room(room_db_id_key)
        await teamService.hot_reload_team(team_name)

        self.return_json({"status": "deleted", "room_name": room_name})


class TeamRoomMembersHandler(BaseHandler):
    """GET /teams/{team_id}/rooms/{room_id}/members.json - 获取 Room 成员"""

    async def get(self, team_id: str, room_db_id: str) -> None:
        team = await gtTeamManager.get_team_by_id(int(team_id))
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")

        room = await gtRoomManager.get_room_by_db_id(int(room_db_id))
        assertUtil.assertNotNull(room, error_message=f"Room ID '{room_db_id}' not found", error_code="room_not_found")

        members = await gtRoomMemberManager.get_members_by_room(room.id)
        self.return_json({"members": members})


class TeamRoomMembersModifyHandler(BaseHandler):
    """POST /teams/{team_id}/rooms/{room_id}/members/modify.json - 更新 Room 成员"""

    async def post(self, team_id: str, room_db_id: str) -> None:
        request = self.parse_request(UpdateMembersRequest)

        team = await gtTeamManager.get_team_by_id(int(team_id))
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")
        team_name = team.name

        room = await gtRoomManager.get_room_by_db_id(int(room_db_id))
        assertUtil.assertNotNull(room, error_message=f"Room ID '{room_db_id}' not found", error_code="room_not_found")

        await gtRoomMemberManager.upsert_room_members(room.id, request.members)
        await teamService.hot_reload_team(team_name)

        self.return_json({"status": "updated", "room_name": room.name})
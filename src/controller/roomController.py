# 标准库
import json
from typing import List

# 第三方包
from pydantic import BaseModel

# 内部包
from controller.baseController import BaseHandler
from dal.db import gtTeamManager, gtRoomManager, gtRoomMemberManager
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
        self.return_json({"rooms": rooms})


class RoomMessagesHandler(BaseHandler):
    async def get(self, room_id: str) -> None:
        room: chat_room.ChatRoom = chat_room.get_room(room_id)
        messages = [
            MessageInfo(sender=m.sender_name, content=m.content, time=m.send_time)
            for m in room.messages
        ]
        resp = RoomMessagesResponse(
            room_id=room.key, room_name=room.name, team_name=room.team_name, messages=messages
        )
        self.return_json(resp)

    async def post(self, room_id: str) -> None:
        room: chat_room.ChatRoom = chat_room.get_room(room_id)
        body = json.loads(self.request.body)
        content = body.get("content")
        assertUtil.assertNotNull(content, error_message="content is required", error_code="invalid_request")

        await room.add_message(SpecialAgent.OPERATOR, content)
        room.finish_turn(SpecialAgent.OPERATOR)
        self.return_json({"status": "ok"})


# Team Room Management Handlers
class TeamRoomsHandler(BaseHandler):
    """GET /teams/{name}/rooms.json - 获取 Team 下的所有 Room"""

    async def get(self, name: str) -> None:
        exists = await gtTeamManager.team_exists(name)
        assertUtil.assertTrue(exists, error_message=f"Team '{name}' not found", error_code="team_not_found")

        rooms = await gtRoomManager.get_rooms_by_team(name)
        self.return_json({"rooms": rooms})


class TeamRoomCreateHandler(BaseHandler):
    """POST /teams/{name}/rooms.json - 在 Team 下创建 Room"""

    async def post(self, name: str) -> None:
        request = self.parse_request(CreateRoomRequest)

        exists = await gtTeamManager.team_exists(name)
        assertUtil.assertTrue(exists, error_message=f"Team '{name}' not found", error_code="team_not_found")

        # 检查房间是否已存在
        existing_rooms = await gtRoomManager.get_rooms_by_team(name)
        existing = next((r for r in existing_rooms if r.name == request.name), None)
        assertUtil.assertEqual(existing, None, error_message=f"Room '{request.name}' already exists", error_code="room_exists")

        # 构建房间配置
        room_config = {
            "name": request.name,
            "type": RoomType.value_of(request.type).value if RoomType.value_of(request.type) else RoomType.GROUP.value,
            "initial_topic": request.initial_topic,
            "max_turns": request.max_turns,
        }

        await gtRoomManager.upsert_rooms(name, [room_config])
        await teamService.hot_reload_team(name)

        self.return_json({"status": "created", "room_name": request.name})


class TeamRoomDetailHandler(BaseHandler):
    """GET /teams/{name}/rooms/{room_name}.json - 获取指定 Room 详情"""

    async def get(self, name: str, room_name: str) -> None:
        exists = await gtTeamManager.team_exists(name)
        assertUtil.assertTrue(exists, error_message=f"Team '{name}' not found", error_code="team_not_found")

        room = await gtRoomManager.get_room_config(f"{room_name}@{name}")
        assertUtil.assertNotNull(room, error_message=f"Room '{room_name}' not found", error_code="room_not_found")

        members = await gtRoomMemberManager.get_members_by_room(room.room_id)
        data = {
            "name": room.name,
            "type": room.type.name,
            "initial_topic": room.initial_topic,
            "max_turns": room.max_turns,
            "members": members,
        }
        self.return_json(data)


class TeamRoomModifyHandler(BaseHandler):
    """POST /teams/{name}/rooms/{room_name}/modify.json - 更新 Room"""

    async def post(self, name: str, room_name: str) -> None:
        request = self.parse_request(UpdateRoomRequest)

        exists = await gtTeamManager.team_exists(name)
        assertUtil.assertTrue(exists, error_message=f"Team '{name}' not found", error_code="team_not_found")

        existing_rooms = await gtRoomManager.get_rooms_by_team(name)
        existing = next((r for r in existing_rooms if r.name == room_name), None)
        assertUtil.assertNotNull(existing, error_message=f"Room '{room_name}' not found", error_code="room_not_found")

        room_type = RoomType.value_of(request.type) or RoomType.GROUP
        initial_topic = request.initial_topic if request.initial_topic is not None else existing.initial_topic
        max_turns = request.max_turns if request.max_turns is not None else existing.max_turns

        all_rooms = []
        for r in existing_rooms:
            if r.name == room_name:
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

        await gtRoomManager.upsert_rooms(name, all_rooms)
        await teamService.hot_reload_team(name)

        self.return_json({"status": "updated", "room_name": room_name})


class TeamRoomDeleteHandler(BaseHandler):
    """POST /teams/{name}/rooms/{room_name}/delete.json - 删除 Room"""

    async def post(self, name: str, room_name: str) -> None:
        exists = await gtTeamManager.team_exists(name)
        assertUtil.assertTrue(exists, error_message=f"Team '{name}' not found", error_code="team_not_found")

        existing_rooms = await gtRoomManager.get_rooms_by_team(name)
        existing = next((r for r in existing_rooms if r.name == room_name), None)
        assertUtil.assertNotNull(existing, error_message=f"Room '{room_name}' not found", error_code="room_not_found")

        room_id = f"{room_name}@{name}"
        remaining_rooms = [r for r in existing_rooms if r.name != room_name]

        await gtRoomManager.upsert_rooms(name, [
            {
                "name": r.name,
                "type": r.type.name,
                "initial_topic": r.initial_topic,
                "max_turns": r.max_turns,
            }
            for r in remaining_rooms
        ])

        await gtRoomMemberManager.delete_members_by_room(room_id)
        await teamService.hot_reload_team(name)

        self.return_json({"status": "deleted", "room_name": room_name})


class TeamRoomMembersHandler(BaseHandler):
    """GET /teams/{name}/rooms/{room_name}/members.json - 获取 Room 成员"""

    async def get(self, name: str, room_name: str) -> None:
        exists = await gtTeamManager.team_exists(name)
        assertUtil.assertTrue(exists, error_message=f"Team '{name}' not found", error_code="team_not_found")

        room_id = f"{room_name}@{name}"
        members = await gtRoomMemberManager.get_members_by_room(room_id)
        self.return_json({"members": members})


class TeamRoomMembersModifyHandler(BaseHandler):
    """POST /teams/{name}/rooms/{room_name}/members/modify.json - 更新 Room 成员"""

    async def post(self, name: str, room_name: str) -> None:
        request = self.parse_request(UpdateMembersRequest)

        exists = await gtTeamManager.team_exists(name)
        assertUtil.assertTrue(exists, error_message=f"Team '{name}' not found", error_code="team_not_found")

        existing_rooms = await gtRoomManager.get_rooms_by_team(name)
        existing = next((r for r in existing_rooms if r.name == room_name), None)
        assertUtil.assertNotNull(existing, error_message=f"Room '{room_name}' not found", error_code="room_not_found")

        room_id = f"{room_name}@{name}"
        await gtRoomMemberManager.upsert_room_members(room_id, request.members)
        await teamService.hot_reload_team(name)

        self.return_json({"status": "updated", "room_name": room_name})
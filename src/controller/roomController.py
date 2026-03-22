# 标准库
import json
from typing import List

# 第三方包
from pydantic import BaseModel

# 内部包
from controller.baseController import BaseHandler
from dal.db import gtTeamManager, gtRoomManager, gtRoomMemberManager
from model.coreModel.gtCoreWebModel import RoomInfo, MessageInfo, RoomMessagesResponse
from model.dbModel.gtRoom import GtRoom
from service import roomService as chat_room, teamService
from constants import SpecialAgent, RoomType, enum_to_str


# Room Config Request Models
class CreateRoomRequest(BaseModel):
    name: str
    type: str
    initial_topic: str | None = None
    max_turns: int = 100


class UpdateRoomRequest(BaseModel):
    type: str | None = None
    initial_topic: str | None = None
    max_turns: int | None = None


class UpdateMembersRequest(BaseModel):
    members: List[str]


class RoomListHandler(BaseHandler):
    async def get(self) -> None:
        rooms: List[chat_room.ChatRoom] = chat_room.get_all_rooms()
        data = [
            RoomInfo(
                room_id=r.key,
                room_name=r.name,
                team_name=r.team_name,
                room_type=r.room_type.value,
                state=r.state.value,
                members=r.agents,
            ).model_dump(mode="json")
            for r in rooms
        ]
        self.return_json({"rooms": data})


class RoomMessagesHandler(BaseHandler):
    async def get(self, room_id: str) -> None:
        try:
            room: chat_room.ChatRoom = chat_room.get_room(room_id)
        except RuntimeError:
            self.set_status(404)
            self.return_json({"error": f"room '{room_id}' not found"})
            return

        messages = [
            MessageInfo(sender=m.sender_name, content=m.content, time=m.send_time)
            for m in room.messages
        ]
        resp = RoomMessagesResponse(
            room_id=room.key, room_name=room.name, team_name=room.team_name, messages=messages
        )
        self.return_json(resp)

    async def post(self, room_id: str) -> None:
        try:
            room: chat_room.ChatRoom = chat_room.get_room(room_id)
        except RuntimeError:
            self.set_status(404)
            self.return_json({"error": f"room '{room_id}' not found"})
            return

        try:
            body = json.loads(self.request.body)
            content = body.get("content")
            if not content:
                self.set_status(400)
                self.return_json({"error": "content is required"})
                return
        except (json.JSONDecodeError, AttributeError):
            self.set_status(400)
            self.return_json({"error": "invalid json body"})
            return

        await room.add_message(SpecialAgent.OPERATOR, content)
        room.finish_turn(SpecialAgent.OPERATOR)
        self.return_json({"status": "ok"})


# Team Room Management Handlers
class TeamRoomsHandler(BaseHandler):
    """GET /teams/{name}/rooms.json - 获取 Team 下的所有 Room"""

    async def get(self, name: str) -> None:
        if not await gtTeamManager.team_exists(name):
            self.set_status(404)
            self.return_json({"error": f"Team '{name}' not found"})
            return

        rooms = await gtRoomManager.get_rooms_by_team(name)
        self.return_json({"rooms": rooms})


class TeamRoomCreateHandler(BaseHandler):
    """POST /teams/{name}/rooms.json - 在 Team 下创建 Room"""

    async def post(self, name: str) -> None:
        try:
            body = json.loads(self.request.body)
            request = CreateRoomRequest(**body)
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            self.set_status(400)
            self.return_json({"error": f"invalid request: {e}"})
            return

        if not await gtTeamManager.team_exists(name):
            self.set_status(404)
            self.return_json({"error": f"Team '{name}' not found"})
            return

        try:
            # 检查房间是否已存在
            existing_rooms = await gtRoomManager.get_rooms_by_team(name)
            existing = next((r for r in existing_rooms if r.name == request.name), None)
            if existing is not None:
                self.set_status(409)
                self.return_json({"error": f"Room '{request.name}' already exists"})
                return

            # 构建房间配置
            room_config = {
                "name": request.name,
                "type": RoomType(request.type),
                "initial_topic": request.initial_topic,
                "max_turns": request.max_turns,
            }

            await gtRoomManager.upsert_rooms(name, [room_config])
            await teamService.hot_reload_team(name)

            self.return_json({"status": "created", "room_name": request.name})
        except ValueError as e:
            self.set_status(400)
            self.return_json({"error": str(e)})
        except Exception as e:
            self.set_status(500)
            self.return_json({"error": str(e)})


class TeamRoomDetailHandler(BaseHandler):
    """GET /teams/{name}/rooms/{room_name}.json - 获取指定 Room 详情"""

    async def get(self, name: str, room_name: str) -> None:
        if not await gtTeamManager.team_exists(name):
            self.set_status(404)
            self.return_json({"error": f"Team '{name}' not found"})
            return

        room = await gtRoomManager.get_room_config(f"{room_name}@{name}")
        if room is None:
            self.set_status(404)
            self.return_json({"error": f"Room '{room_name}' not found"})
            return

        members = await gtRoomMemberManager.get_members_by_room(room.room_key)
        data = {
            "name": room.name,
            "type": enum_to_str(room.type),
            "initial_topic": room.initial_topic,
            "max_turns": room.max_turns,
            "members": members,
        }
        self.return_json(data)


class TeamRoomModifyHandler(BaseHandler):
    """PUT /teams/{name}/rooms/{room_name}/modify.json - 更新 Room"""

    async def put(self, name: str, room_name: str) -> None:
        try:
            body = json.loads(self.request.body)
            request = UpdateRoomRequest(**body)
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            self.set_status(400)
            self.return_json({"error": f"invalid request: {e}"})
            return

        if not await gtTeamManager.team_exists(name):
            self.set_status(404)
            self.return_json({"error": f"Team '{name}' not found"})
            return

        try:
            existing_rooms = await gtRoomManager.get_rooms_by_team(name)
            existing = next((r for r in existing_rooms if r.name == room_name), None)
            if existing is None:
                self.set_status(404)
                self.return_json({"error": f"Room '{room_name}' not found"})
                return

            room_type = RoomType(request.type) if request.type else existing.type
            initial_topic = request.initial_topic if request.initial_topic is not None else existing.initial_topic
            max_turns = request.max_turns if request.max_turns is not None else existing.max_turns

            all_rooms = []
            for r in existing_rooms:
                if r.name == room_name:
                    all_rooms.append({
                        "name": room_name,
                        "type": room_type,
                        "initial_topic": initial_topic,
                        "max_turns": max_turns,
                    })
                else:
                    all_rooms.append({
                        "name": r.name,
                        "type": enum_to_str(r.type),
                        "initial_topic": r.initial_topic,
                        "max_turns": r.max_turns,
                    })

            await gtRoomManager.upsert_rooms(name, all_rooms)
            await teamService.hot_reload_team(name)

            self.return_json({"status": "updated", "room_name": room_name})
        except Exception as e:
            self.set_status(500)
            self.return_json({"error": str(e)})


class TeamRoomDeleteHandler(BaseHandler):
    """DELETE /teams/{name}/rooms/{room_name}/delete.json - 删除 Room"""

    async def delete(self, name: str, room_name: str) -> None:
        if not await gtTeamManager.team_exists(name):
            self.set_status(404)
            self.return_json({"error": f"Team '{name}' not found"})
            return

        try:
            existing_rooms = await gtRoomManager.get_rooms_by_team(name)
            existing = next((r for r in existing_rooms if r.name == room_name), None)
            if existing is None:
                self.set_status(404)
                self.return_json({"error": f"Room '{room_name}' not found"})
                return

            room_key = f"{room_name}@{name}"
            remaining_rooms = [r for r in existing_rooms if r.name != room_name]

            await gtRoomManager.upsert_rooms(name, [
                {
                    "name": r.name,
                    "type": enum_to_str(r.type),
                    "initial_topic": r.initial_topic,
                    "max_turns": r.max_turns,
                }
                for r in remaining_rooms
            ])

            await gtRoomMemberManager.delete_members_by_room(room_key)
            await teamService.hot_reload_team(name)

            self.return_json({"status": "deleted", "room_name": room_name})
        except Exception as e:
            self.set_status(500)
            self.return_json({"error": str(e)})


class RoomMembersHandler(BaseHandler):
    """GET /teams/{name}/rooms/{room_name}/members.json - 获取 Room 成员"""

    async def get(self, name: str, room_name: str) -> None:
        if not await gtTeamManager.team_exists(name):
            self.set_status(404)
            self.return_json({"error": f"Team '{name}' not found"})
            return

        room_key = f"{room_name}@{name}"
        members = await gtRoomMemberManager.get_members_by_room(room_key)
        self.return_json({"members": members})

    """PUT /teams/{name}/rooms/{room_name}/members.json - 更新 Room 成员"""

    async def put(self, name: str, room_name: str) -> None:
        try:
            body = json.loads(self.request.body)
            request = UpdateMembersRequest(**body)
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            self.set_status(400)
            self.return_json({"error": f"invalid request: {e}"})
            return

        if not await gtTeamManager.team_exists(name):
            self.set_status(404)
            self.return_json({"error": f"Team '{name}' not found"})
            return

        try:
            existing_rooms = await gtRoomManager.get_rooms_by_team(name)
            existing = next((r for r in existing_rooms if r.name == room_name), None)
            if existing is None:
                self.set_status(404)
                self.return_json({"error": f"Room '{room_name}' not found"})
                return

            room_key = f"{room_name}@{name}"
            await gtRoomMemberManager.upsert_room_members(room_key, request.members)
            await teamService.hot_reload_team(name)

            self.return_json({"status": "updated", "room_name": room_name})
        except Exception as e:
            self.set_status(500)
            self.return_json({"error": str(e)})
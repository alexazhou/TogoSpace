import json
from typing import List
import service.room_service as room_service
from service.room_service import ChatRoom
from model.web_model import RoomInfo, MessageInfo, RoomMessagesResponse
from controller.base_controller import BaseHandler
from constants import SpecialAgent


class RoomListHandler(BaseHandler):
    async def get(self):
        rooms: List[ChatRoom] = room_service.get_all_rooms()
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
    async def get(self, room_id: str):
        try:
            room: ChatRoom = room_service.get_room(room_id)
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

    async def post(self, room_id: str):
        try:
            room: ChatRoom = room_service.get_room(room_id)
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

        room.add_message(SpecialAgent.OPERATOR, content)
        self.return_json({"status": "ok"})

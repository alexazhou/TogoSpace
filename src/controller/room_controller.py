from typing import List
import service.room_service as room_service
from service.room_service import ChatRoom
from model.web_model import RoomInfo, MessageInfo, RoomMessagesResponse
from controller.base_controller import BaseHandler


class RoomListHandler(BaseHandler):
    async def get(self):
        rooms: List[ChatRoom] = room_service.get_all_rooms()
        data = [
            RoomInfo(
                room_id=r.name,
                room_name=r.name,
                state=r.state.value,
                members=r.member_names,
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
            room_id=room.name, room_name=room.name, messages=messages
        )
        self.return_json(resp)

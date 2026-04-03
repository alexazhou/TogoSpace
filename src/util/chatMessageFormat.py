import re
from dataclasses import dataclass

_ROOM_MESSAGE_PATTERN = re.compile(
    r"^【房间《(?P<room_name>[^》]+)》】【(?P<sender_label>[^】]+)】： (?P<content>[\s\S]*)$"
)
@dataclass(frozen=True)
class ParsedRoomMessage:
    room_name: str
    sender_name: str
    content: str
def parse_room_message(content: str) -> ParsedRoomMessage | None:
    """解析由 format_room_message 生成的文本，非标准格式返回 None。"""
    match = _ROOM_MESSAGE_PATTERN.match(content)
    if match is None:
        return None
    return ParsedRoomMessage(
        room_name=match.group("room_name"),
        sender_name="SYSTEM" if match.group("sender_label") == "系统提醒" else match.group("sender_label"),
        content=match.group("content"),
    )

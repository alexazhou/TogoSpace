import re
from dataclasses import dataclass

_ROOM_MESSAGE_PATTERN = re.compile(
    r"^【房间《(?P<room_name>[^》]+)》】【发言人《(?P<sender_name>[^》]+)》】(?P<content>[\s\S]*)$"
)
_TURN_CONTEXT_SUFFIX = "你现在可以调用工具行动。如果你已完成发言和所有工具调用，请务必调用 finish_chat_turn 结束本轮行动。"


@dataclass(frozen=True)
class ParsedRoomMessage:
    room_name: str
    sender_name: str
    content: str


def format_room_message(room_name: str, sender_name: str, content: str) -> str:
    """统一格式化房间消息，确保房间名与发言人名有明确符号包裹。"""
    return f"【房间《{room_name}》】【发言人《{sender_name}》】\n{content}"


def parse_room_message(content: str) -> ParsedRoomMessage | None:
    """解析由 format_room_message 生成的文本，非标准格式返回 None。"""
    match = _ROOM_MESSAGE_PATTERN.match(content)
    if match is None:
        return None
    return ParsedRoomMessage(
        room_name=match.group("room_name"),
        sender_name=match.group("sender_name"),
        content=match.group("content"),
    )


def build_turn_context_prompt(room_name: str, message_blocks: list[str]) -> str:
    """构造轮到发言时的上下文说明，统一用于所有 driver。"""
    context = "\n\n".join(message_blocks) if message_blocks else "(无新消息)"
    return (
        f"{room_name} 房间轮到你发言，房间消息如下：\n\n"
        f"{context}\n\n"
        f"{_TURN_CONTEXT_SUFFIX}"
    )

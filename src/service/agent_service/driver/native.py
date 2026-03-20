import json
import logging
from typing import Optional

from model.chat_context import ChatContext
from util.llm_api_util import LlmApiMessage, OpenaiLLMApiRole
from service import func_tool_service, room_service
from service.room_service import ChatRoom

from .base import AgentDriver

logger = logging.getLogger(__name__)


class NativeAgentDriver(AgentDriver):
    async def run_turn(self, room_key: str, max_function_calls: int = 5) -> None:
        room: ChatRoom = room_service.get_room(room_key)
        self.host.current_room = room

        try:
            await self.host.sync_room_messages(room)
            await self._run_turn_native(room, max_function_calls)
        finally:
            self.host.current_room = None

    async def _run_turn_native(self, room: ChatRoom, max_function_calls: int = 5) -> None:
        self.host._turn_ctx = ChatContext(
            agent_name=self.host.name,
            team_name=self.host.team_name,
            chat_room=room,
            get_room=room_service.get_room,
        )
        turn_history_start: int = len(self.host._history)

        def _get_last_tool_call() -> Optional[dict]:
            recent_history = self.host._history[turn_history_start:]
            for message in reversed(recent_history):
                if message.role != OpenaiLLMApiRole.ASSISTANT:
                    continue
                tool_calls = message.tool_calls or []
                if not tool_calls:
                    continue
                call = tool_calls[-1]
                function = call.function if isinstance(call.function, dict) else {}
                return {
                    "name": function.get("name"),
                    "args": function.get("arguments", ""),
                }
            return None

        def is_turn_done() -> bool:
            called = _get_last_tool_call()
            if called is None:
                return False
            if called.get("name") == "skip_chat_msg":
                return True
            if called.get("name") == "send_chat_msg":
                try:
                    target = json.loads(called.get("args", "")).get("room_name")
                    return target == room.name or target == room.key
                except Exception:
                    return False
            return False

        hint = f"你必须调用 send_chat_msg 向当前房间 {room.name} 发送消息或 skip_chat_msg 跳过发言，不能直接输出文字。"
        max_retries = 3
        for _ in range(max_retries):
            await self.host.chat(
                tools=func_tool_service.get_tools(),
                done_check=is_turn_done,
                max_function_calls=max_function_calls,
            )

            if is_turn_done():
                break

            await self.host.append_history_message(
                LlmApiMessage.text(OpenaiLLMApiRole.USER, hint)
            )

import json
import logging
from typing import Optional

from model.chat_context import ChatContext
from util.llm_api_util import LlmApiMessage, OpenaiLLMApiRole, Tool
from service import func_tool_service, room_service
from service.room_service import ChatRoom

from .base import AgentDriver

logger = logging.getLogger(__name__)


class NativeAgentDriver(AgentDriver):
    async def run_chat_turn(self, room: ChatRoom, synced_count: int, max_function_calls: int = 5) -> None:
        self.host._turn_ctx = ChatContext(
            agent_name=self.host.name,
            team_name=self.host.team_name,
            chat_room=room,
            get_room=room_service.get_room,
        )
        turn_history_start: int = len(self.host._history)

        hint = f"你必须调用 send_chat_msg 向当前房间 {room.name} 发送消息或 skip_chat_msg 跳过发言，不能直接输出文字。"
        max_retries = 3
        for _ in range(max_retries):
            turn_done = await self._run_until_reply(
                room=room,
                turn_history_start=turn_history_start,
                tools=func_tool_service.get_tools(),
                max_function_calls=max_function_calls,
            )

            if turn_done:
                break

            await self.host.append_history_message(LlmApiMessage.text(OpenaiLLMApiRole.USER, hint))

    async def _run_until_reply(
        self,
        room: ChatRoom,
        turn_history_start: int,
        tools: Optional[list[Tool]] = None,
        max_function_calls: int = 5,
    ) -> bool:
        # native driver 在一次尝试里持续驱动模型和工具调用，直到本轮回复完成或达到上限。
        for _ in range(max_function_calls):
            assistant_message = await self.host._infer(tools)

            if not assistant_message.tool_calls:
                return False

            logger.info(f"检测到工具调用: agent={self.host.key}, count={len(assistant_message.tool_calls)}")
            for tool_call in assistant_message.tool_calls:
                name = tool_call.function.get("name", "")
                args = tool_call.function.get("arguments", "")
                await self.host._execute_tool(tool_call.id, name, args)

            called: dict[str, str] | None = self.host.get_last_assistant_tool_call(turn_history_start)
            assert called is not None, f"[{self.host.key}] tool_calls 已返回，但未能从 history 中找到最后一次 assistant tool call"

            if called.get("name") == "skip_chat_msg":
                return True

            if called.get("name") == "send_chat_msg":
                try:
                    target = json.loads(called.get("args", "")).get("room_name")

                    if target == room.name or target == room.key:
                        return True
                except Exception:
                    pass

        logger.warning(f"达到最大函数调用次数: agent={self.host.key}, max={max_function_calls}")

        return False

import json
import logging
from typing import Optional

from util import llmApiUtil
from service import funcToolService
from service.roomService import ChatRoom

from .base import AgentDriver

logger = logging.getLogger(__name__)


class NativeAgentDriver(AgentDriver):

    async def run_chat_turn(self, room: ChatRoom, synced_count: int, max_function_calls: int = 5) -> None:
        hint = f"你必须通过调用工具来行动。如果你不需要发言，或者已经完成了所有行动，请务必调用 finish_chat_turn 结束本轮（即跳过）。"
        max_retries = 3
        for _ in range(max_retries):
            turn_done = await self._run_until_reply(
                room=room,
                tools=funcToolService.get_tools(),
                max_function_calls=max_function_calls,
            )

            if turn_done:
                break

            await self.host.append_history_message(llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiLLMApiRole.USER, hint))

    async def _run_until_reply(
        self,
        room: ChatRoom,
        tools: Optional[list[llmApiUtil.OpenAITool]] = None,
        max_function_calls: int = 5,
    ) -> bool:

        # native driver 在一次尝试里持续驱动模型和工具调用，直到本轮回复完成或达到上限。
        for _ in range(max_function_calls):
            assistant_message: llmApiUtil.OpenAIMessage = await self.host._infer(tools)

            tool_calls = assistant_message.tool_calls
            if not tool_calls:
                return False

            logger.info(f"检测到工具调用: agent={self.host.key}, count={len(tool_calls)}")
            await self.host._execute_tool()

            # 检查最后一个 tool_call 判断轮次是否完成
            last_call: llmApiUtil.OpenAIToolCall = tool_calls[-1]
            function = last_call.function if isinstance(last_call.function, dict) else {}
            name = function.get("name")

            if name == "finish_chat_turn":
                return True

        logger.warning(f"达到最大函数调用次数: agent={self.host.key}, max={max_function_calls}")

        return False

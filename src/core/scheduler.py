import logging
from typing import List

from core.agent import Agent
from core.chat_room import ChatRoom
from tools.function_loader import build_tools, execute_function

logger = logging.getLogger(__name__)


class Scheduler:
    """多 Agent 调度器：按轮次让 Agent 依次发言"""

    def __init__(self, agents: List[Agent], chat_room: ChatRoom, max_turns: int, api_client):
        self.agents = agents
        self.chat_room = chat_room
        self.max_turns = max_turns
        self.api_client = api_client
        self.tools = build_tools()

    async def run(self) -> None:
        """运行调度循环"""
        agent_names = [a.name for a in self.agents]
        logger.info(f"参与者: {agent_names}")
        logger.info(f"开始 {self.max_turns} 轮对话...")

        for turn in range(1, self.max_turns + 1):
            current_agent = self.agents[(turn - 1) % len(self.agents)]
            logger.info(f"\n--- 第 {turn} 轮 ({current_agent.name}) ---")

            context_messages = self.chat_room.get_context_messages()

            try:
                agent_context = {
                    "chat_room": self.chat_room,
                    "agent_name": current_agent.name
                }
                final_response, _ = await current_agent.generate_with_function_calling(
                    api_client=self.api_client,
                    context_messages=context_messages,
                    tools=self.tools,
                    function_executor=lambda name, args: execute_function(
                        name, args, context=agent_context
                    ),
                    max_function_calls=1
                )
                if final_response:
                    self.chat_room.add_message(current_agent.name, final_response)
                    logger.info(f"{current_agent.name}: {final_response}")
            except Exception as e:
                logger.error(f"{current_agent.name} 生成回复失败: {e}")
                return

        logger.info(f"\n{self.chat_room.format_log()}")

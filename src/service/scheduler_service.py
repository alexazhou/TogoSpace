import logging

import service.agent_service as agent_service
import service.chat_room_service as chat_room
import service.agent_tool_service as agent_tools

logger = logging.getLogger(__name__)


class Scheduler:
    """多 Agent 调度器：按轮次让 Agent 依次发言"""

    def __init__(
        self,
        room_name: str,
        max_turns: int,
        max_function_calls: int = 5,
    ):
        self.room_name = room_name
        self.max_turns = max_turns
        self.tools = agent_tools.get_tools()
        self.max_function_calls = max_function_calls

    async def run(self) -> None:
        """运行调度循环"""
        agents = agent_service.get_agents()
        agent_names = [a.name for a in agents]
        logger.info(f"参与者: {agent_names}")
        logger.info(f"开始 {self.max_turns} 轮对话...")

        for turn in range(1, self.max_turns + 1):
            current_agent = agents[(turn - 1) % len(agents)]
            logger.info(f"\n--- 第 {turn} 轮 ({current_agent.name}) ---")

            context_messages = chat_room.get_context_messages(self.room_name)

            try:
                agent_context = {
                    "chat_room": chat_room.get_room(self.room_name),
                    "agent_name": current_agent.name
                }
                final_response, _ = await current_agent.generate_with_function_calling(
                    context_messages=context_messages,
                    tools=self.tools,
                    function_executor=lambda name, args: agent_tools.execute_function(
                        name, args, context=agent_context
                    ),
                    max_function_calls=self.max_function_calls
                )
                if final_response:
                    chat_room.add_message(self.room_name, current_agent.name, final_response)
                    logger.info(f"{current_agent.name}: {final_response}")
            except Exception as e:
                logger.error(f"{current_agent.name} 生成回复失败: {e}")
                return

        logger.info(f"\n{chat_room.format_log(self.room_name)}")

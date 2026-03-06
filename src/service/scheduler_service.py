import asyncio
import logging

import service.agent_service as agent_service
import service.chat_room_service as chat_room
import service.agent_tool_service as agent_tools

logger = logging.getLogger(__name__)

_rooms_config: list = []
_max_function_calls: int = 5
_tools: list = []


def init(rooms_config: list, max_function_calls: int = 5) -> None:
    """初始化调度器，须在 run() 前调用一次。"""
    global _rooms_config, _max_function_calls, _tools
    _rooms_config = rooms_config
    _max_function_calls = max_function_calls
    _tools = agent_tools.get_tools()


def stop() -> None:
    """重置调度器状态。"""
    global _rooms_config, _max_function_calls, _tools
    _rooms_config = []
    _max_function_calls = 5
    _tools = []


async def run() -> None:
    """并发运行所有房间的调度循环。"""
    await asyncio.gather(*[_run_room(r["name"], r["max_turns"]) for r in _rooms_config])


async def _run_room(room_name: str, max_turns: int) -> None:
    """运行单个房间的调度循环。"""
    agents = agent_service.get_agents(room_name)
    agent_names = [a.name for a in agents]
    logger.info(f"[{room_name}] 参与者: {agent_names}")
    logger.info(f"[{room_name}] 开始 {max_turns} 轮对话...")

    for turn in range(1, max_turns + 1):
        current_agent = agents[(turn - 1) % len(agents)]
        logger.info(f"[{room_name}]\n--- 第 {turn} 轮 ({current_agent.name}) ---")

        context_messages = chat_room.get_context_messages(room_name)

        try:
            agent_context = {
                "chat_room": chat_room.get_room(room_name),
                "agent_name": current_agent.name
            }
            final_response, _ = await current_agent.generate_with_function_calling(
                context_messages=context_messages,
                tools=_tools,
                function_executor=lambda name, args: agent_tools.execute_function(
                    name, args, context=agent_context
                ),
                max_function_calls=_max_function_calls
            )
            if final_response:
                chat_room.add_message(room_name, current_agent.name, final_response)
                logger.info(f"[{room_name}] {current_agent.name}: {final_response}")
        except Exception as e:
            logger.error(f"[{room_name}] {current_agent.name} 生成回复失败: {e}")
            return

    logger.info(f"[{room_name}]\n{chat_room.format_log(room_name)}")

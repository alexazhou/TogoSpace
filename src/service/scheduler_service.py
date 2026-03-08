import asyncio
import logging

import service.agent_service as agent_service
import service.chat_room_service as chat_room
import service.agent_tool_service as agent_tools
from util.llm_api_util import LlmApiMessage
from model.chat_context import ChatContext

logger = logging.getLogger(__name__)

_rooms_config: list = []
_max_function_calls: int = 5


def init(rooms_config: list, max_function_calls: int = 5) -> None:
    """初始化调度器，须在 run() 前调用一次。"""
    global _rooms_config, _max_function_calls
    _rooms_config = rooms_config
    _max_function_calls = max_function_calls


def stop() -> None:
    """重置调度器状态。"""
    global _rooms_config, _max_function_calls
    _rooms_config = []
    _max_function_calls = 5


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

        room = chat_room.get_room(room_name)
        history_messages, latest_message = (
            lambda msgs: (msgs[:-1], msgs[-1]) if msgs else ([], None)
        )([LlmApiMessage.model_validate(m) for m in room.get_context_messages()])

        if latest_message is None:
            logger.warning(f"[{room_name}] 消息为空，跳过本轮")
            continue

        try:
            agent_context = ChatContext(
                agent_name=current_agent.name,
                chat_room=room,
                get_room=chat_room.get_room,
            )
            current_agent.set_messages(history_messages)
            response = await current_agent.chat(
                input_message=latest_message,
                tools=agent_tools.get_tools(),
                function_executor=lambda name, args, _ctx=agent_context: agent_tools.execute_function(
                    name, args, context=_ctx
                ),
                max_function_calls=_max_function_calls,
            )
            if response.content:
                logger.info(f"[{room_name}] {current_agent.name} (思考): {response.content}")
        except Exception as e:
            logger.error(f"[{room_name}] {current_agent.name} 生成回复失败: {e}")
            return

    logger.info(f"[{room_name}]\n{chat_room.get_room(room_name).format_log()}")

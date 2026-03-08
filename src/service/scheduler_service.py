import asyncio
import logging
from typing import Dict, List, Optional

import service.agent_service as agent_service
import service.chat_room_service as chat_room
import service.func_tool_service as agent_tools
from constants import TurnStatus, TurnCheckResult
from service.agent_service import Agent
from service.chat_room_service import ChatRoom
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
    agents: List[Agent] = agent_service.get_agents(room_name)
    agent_names: List[str] = [a.name for a in agents]
    logger.info(f"[{room_name}] 参与者: {agent_names}")
    logger.info(f"[{room_name}] 开始 {max_turns} 轮对话...")

    for turn in range(1, max_turns + 1):
        current_agent: Agent = agents[(turn - 1) % len(agents)]
        logger.info(f"[{room_name}]\n--- 第 {turn} 轮 ({current_agent.name}) ---")

        room: ChatRoom = chat_room.get_room(room_name)
        current_agent.sync_room(room)

        try:
            agent_context: ChatContext = ChatContext(
                agent_name=current_agent.name,
                chat_room=room,
                get_room=chat_room.get_room,
            )
            last_called: Dict[str, Optional[str]] = {"name": None}

            def executor(name: str, args: str, _ctx: ChatContext = agent_context) -> str:
                last_called["name"] = name
                return agent_tools.run_tool_call(name, args, context=_ctx)

            def turn_checker(msg: LlmApiMessage) -> TurnCheckResult:
                if last_called["name"] == "send_chat_msg":
                    return TurnCheckResult(TurnStatus.SUCCESS)
                if not msg.tool_calls:
                    return TurnCheckResult(TurnStatus.ERROR, "你必须调用 send_chat_msg 工具发送消息，不能直接输出文字。")
                return TurnCheckResult(TurnStatus.CONTINUE)

            response: LlmApiMessage = await current_agent.chat(
                tools=agent_tools.get_tools(),
                function_executor=executor,
                turn_checker=turn_checker,
                max_function_calls=_max_function_calls,
            )

            if response.content:
                logger.info(f"[{room_name}] {current_agent.name} (思考): {response.content}")
        except Exception as e:
            logger.error(f"[{room_name}] {current_agent.name} 生成回复失败: {e}")
            return

    logger.info(f"[{room_name}]\n{chat_room.get_room(room_name).format_log()}")

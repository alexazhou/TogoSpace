import asyncio
import logging
from typing import Dict, List, Optional

import service.agent_service as agent_service
import service.chat_room_service as chat_room
import service.func_tool_service as agent_tools
from constants import TurnStatus, TurnCheckResult
from service.agent_service import Agent
from service.chat_room_service import ChatRoom
from model.agent_event import RoomMessageEvent
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
    """以 Agent 为中心的事件驱动调度：为每个房间初始化轮次，并发运行所有 Agent 的事件循环。"""
    # 计算每个 Agent 期望收到的事件总数（每个房间的 max_turns 次）
    expected: Dict[str, int] = {}
    for r in _rooms_config:
        agents = agent_service.get_agents(r["name"])
        for a in agents:
            expected[a.name] = expected.get(a.name, 0) + r["max_turns"]

    # 为每个房间初始化轮次（推送首个事件）
    for r in _rooms_config:
        room = chat_room.get_room(r["name"])
        agents = agent_service.get_agents(r["name"])
        agent_names = [a.name for a in agents]
        logger.info(f"[{r['name']}] 参与者: {agent_names}，最大轮次: {r['max_turns']}")
        room.setup_turns(agents, r["max_turns"])

    # 并发启动所有 Agent 的事件循环
    all_agents = agent_service.get_all_agents()
    await asyncio.gather(*[_run_agent(a, expected.get(a.name, 0)) for a in all_agents])

    # 打印所有房间聊天记录
    for r in _rooms_config:
        logger.info(f"\n{chat_room.get_room(r['name']).format_log()}")


async def _run_agent(agent: Agent, count: int) -> None:
    """消费 Agent 事件队列，处理 count 个事件后退出。"""
    for _ in range(count):
        event: RoomMessageEvent = await agent.wait_event_queue.get()
        await _handle_event(agent, event)
        agent.wait_event_queue.task_done()


async def _handle_event(agent: Agent, event: RoomMessageEvent) -> None:
    """处理单个房间消息事件：同步房间消息并驱动 Agent 发言。"""
    room: ChatRoom = chat_room.get_room(event.room_name)
    agent.sync_room(room)

    try:
        agent_context = ChatContext(
            agent_name=agent.name,
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

        response: LlmApiMessage = await agent.chat(
            tools=agent_tools.get_tools(),
            function_executor=executor,
            turn_checker=turn_checker,
            max_function_calls=_max_function_calls,
        )

        if response.content:
            logger.info(f"[{event.room_name}] {agent.name} (思考): {response.content}")
    except Exception as e:
        logger.error(f"[{event.room_name}] {agent.name} 生成回复失败: {e}")

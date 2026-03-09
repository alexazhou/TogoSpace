import asyncio
from typing import Callable, Dict, List, Optional
import logging

import service.llm_service as llm_service
from constants import TurnStatus, TurnCheckResult
from model.chat_model import AgentDialogContext, ChatMessage
from service.chat_room_service import ChatRoom
from util.llm_api_util import OpenaiLLMApiRole, LlmApiMessage, Tool
from util.config_util import load_prompt

logger = logging.getLogger(__name__)

_agents: Dict[str, "Agent"] = {}
_room_agents: Dict[str, List[str]] = {}  # room_name → agent names (ordered)


class Agent:
    def __init__(self, name: str, system_prompt: str, model: str):
        self.name = name
        self.system_prompt = system_prompt
        self.model = model
        self._history: List[LlmApiMessage] = []
        self.wait_event_queue: asyncio.Queue = asyncio.Queue()

    def sync_room(self, room: ChatRoom) -> None:
        """将聊天室中未读的新消息追加到内部历史，跳过自己发送的消息。"""
        new_msgs: List[ChatMessage] = room.get_unread_messages(self.name)
        logger.info(f"[{self.name}] 同步 {room.name} 房间：{len(new_msgs)} 条新消息")
        for msg in new_msgs:
            #if msg.sender_name == self.name:
            #    continue
            if msg.sender_name == "system":
                self._history.append(LlmApiMessage(role=OpenaiLLMApiRole.USER, content=f"{room.name} 房间系统消息: {msg.content}"))
            else:
                self._history.append(LlmApiMessage.text(OpenaiLLMApiRole.USER, f"{msg.sender_name} 在 {room.name} 房间发言: {msg.content}"))
        self._history.append(LlmApiMessage.text(OpenaiLLMApiRole.USER, f"系统提示：当前进入到 {room.name} 房间，请在 {room.name} 房间发言"))

    async def _infer(self, tools: Optional[List[Tool]]) -> LlmApiMessage:
        """基于当前 _history 发起一次 LLM 调用，返回 assistant 消息。"""
        assert self._history and self._history[-1].role in (OpenaiLLMApiRole.USER, OpenaiLLMApiRole.TOOL, OpenaiLLMApiRole.SYSTEM), \
            f"[{self.name}] _infer 前最后一条消息不能是 assistant，当前为: {self._history[-1].role if self._history else 'empty'}"
        ctx = AgentDialogContext(
            system_prompt=self.system_prompt,
            messages=self._history,
            tools=tools or None,
        )
        response = await llm_service.infer(self.model, ctx)
        return response.choices[0].message

    async def chat(
        self,
        tools: Optional[List[Tool]] = None,
        function_executor: Optional[Callable[[str, str], str]] = None,
        turn_checker: Optional[Callable[[LlmApiMessage], TurnCheckResult]] = None,
        max_function_calls: int = 5,
    ) -> LlmApiMessage:
        """基于当前 _history 自动执行 tool calls 循环。
        turn_checker: 每次 LLM 响应或 tool 执行后调用，根据返回的 TurnCheckResult 决定继续、终止或注入提示重试。
        """
        for _ in range(max_function_calls):
            assistant_message: LlmApiMessage = await self._infer(tools)
            self._history.append(assistant_message)

            if not assistant_message.tool_calls:
                if turn_checker:
                    check: TurnCheckResult = turn_checker(assistant_message)
                    if check.status == TurnStatus.ERROR:
                        logger.warning(f"[{self.name}] checker 返回 ERROR，注入提示重试")
                        self._history.append(LlmApiMessage.text(OpenaiLLMApiRole.USER, check.error_hint))
                        continue
                return assistant_message

            logger.info(f"[{self.name}] 检测到 {len(assistant_message.tool_calls)} 个工具调用")

            recheck: bool = False
            for tool_call in assistant_message.tool_calls:
                function_name: str = tool_call.function.get("name")
                function_args: str = tool_call.function.get("arguments", "")

                assert function_executor is not None, "function_executor 未配置"
                result: str = function_executor(function_name, function_args)

                tool_result_msg: LlmApiMessage = LlmApiMessage.tool_result(tool_call.id, result)
                self._history.append(tool_result_msg)

                if turn_checker:
                    check = turn_checker(tool_result_msg)
                    if check.status == TurnStatus.SUCCESS:
                        return LlmApiMessage.text(OpenaiLLMApiRole.ASSISTANT, "")
                    if check.status == TurnStatus.ERROR:
                        logger.warning(f"[{self.name}] checker 返回 ERROR，注入提示重试")
                        self._history.append(LlmApiMessage.text(OpenaiLLMApiRole.USER, check.error_hint))
                        recheck = True
                        break

            if recheck:
                continue

        logger.warning(f"[{self.name}] 达到最大函数调用次数 {max_function_calls}")
        return assistant_message


def init(agents_config: list, rooms_config: list) -> None:
    """为每个 Agent 创建单一实例，按房间配置建立成员映射。"""
    global _agents, _room_agents
    _agents = {}
    _room_agents = {}

    agent_defs: Dict[str, dict] = {cfg["name"]: cfg for cfg in agents_config}

    # 收集每个 Agent 跨所有房间的其他参与者
    agent_peers: Dict[str, set] = {name: set() for name in agent_defs}
    for room in rooms_config:
        member_names: List[str] = room["agents"]
        for name in member_names:
            agent_peers[name].update(n for n in member_names if n != name)

    for name, cfg in agent_defs.items():
        prompt: str = load_prompt(cfg["prompt_file"])
        prompt = prompt.replace("{participants}", "、".join(sorted(agent_peers[name])))
        _agents[name] = Agent(name=name, system_prompt=prompt, model=cfg["model"])

    for room in rooms_config:
        room_name: str = room["name"]
        _room_agents[room_name] = room["agents"]
        logger.info(f"[{room_name}] 参与者: {room['agents']}")


def get_all_agents() -> List[Agent]:
    """返回所有唯一 Agent 实例列表。"""
    return list(_agents.values())


def get_agents(room_name: str) -> List[Agent]:
    """返回指定房间的 Agent 列表（按配置顺序）。"""
    return [_agents[n] for n in _room_agents.get(room_name, []) if n in _agents]


def get_all_rooms(agent_name: str) -> List[str]:
    """返回指定 Agent 参与的所有房间名列表。"""
    return [room for room, names in _room_agents.items() if agent_name in names]


def close() -> None:
    """清空 Agent 字典，程序退出前调用。"""
    global _agents, _room_agents
    _agents = {}
    _room_agents = {}

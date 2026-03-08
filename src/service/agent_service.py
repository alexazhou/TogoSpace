from typing import Callable, Dict, List, Optional
import logging

import service.llm_service as llm_service
from model.chat_model import AgentDialogContext
from util.llm_api_util import OpenaiLLMApiRole, LlmApiMessage, Tool
from util.config_util import load_prompt

logger = logging.getLogger(__name__)

_agents_by_room: Dict[str, List["Agent"]] = {}


class Agent:
    def __init__(self, name: str, system_prompt: str, model: str):
        self.name = name
        self.system_prompt = system_prompt
        self.model = model
        self._history: List[LlmApiMessage] = []

    def set_messages(self, messages: List[LlmApiMessage]) -> None:
        """设置内部历史消息。"""
        self._history = list(messages)

    def get_messages(self) -> List[LlmApiMessage]:
        """返回当前内部历史消息。"""
        return list(self._history)

    async def _infer(self, tools: List[Tool]) -> LlmApiMessage:
        """基于当前 _history 发起一次 LLM 调用，返回 assistant 消息。"""
        assert self._history and self._history[-1].role in (OpenaiLLMApiRole.USER, OpenaiLLMApiRole.TOOL), \
            f"[{self.name}] _infer 前最后一条消息必须是 user 或 tool，当前为: {self._history[-1].role if self._history else 'empty'}"
        ctx = AgentDialogContext(
            system_prompt=self.system_prompt,
            messages=self._history,
            tools=tools or None,
        )
        response = await llm_service.infer(self.model, ctx)
        return response.choices[0].message

    async def call_once(
        self,
        input_message: LlmApiMessage,
        tools: Optional[List[Tool]] = None,
    ) -> LlmApiMessage:
        """将 input_message 追加到历史后发起一轮 LLM 调用，返回原始 assistant 消息（不处理 tool_calls）。"""
        self._history.append(input_message)
        return await self._infer(tools)

    async def chat(
        self,
        input_message: LlmApiMessage,
        tools: Optional[List[Tool]] = None,
        function_executor: Optional[Callable[[str, str], str]] = None,
        should_stop: Optional[Callable[[LlmApiMessage], bool]] = None,
        max_function_calls: int = 5,
    ) -> LlmApiMessage:
        """将 input_message 追加到历史后自动执行 tool calls 循环，直到返回文本输出。
        should_stop: 每次 tool 执行后调用，接收最后一条消息，返回 True 时立即终止循环。
        """
        self._history.append(input_message)

        for _ in range(max_function_calls):
            assistant_message: LlmApiMessage = await self._infer(tools)
            self._history.append(assistant_message)

            if not assistant_message.tool_calls:
                return assistant_message

            logger.info(f"[{self.name}] 检测到 {len(assistant_message.tool_calls)} 个工具调用")

            stopped: bool = False
            for tool_call in assistant_message.tool_calls:
                function_name: str = tool_call.function.get("name")
                function_args: str = tool_call.function.get("arguments", "")

                assert function_executor is not None, "function_executor 未配置"
                result: str = function_executor(function_name, function_args)

                self._history.append(LlmApiMessage.tool_result(tool_call.id, result))

                if should_stop and should_stop(self._history[-1]):
                    stopped = True
                    break

            if stopped:
                return LlmApiMessage.text(OpenaiLLMApiRole.ASSISTANT, "")

        logger.warning(f"[{self.name}] 达到最大函数调用次数 {max_function_calls}")
        return assistant_message


def init(agents_config: list, rooms_config: list) -> None:
    """根据配置列表为每个房间独立创建 Agent 实例。"""
    global _agents_by_room
    _agents_by_room = {}

    agent_defs: Dict[str, dict] = {cfg["name"]: cfg for cfg in agents_config}

    for room in rooms_config:
        room_name: str = room["name"]
        member_names: List[str] = room["agents"]
        room_agents: List[Agent] = []
        for name in member_names:
            cfg: dict = agent_defs[name]
            other_names: List[str] = [n for n in member_names if n != name]
            prompt: str = load_prompt(cfg["prompt_file"])
            prompt = prompt.replace("{participants}", "、".join(other_names))
            prompt = prompt.replace("{room_name}", room_name)
            room_agents.append(Agent(name=name, system_prompt=prompt, model=cfg["model"]))
        _agents_by_room[room_name] = room_agents
        logger.info(f"[{room_name}] 已创建 {len(room_agents)} 个 Agent: {member_names}")


def get_agents(room_name: str) -> List[Agent]:
    """返回指定房间已初始化的 Agent 列表，房间不存在时返回空列表。"""
    return _agents_by_room.get(room_name, [])


def close() -> None:
    """清空 Agent 字典，程序退出前调用。"""
    global _agents_by_room
    _agents_by_room = {}

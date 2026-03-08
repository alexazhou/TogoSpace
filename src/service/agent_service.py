from typing import Dict, List, Optional
import logging
import json

import service.llm_api_service as api_client
from model.llm_api_model import LlmApiMessage, Tool
from util.config_util import load_prompt

logger = logging.getLogger(__name__)

_agents_by_room: Dict[str, List["Agent"]] = {}


class Agent:
    def __init__(self, name: str, system_prompt: str, model: str, tools: List[Tool] = None):
        self.name = name
        self.system_prompt = system_prompt
        self.model = model
        self.tools = tools or []

    async def chat(
        self,
        messages: List[LlmApiMessage],
        function_executor: callable = None,
        max_function_calls: int = 5,
    ) -> LlmApiMessage:
        """输入上下文消息列表，经 function calling 循环后返回最终的 assistant LlmApiMessage。"""
        history: List[LlmApiMessage] = [
            LlmApiMessage.text("system", self.system_prompt),
            *messages,
        ]

        for _ in range(max_function_calls):
            response = await api_client.send_request(
                model=self.model,
                messages=history,
                tools=self.tools or None,
            )

            assistant_message = response.choices[0].message
            history.append(assistant_message)

            if not assistant_message.tool_calls:
                return assistant_message

            logger.info(f"[{self.name}] 检测到 {len(assistant_message.tool_calls)} 个工具调用")

            sent_msg = False
            for tool_call in assistant_message.tool_calls:
                function_name = tool_call.function.get("name")
                function_args = tool_call.function.get("arguments", {})

                if isinstance(function_args, str):
                    try:
                        function_args = json.loads(function_args)
                    except json.JSONDecodeError:
                        function_args = {}

                logger.info(f"[{self.name}] 调用函数: {function_name}, 参数: {function_args}")

                if function_executor:
                    try:
                        result = function_executor(function_name, function_args)
                        logger.info(f"[{self.name}] 函数执行结果: {result}")
                    except Exception as e:
                        logger.error(f"[{self.name}] 函数执行失败: {e}")
                        result = f"函数执行失败: {str(e)}"
                else:
                    result = "函数执行器未配置"

                history.append(LlmApiMessage.tool_result(tool_call.id, result))

                if function_name == "send_chat_msg":
                    sent_msg = True
                    break

            if sent_msg:
                return LlmApiMessage.text("assistant", "")

        logger.warning(f"[{self.name}] 达到最大函数调用次数 {max_function_calls}")
        return assistant_message


def init(agents_config: list, rooms_config: list, tools: List[Tool] = None) -> None:
    """根据配置列表为每个房间独立创建 Agent 实例。"""
    global _agents_by_room
    _agents_by_room = {}

    agent_defs = {cfg["name"]: cfg for cfg in agents_config}

    for room in rooms_config:
        room_name = room["name"]
        member_names = room["agents"]
        room_agents = []
        for name in member_names:
            cfg = agent_defs[name]
            other_names = [n for n in member_names if n != name]
            prompt = load_prompt(cfg["prompt_file"])
            prompt = prompt.replace("{participants}", "、".join(other_names))
            prompt = prompt.replace("{room_name}", room_name)
            room_agents.append(Agent(name=name, system_prompt=prompt, model=cfg["model"], tools=tools))
        _agents_by_room[room_name] = room_agents
        logger.info(f"[{room_name}] 已创建 {len(room_agents)} 个 Agent: {member_names}")


def get_agents(room_name: str) -> List[Agent]:
    """返回指定房间已初始化的 Agent 列表，房间不存在时返回空列表。"""
    return _agents_by_room.get(room_name, [])


def close() -> None:
    """清空 Agent 字典，程序退出前调用。"""
    global _agents_by_room
    _agents_by_room = {}

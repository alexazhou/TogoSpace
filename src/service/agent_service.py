from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Any
import logging
import json

import service.llm_api_service as api_client
from model.api_model import Message, Tool
from util.config_util import load_prompt

logger = logging.getLogger(__name__)

_agents_by_room: Dict[str, List["Agent"]] = {}


@dataclass
class Agent:
    name: str
    system_prompt: str
    model: str


def init(agents_config: list, rooms_config: list) -> None:
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


async def run(
    agent: Agent,
    context_messages: List[dict],
    tools: Optional[List[Tool]] = None,
    function_executor: callable = None,
    max_function_calls: int = 5,
) -> Tuple[str, List[dict]]:
    """支持 Function Calling 的响应生成循环。

    Returns:
        (最终回复内容, 工具调用信息列表)
    """
    messages: List[Message] = [
        Message.text("system", agent.system_prompt),
        *[Message.model_validate(m) for m in context_messages],
    ]
    tool_calls_info = []
    function_call_count = 0

    while function_call_count < max_function_calls:
        response = await api_client.send_request(
            model=agent.model,
            messages=[m.to_dict() for m in messages],
            tools=tools,
        )

        assistant_message = response.choices[0].message
        messages.append(assistant_message)

        if not assistant_message.tool_calls:
            return assistant_message.content or "", tool_calls_info

        logger.info(f"[{agent.name}] 检测到 {len(assistant_message.tool_calls)} 个工具调用")

        sent_msg = False
        for tool_call in assistant_message.tool_calls:
            function_name = tool_call.function.get("name")
            function_args = tool_call.function.get("arguments", {})
            tool_call_id = tool_call.id

            if isinstance(function_args, str):
                try:
                    function_args = json.loads(function_args)
                except json.JSONDecodeError:
                    function_args = {}

            logger.info(f"[{agent.name}] 调用函数: {function_name}, 参数: {function_args}")

            if function_executor:
                try:
                    result = function_executor(function_name, function_args)
                    logger.info(f"[{agent.name}] 函数执行结果: {result}")
                except Exception as e:
                    logger.error(f"[{agent.name}] 函数执行失败: {e}")
                    result = f"函数执行失败: {str(e)}"
            else:
                result = "函数执行器未配置"

            tool_calls_info.append({
                "function": function_name,
                "arguments": function_args,
                "result": result,
            })
            messages.append(Message.tool_result(tool_call_id, result))

            if function_name == "send_chat_msg":
                sent_msg = True
                break

        if sent_msg:
            return "", tool_calls_info

        function_call_count += 1

    logger.warning(f"[{agent.name}] 达到最大函数调用次数 {max_function_calls}")
    return assistant_message.content or "", tool_calls_info

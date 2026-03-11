import asyncio
import logging
from typing import Callable, Dict, List, Optional

from util.llm_api_util import OpenaiLLMApiRole, LlmApiMessage, Tool
from util.config_util import load_prompt
from model.chat_model import AgentDialogContext, ChatMessage
from model.chat_context import ChatContext
from service import llm_service, func_tool_service, room_service
from service.room_service import ChatRoom
from constants import TurnStatus, TurnCheckResult, RoomType, SpecialAgent

logger = logging.getLogger(__name__)

# 全局 Agent 实例池，key 为 agent_name
_agents: Dict[str, "Agent"] = {}


class Agent:
    """AI Agent 实体类，维护其性格、对话历史及任务队列"""

    def __init__(self, name: str, system_prompt: str, model: str):
        self.name: str = name  # Agent 名称
        self.system_prompt: str = system_prompt  # 系统提示词（定义性格和规则）
        self.model: str = model  # 使用的 LLM 模型名称
        
        self._history: List[LlmApiMessage] = []  # Agent 的私有对话历史（包含 Tool Call 详情）
        self.wait_task_queue: asyncio.Queue = asyncio.Queue()  # 待处理的房间任务队列

    @property
    def is_active(self) -> bool:
        """如果 Agent 正在运行任务，或者其任务队列中仍有待处理项，则视为活跃。"""
        from service import scheduler_service
        task = scheduler_service.get_running_task(self.name)
        if task and not task.done():
            return True
        return not self.wait_task_queue.empty()

    async def consume_task(self, max_function_calls: int) -> None:
        """持续消费队列中的任务，直到队列为空。"""
        while True:
            try:
                # 尝试以非阻塞方式获取任务（RoomMessageEvent）
                event = self.wait_task_queue.get_nowait()
            except asyncio.QueueEmpty:
                # 队列空了，退出循环
                break
                
            try:
                # 驱动 Agent 在指定房间执行一个轮次
                await self.run_turn(event.room_name, max_function_calls)
            except Exception as e:
                logger.error(f"Agent 处理任务失败: agent={self.name}, room={event.room_name}, error={e}")
            finally:
                self.wait_task_queue.task_done()

    async def run_turn(self, room_name: str, max_function_calls: int = 5) -> None:
        """同步房间消息，驱动 Agent 完成一轮发言（含 tool call 循环）。"""
        room: ChatRoom = room_service.get_room(room_name)
        self.sync_room(room)

        agent_context = ChatContext(
            agent_name=self.name,
            chat_room=room,
            get_room=room_service.get_room,
        )
        last_called: dict = {"name": None}

        def executor(name: str, args: str, _ctx: ChatContext = agent_context) -> str:
            last_called["name"] = name
            return func_tool_service.run_tool_call(name, args, context=_ctx)

        def turn_checker(msg: LlmApiMessage) -> TurnCheckResult:
            if last_called["name"] in ("send_chat_msg", "skip_chat_msg"):
                return TurnCheckResult(TurnStatus.SUCCESS)
            if not msg.tool_calls:
                return TurnCheckResult(TurnStatus.ERROR, "你必须调用 send_chat_msg 发送消息或 skip_chat_msg 跳过发言，不能直接输出文字。")
            return TurnCheckResult(TurnStatus.CONTINUE)

        response: LlmApiMessage = await self.chat(
            tools=func_tool_service.get_tools(),
            function_executor=executor,
            turn_checker=turn_checker,
            max_function_calls=max_function_calls,
        )
        if response.content:
            logger.info(f"Agent 思考内容: agent={self.name}, room={room_name}, content={response.content}")

    def sync_room(self, room: ChatRoom) -> None:
        """将聊天室中未读的新消息追加到内部历史，跳过自己发送的消息。"""
        new_msgs: List[ChatMessage] = room.get_unread_messages(self.name)
        logger.info(f"同步房间消息: agent={self.name}, room={room.name}, count={len(new_msgs)}")
        for msg in new_msgs:
            if msg.sender_name == "system":
                self._history.append(LlmApiMessage(role=OpenaiLLMApiRole.USER, content=f"{room.name} 房间系统消息: {msg.content}"))
            else:
                self._history.append(LlmApiMessage.text(OpenaiLLMApiRole.USER, f"{msg.sender_name} 在 {room.name} 房间发言: {msg.content}"))

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
        """基于当前 _history 自动执行 tool calls 循环。"""
        for _ in range(max_function_calls):
            assistant_message: LlmApiMessage = await self._infer(tools)
            self._history.append(assistant_message)

            if not assistant_message.tool_calls:
                if turn_checker:
                    check: TurnCheckResult = turn_checker(assistant_message)
                    if check.status == TurnStatus.ERROR:
                        logger.warning(f"checker 返回 ERROR，注入提示重试: agent={self.name}")
                        self._history.append(LlmApiMessage.text(OpenaiLLMApiRole.USER, check.error_hint))
                        continue
                return assistant_message

            logger.info(f"检测到工具调用: agent={self.name}, count={len(assistant_message.tool_calls)}")

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
                        logger.warning(f"checker 返回 ERROR，注入提示重试: agent={self.name}")
                        self._history.append(LlmApiMessage.text(OpenaiLLMApiRole.USER, check.error_hint))
                        recheck = True
                        break

            if recheck:
                continue

        logger.warning(f"达到最大函数调用次数: agent={self.name}, max={max_function_calls}")
        return assistant_message


def init(agents_config: list, rooms_config: list) -> None:
    """为每个 Agent 创建单一实例。"""
    global _agents
    _agents = {}

    agent_defs: Dict[str, dict] = {cfg["name"]: cfg for cfg in agents_config}

    # 收集每个 Agent 跨所有房间的其他参与者
    agent_peers: Dict[str, set] = {name: set() for name in agent_defs}
    for room in rooms_config:
        names: List[str] = list(room["agents"])
        for name in names:
            if name in agent_peers:
                agent_peers[name].update(n for n in names if n != name)

    for name, cfg in agent_defs.items():
        prompt: str = load_prompt(cfg["prompt_file"])
        participants = sorted(list(agent_peers[name]))
        prompt = prompt.replace("{participants}", "、".join(participants))
        _agents[name] = Agent(name=name, system_prompt=prompt, model=cfg["model"])
        logger.info(f"创建 Agent: name={name}, model={cfg['model']}")


def get_agent(name: str) -> Agent:
    """返回指定名称的 Agent 实例。"""
    return _agents[name]


def is_agent_active(name: str) -> bool:
    """判断指定名称的 Agent 是否活跃。"""
    agent = _agents.get(name)
    return agent.is_active if agent else False


def get_all_agents() -> List[Agent]:
    """返回所有唯一 Agent 实例列表。"""
    return list(_agents.values())


def get_agents(room_name: str) -> List[Agent]:
    """返回指定房间的 Agent 实例列表（按配置顺序，排除非 AI 角色）。"""
    return [_agents[n] for n in room_service.get_member_names(room_name) if n in _agents]


def get_all_rooms(agent_name: str) -> List[str]:
    """返回指定 Agent 参与的所有房间名列表。"""
    return room_service.get_rooms_for_agent(agent_name)


def close() -> None:
    """清空 Agent 字典，程序退出前调用。"""
    global _agents
    _agents = {}

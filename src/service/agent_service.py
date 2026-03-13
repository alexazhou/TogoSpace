import asyncio
import json
import logging
from typing import Callable, Dict, List, Optional

from util.llm_api_util import OpenaiLLMApiRole, LlmApiMessage, Tool
from util.config_util import load_prompt
from model.chat_model import AgentDialogContext, ChatMessage
from model.chat_context import ChatContext
from service import llm_service, func_tool_service, room_service, message_bus
from service.room_service import ChatRoom
from constants import RoomType, SpecialAgent, MessageBusTopic

logger = logging.getLogger(__name__)

# Agent 定义（name → config dict），全局共享
_agent_defs: Dict[str, dict] = {}

# Agent 实例池，key 为 "agent_name@team_name"
_agents: Dict[str, "Agent"] = {}


def _make_agent_key(team_name: str, agent_name: str) -> str:
    return f"{agent_name}@{team_name}"


class Agent:
    """AI Agent 实体类，维护其性格、对话历史及任务队列"""

    def __init__(self, name: str, team_name: str, system_prompt: str, model: str):
        self.name: str = name  # Agent 名称
        self.team_name: str = team_name  # 所属 Team
        self.system_prompt: str = system_prompt  # 系统提示词（定义性格和规则）
        self.model: str = model  # 使用的 LLM 模型名称

        self._history: List[LlmApiMessage] = []  # Agent 的私有对话历史（包含 Tool Call 详情）
        self.wait_task_queue: asyncio.Queue = asyncio.Queue()  # 待处理的房间任务队列
        self._is_running: bool = False  # 标志当前是否正在处理任务协程
        self._last_published_status: Optional[str] = None  # 记录上一次发布的状态，用于幂等校验

    @property
    def key(self) -> str:
        return _make_agent_key(self.team_name, self.name)

    @property
    def is_active(self) -> bool:
        """如果 Agent 正在运行任务协程，或者其任务队列中仍有待处理项，则视为活跃。"""
        return self._is_running or not self.wait_task_queue.empty()

    def _publish_status(self) -> None:
        """检查并发布状态变更消息。"""
        current_status = "active" if self.is_active else "idle"
        if current_status != self._last_published_status:
            self._last_published_status = current_status
            message_bus.publish(
                MessageBusTopic.AGENT_STATUS_CHANGED,
                agent_name=self.name,
                team_name=self.team_name,
                status=current_status
            )

    async def consume_task(self, max_function_calls: int) -> None:
        """持续消费队列中的任务，直到队列为空。"""
        self._is_running = True
        self._publish_status()  # 启动时声明 active
        try:
            while True:
                try:
                    # 尝试以非阻塞方式获取任务（RoomMessageEvent）
                    event = self.wait_task_queue.get_nowait()
                except asyncio.QueueEmpty:
                    # 队列空了，退出循环
                    break

                try:
                    # 驱动 Agent 在指定房间执行一个轮次
                    await self.run_turn(event.room_key, max_function_calls)
                except Exception as e:
                    logger.error(f"Agent 处理任务失败: agent={self.key}, room={event.room_key}, error={e}")
                finally:
                    self.wait_task_queue.task_done()
        finally:
            self._is_running = False
            self._publish_status()  # 退出前声明 idle (如果队列为空)

    async def run_turn(self, room_key: str, max_function_calls: int = 5) -> None:
        """同步房间消息，驱动 Agent 完成一轮发言（含 tool call 循环）。"""
        room: ChatRoom = room_service.get_room(room_key)
        self.sync_room(room)

        ctx = ChatContext(
            agent_name=self.name,
            team_name=self.team_name,
            chat_room=room,
            get_room=room_service.get_room,
        )
        last_called: dict = {}

        def executor(name: str, args: str) -> str:
            last_called["name"] = name
            last_called["args"] = args
            return func_tool_service.run_tool_call(name, args, context=ctx)

        def is_turn_done() -> bool:
            called = last_called.get("name")
            if called == "skip_chat_msg":
                return True
            if called == "send_chat_msg":
                try:
                    target = json.loads(last_called["args"]).get("room_name")
                    return target == room.name or target == room.key
                except Exception:
                    return False
            return False

        hint = f"你必须调用 send_chat_msg 向当前房间 {room.name} 发送消息或 skip_chat_msg 跳过发言，不能直接输出文字。"
        max_retries = 3
        for _ in range(max_retries):
            response = await self.chat(
                tools=func_tool_service.get_tools(),
                function_executor=executor,
                done_check=is_turn_done,
                max_function_calls=max_function_calls,
            )
            if is_turn_done():
                break
            # LLM 未调用工具或调用了无关工具，注入提示后重试
            self._history.append(LlmApiMessage.text(OpenaiLLMApiRole.USER, hint))

    async def chat(
        self,
        tools: Optional[List[Tool]] = None,
        function_executor: Optional[Callable[[str, str], str]] = None,
        done_check: Optional[Callable[[], bool]] = None,
        max_function_calls: int = 5,
    ) -> LlmApiMessage:
        """基于当前 _history 自动执行对话和 tool calls 循环。

        停止条件（任一满足即返回）：
        - LLM 返回无 tool_calls（自然结束）
        - done_check() 返回 True（调用方判定完成）
        - 达到 max_function_calls 上限
        """
        assistant_message: Optional[LlmApiMessage] = None
        for _ in range(max_function_calls):
            assistant_message = await self._infer(tools)
            self._history.append(assistant_message)

            if not assistant_message.tool_calls:
                return assistant_message

            logger.info(f"检测到工具调用: agent={self.key}, count={len(assistant_message.tool_calls)}")
            for tool_call in assistant_message.tool_calls:
                name = tool_call.function.get("name")
                args = tool_call.function.get("arguments", "")
                result = function_executor(name, args)
                self._history.append(LlmApiMessage.tool_result(tool_call.id, result))

            if done_check and done_check():
                return assistant_message

        logger.warning(f"达到最大函数调用次数: agent={self.key}, max={max_function_calls}")
        return assistant_message

    def sync_room(self, room: ChatRoom) -> None:
        """将聊天室中未读的新消息追加到内部历史，跳过自己发送的消息。"""
        new_msgs: List[ChatMessage] = room.get_unread_messages(self.name)
        logger.info(f"同步房间消息: agent={self.key}, room={room.name}, count={len(new_msgs)}")
        for msg in new_msgs:
            if msg.sender_name == self.name:
                continue
            if msg.sender_name == "system":
                self._history.append(LlmApiMessage(role=OpenaiLLMApiRole.USER, content=f"{room.name} 房间系统消息: {msg.content}"))
            else:
                self._history.append(LlmApiMessage.text(OpenaiLLMApiRole.USER, f"{msg.sender_name} 在 {room.name} 房间发言: {msg.content}"))

    async def _infer(self, tools: Optional[List[Tool]]) -> LlmApiMessage:
        """基于当前 _history 发起一次 LLM 调用，返回 assistant 消息。"""
        assert self._history and self._history[-1].role in (OpenaiLLMApiRole.USER, OpenaiLLMApiRole.TOOL, OpenaiLLMApiRole.SYSTEM), \
            f"[{self.key}] _infer 前最后一条消息不能是 assistant，当前为: {self._history[-1].role if self._history else 'empty'}"
        ctx = AgentDialogContext(
            system_prompt=self.system_prompt,
            messages=self._history,
            tools=tools or None,
        )
        response = await llm_service.infer(self.model, ctx)
        return response.choices[0].message


def startup() -> None:
    """初始化 Agent 服务，清空所有状态。"""
    global _agent_defs, _agents
    _agent_defs = {}
    _agents = {}


def load_agent_config(agents_config: list) -> None:
    """加载 Agent 定义（prompt/model）到 _agent_defs 字典，不创建实例。"""
    global _agent_defs
    _agent_defs = {cfg["name"]: cfg for cfg in agents_config}
    logger.info(f"加载 Agent 定义: {list(_agent_defs.keys())}")


def create_team_agents(teams_config: list) -> None:
    """遍历所有 team，从 _agent_defs 读取定义，创建 agent@team 实例。"""
    base_prompt_tmpl = load_prompt("src/prompts/GroupChat.md")

    for team_config in teams_config:
        team_name = team_config["name"]

        agent_names_in_team: set = set()
        for group in team_config["groups"]:
            for name in group["members"]:
                if name != SpecialAgent.OPERATOR:
                    agent_names_in_team.add(name)

        for name in agent_names_in_team:
            if name not in _agent_defs:
                logger.warning(f"Agent 定义不存在: {name}，跳过创建")
                continue

            cfg = _agent_defs[name]
            if "system_prompt" in cfg:
                agent_specific_prompt = cfg["system_prompt"]
            else:
                agent_specific_prompt = load_prompt(cfg["prompt_file"])

            full_prompt = base_prompt_tmpl + "\n\n" + agent_specific_prompt
            key = _make_agent_key(team_name, name)
            _agents[key] = Agent(name=name, team_name=team_name, system_prompt=full_prompt, model=cfg["model"])
            logger.info(f"创建 Agent 实例: key={key}, model={cfg['model']}")


def get_agent(team_name: str, agent_name: str) -> Agent:
    """返回指定 agent@team 的 Agent 实例。"""
    key = _make_agent_key(team_name, agent_name)
    return _agents[key]



def get_all_agents() -> List[Agent]:
    """返回所有 Agent 实例列表。"""
    return list(_agents.values())


def get_agents(team_name: str, room_name: str) -> List[Agent]:
    """返回指定 team 和 room 中的 Agent 实例列表。"""
    members = room_service.get_member_names(team_name, room_name)
    return [_agents[_make_agent_key(team_name, n)] for n in members if _make_agent_key(team_name, n) in _agents]


def get_all_rooms(team_name: str, agent_name: str) -> List[str]:
    """返回指定 Agent 在指定 Team 中参与的所有房间 key 列表。"""
    return room_service.get_rooms_for_agent(team_name, agent_name)


def shutdown() -> None:
    """清空 Agent 字典，程序退出前调用。"""
    global _agents, _agent_defs
    _agents = {}
    _agent_defs = {}

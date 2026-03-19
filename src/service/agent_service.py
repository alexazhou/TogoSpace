import asyncio
import json
import logging
from typing import Any, Callable, Dict, List, Optional

from util.llm_api_util import OpenaiLLMApiRole, LlmApiMessage, LlmApiResponse, Tool
from util.config_util import load_prompt
from model.chat_model import AgentDialogContext, ChatMessage
from model.chat_context import ChatContext
from model.agent_event import RoomMessageEvent
from model.db_model.agent_history_message import AgentHistoryMessageRecord
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

    def __init__(self, name: str, team_name: str, system_prompt: str, model: str,
                 use_agent_sdk: bool = False, allowed_tools: list = None):
        self.name: str = name  # Agent 名称
        self.team_name: str = team_name  # 所属 Team
        self.system_prompt: str = system_prompt  # 系统提示词（定义性格和规则）
        self.model: str = model  # 使用的 LLM 模型名称
        self.use_agent_sdk: bool = use_agent_sdk  # 是否使用 Claude Agent SDK 驱动
        self.allowed_tools: list = allowed_tools if allowed_tools is not None else []  # SDK 允许使用的工具列表

        self._history: List[LlmApiMessage] = []  # Agent 的私有对话历史（包含 Tool Call 详情）
        self.wait_task_queue: asyncio.Queue = asyncio.Queue()  # 待处理的房间任务队列
        self._is_running: bool = False  # 标志当前是否正在处理任务协程
        self._last_published_status: Optional[str] = None  # 记录上一次发布的状态，用于幂等校验
        self._last_called: dict = {}  # 记录当前轮次最后一次工具调用的名称和参数
        self._turn_ctx: Optional[ChatContext] = None  # 当前轮次的上下文（run_turn 期间有效）
        self._sdk_client = None  # 持久化 SDK 会话客户端（由 init_sdk 赋值）

    @property
    def key(self) -> str:
        return _make_agent_key(self.team_name, self.name)

    @property
    def is_active(self) -> bool:
        """如果 Agent 正在运行任务协程，或者其任务队列中仍有待处理项，则视为活跃。"""
        return self._is_running or not self.wait_task_queue.empty()

    async def init_sdk(self) -> None:
        """初始化持久化 SDK 会话（仅对 use_agent_sdk=True 的 Agent 调用一次）。"""
        import os
        from claude_agent_sdk import (ClaudeSDKClient, ClaudeAgentOptions,
                                       tool, create_sdk_mcp_server)

        # 可变容器：工具闭包直接捕获，_run_turn_sdk 通过引用更新
        _room_slot = [None]
        _done_slot = [False]
        self._sdk_room_slot = _room_slot
        self._sdk_done_slot = _done_slot

        agent_name = self.name

        @tool("send_chat_msg", "向聊天室发送消息", {
            "type": "object",
            "properties": {
                "room_name": {"type": "string"},
                "msg": {"type": "string"},
            },
            "required": ["room_name", "msg"],
        })
        async def _send(args):
            room_name = args.get("room_name", "")
            msg = args.get("msg", "")
            logger.info(f"SDK MCP tool called: send_chat_msg, agent={self.key}, room={room_name}, msg_len={len(msg)}")
            return self._sdk_do_send(room_name, msg)

        @tool("skip_chat_msg", "跳过本轮发言", {
            "type": "object",
            "properties": {},
        })
        async def _skip(args):
            room: ChatRoom = _room_slot[0]
            logger.info(f"SDK MCP tool called: skip_chat_msg, agent={self.key}")
            ok = room.skip_turn(sender=agent_name)
            if not ok:
                current: Optional[str] = room.get_current_turn_agent()
                return {"content": [{"type": "text", "text": f"error: 现在不是你的发言轮次（当前应由 {current} 发言），请勿再调用任何工具。"}], "isError": True}
            _done_slot[0] = True
            return {"content": [{"type": "text", "text": "success: 已跳过本轮发言。"}]}

        server = create_sdk_mcp_server("chat-tools", tools=[_send, _skip])
        options = ClaudeAgentOptions(
            system_prompt=self.system_prompt,
            allowed_tools=self.allowed_tools,
            mcp_servers={"chat": server},
            permission_mode="bypassPermissions",
            max_turns=100,
        )

        # 移除 CLAUDECODE，防止 bundled CLI 检测到嵌套会话后拒绝启动
        os.environ.pop("CLAUDECODE", None)

        client = ClaudeSDKClient(options=options)
        await client.connect()
        self._sdk_client = client
        logger.info(f"SDK 持久会话初始化: agent={self.key}")

    def _sdk_do_send(self, room_name: str, msg: str) -> dict:
        """send_chat_msg MCP 工具的核心逻辑（从闭包提取，便于单元测试）。

        - 发到当前房间：写消息、标记本轮结束（_sdk_done_slot[0] = True）
        - 发到其他房间：写消息、不标记结束，返回提示要求继续回复当前房间
        """
        room_key = room_name if "@" in room_name else f"{room_name}@{self.team_name}"
        target_room: ChatRoom = room_service.get_room(room_key)
        if target_room is None:
            logger.warning(f"SDK send_chat_msg: 目标房间不存在 {room_key}，回落到当前房间")
            target_room = self._sdk_room_slot[0]
        target_room.add_message(self.name, msg)
        self._append_history_message(LlmApiMessage.text(OpenaiLLMApiRole.ASSISTANT, msg))
        current_room: ChatRoom = self._sdk_room_slot[0]
        if target_room is current_room:
            self._sdk_done_slot[0] = True
            return {"content": [{"type": "text", "text": "success: 消息已发送，本轮发言结束。"}]}
        else:
            return {"content": [{"type": "text", "text": f"success: 消息已发送到 {target_room.name}。你还需要调用 send_chat_msg 向当前房间 {current_room.name} 发言，或调用 skip_chat_msg 跳过。"}]}

    async def close(self) -> None:
        """关闭持久化 SDK 会话。"""
        if self._sdk_client is not None:
            try:
                await self._sdk_client.disconnect()
                logger.info(f"SDK 会话已关闭: agent={self.key}")
            except Exception as e:
                logger.error(f"SDK 会话关闭失败: agent={self.key}, error={e}", exc_info=True)
            finally:
                self._sdk_client = None

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
                    event: RoomMessageEvent = self.wait_task_queue.get_nowait()
                except asyncio.QueueEmpty:
                    # 队列空了，退出循环
                    break

                try:
                    # 驱动 Agent 在指定房间执行一个轮次
                    await self.run_turn(event.room_key, max_function_calls)
                except Exception as e:
                    logger.error(f"Agent 处理任务失败: agent={self.key}, room={event.room_key}, error={e}", exc_info=True)
                finally:
                    self.wait_task_queue.task_done()
        finally:
            self._is_running = False
            self._publish_status()  # 退出前声明 idle (如果队列为空)

    async def run_turn(self, room_key: str, max_function_calls: int = 5) -> None:
        """同步房间消息，驱动 Agent 完成一轮发言（含 tool call 循环）。"""
        if self.use_agent_sdk:
            return await self._run_turn_sdk(room_key)

        room: ChatRoom = room_service.get_room(room_key)
        self.sync_room(room)

        self._last_called = {}
        self._turn_ctx = ChatContext(
            agent_name=self.name,
            team_name=self.team_name,
            chat_room=room,
            get_room=room_service.get_room,
        )

        def is_turn_done() -> bool:
            called: Optional[str] = self._last_called.get("name")
            if called == "skip_chat_msg":
                return True
            if called == "send_chat_msg":
                try:
                    target: Optional[str] = json.loads(self._last_called["args"]).get("room_name")
                    return target == room.name or target == room.key
                except Exception:
                    return False
            return False

        hint = f"你必须调用 send_chat_msg 向当前房间 {room.name} 发送消息或 skip_chat_msg 跳过发言，不能直接输出文字。"
        max_retries = 3
        for _ in range(max_retries):
            await self.chat(
                tools=func_tool_service.get_tools(),
                done_check=is_turn_done,
                max_function_calls=max_function_calls,
            )
            if is_turn_done():
                break
            # LLM 未调用工具或调用了无关工具，注入提示后重试
            self._append_history_message(LlmApiMessage.text(OpenaiLLMApiRole.USER, hint))

    async def _run_turn_sdk(self, room_key: str) -> None:
        """使用持久化 Claude Agent SDK 会话驱动 Agent 完成一轮发言（增量消息注入）。"""
        from claude_agent_sdk import (ResultMessage, AssistantMessage, UserMessage, SystemMessage,
                                       TextBlock, ToolUseBlock, ToolResultBlock, ThinkingBlock)

        room: ChatRoom = room_service.get_room(room_key)
        self._sdk_room_slot[0] = room    # 更新槽，工具闭包自动可见
        self._sdk_done_slot[0] = False

        # 获取增量消息，同步写入 _history（与 sync_room 保持相同格式）
        new_msgs = room.get_unread_messages(self.name)
        prompt_lines = []
        for msg in new_msgs:
            if msg.sender_name == self.name:
                continue
            if msg.sender_name == "system":
                text = f"{room.name} 房间系统消息: {msg.content}"
                self._append_history_message(LlmApiMessage(role=OpenaiLLMApiRole.USER, content=text))
                prompt_lines.append(f"[系统] {msg.content}")
            else:
                text = f"{msg.sender_name} 在 {room.name} 房间发言: {msg.content}"
                self._append_history_message(LlmApiMessage.text(OpenaiLLMApiRole.USER, text))
                prompt_lines.append(f"{msg.sender_name}: {msg.content}")

        context_text = "\n".join(prompt_lines) if prompt_lines else "(无新消息)"
        turn_prompt = (
            f"新收到的消息：\n{context_text}\n\n"
            f"现在轮到你（{self.name}）在 {room.name} 发言。"
            f"你必须调用 send_chat_msg 发送消息或 skip_chat_msg 跳过本轮发言。"
        )

        client: Any = self._sdk_client
        logger.info(f"SDK 注入增量消息: agent={self.key}, room={room_key}, new_msgs={len(prompt_lines)}")

        try:
            await client.query(turn_prompt)
            logger.info(f"SDK prompt 已发送，等待响应: agent={self.key}")
            hint = f"你必须调用 send_chat_msg 将回复发送到 {room.name} 聊天室，或调用 skip_chat_msg 跳过本轮。直接输出的文字不会出现在聊天室里。"
            max_retries = 3
            for attempt in range(max_retries):
                if attempt > 0:
                    logger.info(f"SDK 注入发言提醒: agent={self.key}, attempt={attempt}")
                    await client.query(hint)
                msg_count = 0
                _interrupted = False
                async for msg in client.receive_response():
                    msg_count += 1
                    if isinstance(msg, AssistantMessage):
                        parts = []
                        for block in (msg.content or []):
                            if isinstance(block, TextBlock):
                                parts.append(f"text={block.text[:80]!r}")
                            elif isinstance(block, ToolUseBlock):
                                parts.append(f"tool_use={block.name}({block.input})")
                            elif isinstance(block, ThinkingBlock):
                                parts.append(f"thinking={block.thinking[:60]!r}")
                            else:
                                parts.append(f"{type(block).__name__}")
                        logger.info(f"SDK AssistantMessage: agent={self.key}, model={msg.model}, content=[{', '.join(parts)}]")
                    elif isinstance(msg, UserMessage):
                        parts = []
                        for block in (msg.content or []):
                            if isinstance(block, ToolResultBlock):
                                parts.append(f"tool_result(id={block.tool_use_id}, is_error={block.is_error})")
                            elif isinstance(block, TextBlock):
                                parts.append(f"text={block.text[:80]!r}")
                            else:
                                parts.append(f"{type(block).__name__}")
                        logger.info(f"SDK UserMessage: agent={self.key}, content=[{', '.join(parts)}]")
                        # 工具调用结果返回后，若本轮已完成则发起中断，但不立即 break，
                        # 而是让流自然结束，避免 interrupt 响应残留到下一轮
                        if self._sdk_done_slot[0] and not _interrupted:
                            logger.info(f"SDK 发言完成，主动中断会话: agent={self.key}")
                            await client.interrupt()
                            _interrupted = True
                    elif isinstance(msg, SystemMessage):
                        logger.info(f"SDK SystemMessage: agent={self.key}, subtype={msg.subtype}, data={msg.data}")
                    elif isinstance(msg, ResultMessage):
                        if msg.is_error:
                            logger.error(f"SDK 执行失败: agent={self.key}, room={room_key}, result={msg.result}")
                        else:
                            logger.info(f"SDK 会话完成: agent={self.key}, num_turns={msg.num_turns}, duration_ms={msg.duration_ms}, cost_usd={msg.total_cost_usd}")
                    else:
                        logger.debug(f"SDK 未知消息: agent={self.key}, type={type(msg).__name__}, data={msg}")
                logger.info(f"SDK receive_response 结束: agent={self.key}, total_msgs={msg_count}, attempt={attempt}")
                if self._sdk_done_slot[0]:
                    break
                logger.warning(f"SDK agent 未调用发言工具（可能只输出 thinking 或纯文字）: agent={self.key}, attempt={attempt}")
        except Exception as e:
            logger.error(f"SDK 会话异常: agent={self.key}, room={room_key}, error={e}", exc_info=True)
            raise

    async def chat(
        self,
        tools: Optional[List[Tool]] = None,
        done_check: Optional[Callable[[], bool]] = None,
        max_function_calls: int = 5,
    ) -> LlmApiMessage:
        """基于当前 _history 自动执行一轮对话（含对话中的多次 tool calls ），在满足条件时停止。

        停止条件（任一满足即返回）：
        - LLM 返回无 tool_calls（即输出文本，自然结束）
        - done_check() 返回 True（调用方判定完成）
        - 达到 max_function_calls 上限
        """
        assistant_message: Optional[LlmApiMessage] = None
        for _ in range(max_function_calls):
            assistant_message = await self._infer(tools)

            if not assistant_message.tool_calls:
                return assistant_message

            logger.info(f"检测到工具调用: agent={self.key}, count={len(assistant_message.tool_calls)}")
            for tool_call in assistant_message.tool_calls:
                name = tool_call.function.get("name", "")
                args = tool_call.function.get("arguments", "")
                self._execute_tool(tool_call.id, name, args)

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
                self._append_history_message(LlmApiMessage(role=OpenaiLLMApiRole.USER, content=f"{room.name} 房间系统消息: {msg.content}"))
            else:
                self._append_history_message(LlmApiMessage.text(OpenaiLLMApiRole.USER, f"{msg.sender_name} 在 {room.name} 房间发言: {msg.content}"))

    async def _infer(self, tools: Optional[List[Tool]]) -> LlmApiMessage:
        """基于当前 _history 发起一次 LLM 调用，将 assistant 消息写入历史并返回。"""
        assert self._history and self._history[-1].role in (OpenaiLLMApiRole.USER, OpenaiLLMApiRole.TOOL, OpenaiLLMApiRole.SYSTEM), \
            f"[{self.key}] _infer 前最后一条消息不能是 assistant，当前为: {self._history[-1].role if self._history else 'empty'}"
        ctx = AgentDialogContext(
            system_prompt=self.system_prompt,
            messages=self._history,
            tools=tools or None,
        )
        response: LlmApiResponse = await llm_service.infer(self.model, ctx)
        assistant_message: LlmApiMessage = response.choices[0].message
        self._append_history_message(assistant_message)
        return assistant_message

    def _execute_tool(self, tool_call_id: str, name: str, args: str) -> None:
        """执行工具调用，将结果写入 _history，并记录调用信息供 run_turn 判定完成条件。"""
        self._last_called = {"name": name, "args": args}
        result = func_tool_service.run_tool_call(name, args, context=self._turn_ctx)
        self._append_history_message(LlmApiMessage.tool_result(tool_call_id, result))

    def dump_history_messages(self) -> List[AgentHistoryMessageRecord]:
        return [
            AgentHistoryMessageRecord(
                agent_key=self.key,
                seq=idx,
                message_json=msg.model_dump_json(exclude_none=True),
            )
            for idx, msg in enumerate(self._history)
        ]

    def inject_history_messages(self, items: List[AgentHistoryMessageRecord]) -> None:
        self._history = [LlmApiMessage.model_validate_json(item.message_json) for item in items]

    def _append_history_message(self, message: LlmApiMessage) -> None:
        self._history.append(message)
        self._persist_history_message(message)

    def _persist_history_message(self, message: LlmApiMessage) -> None:
        from service import persistence_service

        seq: int = len(self._history) - 1
        item = AgentHistoryMessageRecord(
            agent_key=self.key,
            seq=seq,
            message_json=message.model_dump_json(exclude_none=True),
        )
        persistence_service.dispatch(
            persistence_service.append_agent_history_message(item)
        )


async def startup() -> None:
    """初始化 Agent 服务，清空所有状态。"""
    global _agent_defs, _agents
    _agent_defs = {}
    _agents = {}


def load_agent_config(agents_config: list) -> None:
    """加载 Agent 定义（prompt/model）到 _agent_defs 字典，不创建实例。"""
    global _agent_defs
    _agent_defs = {cfg["name"]: cfg for cfg in agents_config}
    logger.info(f"加载 Agent 定义: {list(_agent_defs.keys())}")


async def create_team_agents(teams_config: list) -> None:
    """遍历所有 team，从 _agent_defs 读取定义，创建 agent@team 实例。"""
    base_prompt_tmpl = load_prompt("src/prompts/GroupChat.md")

    for team_config in teams_config:
        team_name = team_config["name"]

        agent_names_in_team = set()
        for group in team_config["groups"]:
            for name in group["members"]:
                if name != SpecialAgent.OPERATOR:
                    agent_names_in_team.add(name)

        for name in agent_names_in_team:
            if name not in _agent_defs:
                logger.warning(f"Agent 定义不存在: {name}，跳过创建")
                continue

            cfg: dict[str, Any] = _agent_defs[name]
            if "system_prompt" in cfg:
                agent_specific_prompt = cfg["system_prompt"]
            else:
                agent_specific_prompt = load_prompt(cfg["prompt_file"])

            full_prompt = base_prompt_tmpl + "\n\n" + agent_specific_prompt
            key = _make_agent_key(team_name, name)
            agent = Agent(
                name=name,
                team_name=team_name,
                system_prompt=full_prompt,
                model=cfg["model"],
                use_agent_sdk=cfg.get("use_agent_sdk", False),
                allowed_tools=cfg.get("allowed_tools", []),
            )
            _agents[key] = agent
            logger.info(f"创建 Agent 实例: key={key}, model={cfg['model']}")
            if cfg.get("use_agent_sdk", False):
                await agent.init_sdk()


def get_agent(team_name: str, agent_name: str) -> Agent:
    """返回指定 agent@team 的 Agent 实例。"""
    key = _make_agent_key(team_name, agent_name)
    return _agents[key]



def get_all_agents() -> List[Agent]:
    """返回所有 Agent 实例列表。"""
    return list(_agents.values())


def get_agents(team_name: str, room_name: str) -> List[Agent]:
    """返回指定 team 和 room 中的 Agent 实例列表。"""
    members: List[str] = room_service.get_member_names(team_name, room_name)
    return [_agents[_make_agent_key(team_name, n)] for n in members if _make_agent_key(team_name, n) in _agents]


def get_all_rooms(team_name: str, agent_name: str) -> List[str]:
    """返回指定 Agent 在指定 Team 中参与的所有房间 key 列表。"""
    return room_service.get_rooms_for_agent(team_name, agent_name)


async def shutdown() -> None:
    """关闭所有持久化 SDK 会话，清空 Agent 字典，程序退出前调用。"""
    global _agents, _agent_defs
    close_tasks: List[Any] = [a.close() for a in _agents.values() if a._sdk_client is not None]
    if close_tasks:
        await asyncio.gather(*close_tasks, return_exceptions=True)
    _agents = {}
    _agent_defs = {}

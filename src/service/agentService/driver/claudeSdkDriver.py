import json
import logging
import os
from typing import Any

from claude_agent_sdk import (
    AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, ResultMessage,
    SystemMessage, TextBlock, ThinkingBlock, ToolResultBlock, ToolUseBlock,
    UserMessage, create_sdk_mcp_server, tool,
)

from service.roomService import ToolCallContext, ChatRoom
from service.agentService.promptBuilder import build_turn_context_prompt
from service.funcToolService.toolLoader import get_function_metadata
from service.funcToolService.tools import FUNCTION_REGISTRY
from service import funcToolService, roomService
from model.dbModel.gtAgentTask import GtAgentTask
from constants import AgentHistoryStage, AgentHistoryStatus
from util import llmApiUtil

from .base import AgentDriver

logger = logging.getLogger(__name__)

_HINT_PROMPT = (
    "你必须通过调用工具来行动。如果你不需要发言，或者已经完成了所有行动，请务必调用 finish_chat_turn 结束本轮（即跳过）。直接输出的文字不会出现在聊天室里。"
)
_REMINDER_PROMPT = (
    "【提醒】检测到你直接输出了文字。这些文字不会出现在聊天室中！你必须使用 `send_chat_msg` 工具来发言。如果你已经说完，请调用 `finish_chat_turn`。"
)


def _format_sdk_blocks(blocks) -> list[str]:
    parts: list[str] = []
    block_list = [] if blocks is None else blocks

    for block in block_list:
        if isinstance(block, TextBlock):
            parts.append(f"text={block.text[:80]!r}")
            continue

        if isinstance(block, ToolUseBlock):
            parts.append(f"tool_use={block.name}({block.input})")
            continue

        if isinstance(block, ThinkingBlock):
            parts.append(f"thinking={block.thinking[:60]!r}")
            continue

        if isinstance(block, ToolResultBlock):
            parts.append(f"tool_result(id={block.tool_use_id}, is_error={block.is_error})")
            continue

        parts.append(f"{type(block).__name__}")

    return parts


class ClaudeSdkAgentDriver(AgentDriver):
    def __init__(self, host, config):
        super().__init__(host, config)
        self._sdk_client: ClaudeSDKClient | None = None
        self._turn_done: bool = False  # 当前轮次是否已完成发言，由 tool handler 设置，_run_turn_sdk 检查以决定是否中断/退出
        self._tool_call_counter: int = 0  # tool_call_id 计数器

    _SDK_TOOL_NAMES = ("send_chat_msg", "finish_chat_turn")

    async def startup(self) -> None:
        await super().startup()
        # 仅注册 SDK 使用的两个工具到 tool_registry
        self.host.tool_registry.clear()
        for t in funcToolService.get_tools_by_names(list(self._SDK_TOOL_NAMES)):
            fn_name = t.function.name
            self.host.tool_registry.register(
                t,
                funcToolService.run_tool_call,
                marks_turn_finish=fn_name == "finish_chat_turn",
            )

        server = create_sdk_mcp_server(
            "chat-tools",
            tools=[
                self._build_claude_sdk_tool(name) for name in self._SDK_TOOL_NAMES
            ],
        )
        options = ClaudeAgentOptions(
            system_prompt=self.host.system_prompt,
            allowed_tools=self.config.options.get("allowed_tools", []),
            mcp_servers={"chat": server},
            permission_mode="bypassPermissions",
            max_turns=self.config.options.get("max_turns", 100),
        )

        os.environ.pop("CLAUDECODE", None)

        client = ClaudeSDKClient(options=options)
        await client.connect()
        self._sdk_client = client
        logger.info(f"SDK 持久会话初始化: agent_id={self.host.gt_agent.id}")

    async def shutdown(self) -> None:
        if self._sdk_client is None:
            await super().shutdown()
            return

        try:
            await self._sdk_client.disconnect()
            logger.info(f"SDK 会话已关闭: agent_id={self.host.gt_agent.id}")
        except Exception as e:
            logger.error(f"SDK 会话关闭失败: agent_id={self.host.gt_agent.id}, error={e}", exc_info=True)
        finally:
            self._sdk_client = None
        await super().shutdown()

    async def run_chat_turn(self, task: GtAgentTask, synced_count: int, max_function_calls: int = 5) -> None:
        room_id = task.task_data.get("room_id")
        if room_id is None:
            logger.warning(f"run_chat_turn 跳过：task 缺少 room_id, agent_id={self.host.gt_agent.id}, task_id={task.id}")
            return

        room = roomService.get_room(room_id)
        if room is None:
            logger.warning(f"run_chat_turn 跳过：room_id={room_id} 不存在, agent_id={self.host.gt_agent.id}")
            return

        self._turn_done = False
        prompt_prefix = f"【{room.name}】 房间轮到你行动，新消息如下："

        if synced_count > 0:
            latest_history = self.host._history.last()
            assert latest_history is not None, f"synced_count={synced_count} 时 history 不应为空: agent_id={self.host.gt_agent.id}"
            turn_prompt = latest_history.content
            assert turn_prompt is not None, f"turn_prompt 不应为 None: agent_id={self.host.gt_agent.id}, room={room.key}"

            if turn_prompt.startswith(prompt_prefix) is False:
                raise ValueError(
                    f"ClaudeSdkAgentDriver 只接受完整 turn_prompt: agent_id={self.host.gt_agent.id}, room={room.key}"
                )
        else:
            turn_prompt = build_turn_context_prompt(room.name, [])

        await self._run_turn_sdk(room, turn_prompt, synced_count, max_function_calls)

    def _next_tool_call_id(self) -> str:
        """生成下一个 tool_call_id。"""
        self._tool_call_counter += 1
        return f"claude_sdk_{self._tool_call_counter}"

    def _build_claude_sdk_tool(self, tool_name: str):
        meta = get_function_metadata(tool_name, FUNCTION_REGISTRY[tool_name])

        @tool(tool_name, meta["description"], meta["parameters"])
        async def _wrapped(args):
            # 写入 tool_use 消息到 history
            tool_call_id = self._next_tool_call_id()
            await self.host._history.append_history_message(
                llmApiUtil.OpenAIMessage(
                    role="assistant",
                    content=None,
                    tool_calls=[
                        {
                            "id": tool_call_id,
                            "type": "function",
                            "function": {"name": tool_name, "arguments": json.dumps(args, ensure_ascii=False)},
                        }
                    ],
                ),
                stage=AgentHistoryStage.INFER,
                status=AgentHistoryStatus.SUCCESS,
            )

            # 执行最后一条 assistant 消息中的 tool_call 并写入 tool_result
            await self.host._execute_tool()

            # 获取最后一个 tool_result 消息作为返回值
            result_history = self.host._history.find_tool_result_by_call_id(tool_call_id)
            result = (result_history.content if result_history else "") or ""

            result_data = json.loads(result)
            is_error = result_data.get("success", True) is not True

            if is_error is False:
                if tool_name == "finish_chat_turn":
                    self._turn_done = True

            return {"content": [{"type": "text", "text": result}], "isError": is_error}

        return _wrapped

    async def _run_turn_sdk(self, room: ChatRoom, turn_prompt: str, synced_count: int, max_function_calls: int) -> None:
        """执行一次 SDK turn：发送 prompt → 多次尝试等待 agent 使用工具完成发言。"""
        client = self._sdk_client

        if client is None:
            raise RuntimeError(f"Claude SDK client 尚未初始化: agent_id={self.host.gt_agent.id}")

        max_attempts = max(1, max_function_calls)
        logger.info(f"SDK 注入增量消息: agent_id={self.host.gt_agent.id}, room={room.key}, new_msgs={synced_count}")

        try:
            await client.query(turn_prompt)
            logger.info(f"SDK prompt 已发送，等待响应: agent_id={self.host.gt_agent.id}")
            hint = _HINT_PROMPT

            for attempt in range(max_attempts):
                if attempt > 0:
                    logger.info(f"SDK 注入发言提醒: agent_id={self.host.gt_agent.id}, attempt={attempt}")
                    await client.query(hint)

                has_direct_text = await self._consume_response_stream(client, room)

                if self._turn_done is True:
                    if has_direct_text and room._current_turn_has_content is False:
                        logger.warning(f"SDK Agent 输出了文字但未调用 send_chat_msg，强制提醒: agent_id={self.host.gt_agent.id}")
                        self._turn_done = False
                        hint = _REMINDER_PROMPT
                        continue
                    break

                logger.warning(f"SDK agent 未调用发言工具（可能只输出 thinking 或纯文字）: agent_id={self.host.gt_agent.id}, attempt={attempt}")
        except Exception as e:
            logger.error(f"SDK 会话异常: agent_id={self.host.gt_agent.id}, room={room.key}, error={e}", exc_info=True)
            raise

    async def _consume_response_stream(self, client: ClaudeSDKClient, room: ChatRoom) -> bool:
        """消费一轮 SDK 响应流，处理各类消息。返回是否检测到直接文本输出。"""
        has_direct_text = False
        msg_count = 0
        interrupted = False

        async for msg in client.receive_response():
            msg_count += 1

            if isinstance(msg, AssistantMessage):
                parts = _format_sdk_blocks(msg.content)
                logger.info(f"SDK AssistantMessage: agent_id={self.host.gt_agent.id}, model={msg.model}, content=[{', '.join(parts)}]")
                for block in msg.content:
                    if isinstance(block, TextBlock) and len(block.text.strip()) > 0:
                        logger.warning(f"检测到 SDK Agent 直接输出文字: agent_id={self.host.gt_agent.id}, text={block.text[:50]!r}")
                        has_direct_text = True

            elif isinstance(msg, UserMessage):
                parts = _format_sdk_blocks(msg.content)
                logger.info(f"SDK UserMessage: agent_id={self.host.gt_agent.id}, content=[{', '.join(parts)}]")
                if self._turn_done is True and interrupted is False:
                    logger.info(f"SDK 发言完成，主动中断会话: agent_id={self.host.gt_agent.id}")
                    await client.interrupt()
                    interrupted = True

            elif isinstance(msg, SystemMessage):
                logger.info(f"SDK SystemMessage: agent_id={self.host.gt_agent.id}, subtype={msg.subtype}, data={msg.data}")

            elif isinstance(msg, ResultMessage):
                if msg.is_error is True:
                    logger.error(f"SDK 执行失败: agent_id={self.host.gt_agent.id}, room={room.key}, result={msg.result}")
                else:
                    logger.info(f"SDK 会话完成: agent_id={self.host.gt_agent.id}, num_turns={msg.num_turns}, duration_ms={msg.duration_ms}, cost_usd={msg.total_cost_usd}")

            else:
                logger.debug(f"SDK 未知消息: agent_id={self.host.gt_agent.id}, type={type(msg).__name__}, data={msg}")

        logger.info(f"SDK receive_response 结束: agent_id={self.host.gt_agent.id}, total_msgs={msg_count}")
        return has_direct_text

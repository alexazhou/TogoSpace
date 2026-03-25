import json
import logging
import os
from typing import Any

from claude_agent_sdk import (
    AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, ResultMessage,
    SystemMessage, TextBlock, ThinkingBlock, ToolResultBlock, ToolUseBlock,
    UserMessage, create_sdk_mcp_server, tool,
)

from service.roomService import ChatContext
from service.funcToolService.toolLoader import get_function_metadata
from service.funcToolService.tools import FUNCTION_REGISTRY
from service.roomService import ChatRoom
from util import llmApiUtil

from .base import AgentDriver

logger = logging.getLogger(__name__)

# Prompt 模板
_SYSTEM_MSG_PREFIX_TEMPLATE = "{room_name} 房间系统消息: "
_USER_MSG_SEP_TEMPLATE = " 在 {room_name} 房间发言: "
_TURN_PROMPT_TEMPLATE = (
    "新收到的消息：\n{context}\n\n"
    "现在轮到你（{agent_name}）在 {room_name} 发言。"
    "你必须调用工具来行动。如果你已完成发言和所有工具调用，请务必调用 finish_chat_turn 结束本轮行动。"
)
_HINT_PROMPT = (
    "你必须通过调用工具来行动。如果你不需要发言，或者已经完成了所有行动，请务必调用 finish_chat_turn 结束本轮（即跳过）。直接输出的文字不会出现在聊天室里。"
)
_REMINDER_PROMPT = (
    "【提醒】检测到你直接输出了文字。这些文字不会出现在聊天室中！你必须使用 `send_chat_msg` 工具来发言。如果你已经说完，请调用 `finish_chat_turn`。"
)


def _format_sdk_blocks(blocks) -> list[str]:
    parts: list[str] = []

    for block in (blocks or []):
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

    async def startup(self) -> None:
        server = create_sdk_mcp_server(
            "chat-tools",
            tools=[
                self._build_claude_sdk_tool("send_chat_msg"),
                self._build_claude_sdk_tool("finish_chat_turn"),
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
        logger.info(f"SDK 持久会话初始化: agent={self.host.key}")

    async def shutdown(self) -> None:
        if self._sdk_client is None:
            return

        try:
            await self._sdk_client.disconnect()
            logger.info(f"SDK 会话已关闭: agent={self.host.key}")
        except Exception as e:
            logger.error(f"SDK 会话关闭失败: agent={self.host.key}, error={e}", exc_info=True)
        finally:
            self._sdk_client = None

    async def run_chat_turn(self, room: ChatRoom, synced_count: int, max_function_calls: int = 5) -> None:
        self._turn_done = False
        prompt_lines = self._build_prompt_lines_from_history(room, synced_count)
        await self._run_turn_sdk(room, prompt_lines, max_function_calls)

    def _build_prompt_lines_from_history(self, room: ChatRoom, synced_count: int) -> list[str]:
        if synced_count <= 0:
            return []

        recent_history = self.host._history[-synced_count:]
        prompt_lines: list[str] = []
        system_prefix = _SYSTEM_MSG_PREFIX_TEMPLATE.format(room_name=room.name)
        user_sep = _USER_MSG_SEP_TEMPLATE.format(room_name=room.name)

        for message in recent_history:
            content = message.content or ""

            if content.startswith(system_prefix):
                prompt_lines.append(f"[系统] {content[len(system_prefix):]}")
                continue

            if user_sep in content:
                sender, msg = content.split(user_sep, 1)
                prompt_lines.append(f"{sender}: {msg}")
                continue

            prompt_lines.append(content)

        return prompt_lines

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
            await self.host.append_history_message(
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
                )
            )

            # 执行最后一条 assistant 消息中的 tool_call 并写入 tool_result
            await self.host._execute_tool()

            # 获取最后一个 tool_result 消息作为返回值
            history = self.host._history
            result = ""
            for msg in reversed(history):
                if msg.role == llmApiUtil.OpenaiLLMApiRole.TOOL and msg.tool_call_id == tool_call_id:
                    result = msg.content or ""
                    break

            result_data = json.loads(result)
            is_error = not result_data.get("success", True)
            if not is_error:
                if tool_name == "finish_chat_turn":
                    self._turn_done = True

            return {"content": [{"type": "text", "text": result}], "isError": is_error}

        return _wrapped

    async def _run_turn_sdk(self, room: ChatRoom, prompt_lines: list[str], max_function_calls: int) -> None:
        context_text = "\n".join(prompt_lines) if prompt_lines else "(无新消息)"
        turn_prompt = _TURN_PROMPT_TEMPLATE.format(
            context=context_text,
            agent_name=self.host.name,
            room_name=room.name
        )

        client = self._sdk_client

        if client is None:
            raise RuntimeError(f"Claude SDK client 尚未初始化: agent={self.host.key}")

        max_attempts = max(1, max_function_calls)
        logger.info(f"SDK 注入增量消息: agent={self.host.key}, room={room.key}, new_msgs={len(prompt_lines)}")

        try:
            await client.query(turn_prompt)
            logger.info(f"SDK prompt 已发送，等待响应: agent={self.host.key}")
            hint = _HINT_PROMPT
            
            for attempt in range(max_attempts):
                # 追踪本次尝试是否发生了直接文本输出
                has_direct_text = False

                if attempt > 0:
                    logger.info(f"SDK 注入发言提醒: agent={self.host.key}, attempt={attempt}")
                    await client.query(hint)

                msg_count = 0
                interrupted = False
                async for msg in client.receive_response():
                    msg_count += 1

                    if isinstance(msg, AssistantMessage):
                        parts = _format_sdk_blocks(msg.content)
                        logger.info(
                            f"SDK AssistantMessage: agent={self.host.key}, model={msg.model}, content=[{', '.join(parts)}]"
                        )
                        # 检查是否有 TextBlock
                        for block in msg.content:
                            if isinstance(block, TextBlock) and block.text.strip():
                                logger.warning(f"检测到 SDK Agent 直接输出文字: agent={self.host.key}, text={block.text[:50]!r}")
                                has_direct_text = True

                    elif isinstance(msg, UserMessage):
                        parts = _format_sdk_blocks(msg.content)
                        logger.info(f"SDK UserMessage: agent={self.host.key}, content=[{', '.join(parts)}]")

                        if self._turn_done and not interrupted:
                            logger.info(f"SDK 发言完成，主动中断会话: agent={self.host.key}")
                            await client.interrupt()
                            interrupted = True

                    elif isinstance(msg, SystemMessage):
                        logger.info(f"SDK SystemMessage: agent={self.host.key}, subtype={msg.subtype}, data={msg.data}")

                    elif isinstance(msg, ResultMessage):
                        if msg.is_error:
                            logger.error(f"SDK 执行失败: agent={self.host.key}, room={room.key}, result={msg.result}")
                        else:
                            logger.info(
                                f"SDK 会话完成: agent={self.host.key}, num_turns={msg.num_turns}, duration_ms={msg.duration_ms}, cost_usd={msg.total_cost_usd}"
                            )

                    else:
                        logger.debug(f"SDK 未知消息: agent={self.host.key}, type={type(msg).__name__}, data={msg}")

                logger.info(
                    f"SDK receive_response 结束: agent={self.host.key}, total_msgs={msg_count}, attempt={attempt}"
                )

                if self._turn_done:
                    # 检查是否存在"无效发言"：输出了文字但房间没收到内容
                    if has_direct_text and not room._current_turn_has_content:
                        logger.warning(f"SDK Agent 输出了文字但未调用 send_chat_msg，强制提醒: agent={self.host.key}")
                        # 重置状态，注入提醒
                        self._turn_done = False
                        hint = _REMINDER_PROMPT
                        continue
                    break

                logger.warning(
                    f"SDK agent 未调用发言工具（可能只输出 thinking 或纯文字）: agent={self.host.key}, attempt={attempt}"
                )
        except Exception as e:
            logger.error(f"SDK 会话异常: agent={self.host.key}, room={room.key}, error={e}", exc_info=True)
            raise

import logging
import os
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    create_sdk_mcp_server,
    tool,
)

from service import room_service
from service.room_service import ChatRoom

from .base import AgentDriver

logger = logging.getLogger(__name__)

_SEND_CHAT_MSG_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "room_name": {"type": "string"},
        "msg": {"type": "string"},
    },
    "required": ["room_name", "msg"],
}

_SKIP_CHAT_MSG_TOOL_SCHEMA = {
    "type": "object",
    "properties": {},
}


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
        self._sdk_client = None

    async def startup(self) -> None:
        server = create_sdk_mcp_server(
            "chat-tools",
            tools=[self._build_send_tool(), self._build_skip_tool()],
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
        prompt_lines = self._build_prompt_lines_from_history(room, synced_count)
        await self._run_turn_sdk(room.key, room, prompt_lines)

    def _build_prompt_lines_from_history(self, room: ChatRoom, synced_count: int) -> list[str]:
        if synced_count <= 0:
            return []

        recent_history = self.host._history[-synced_count:]
        prompt_lines: list[str] = []
        system_prefix = f"{room.name} 房间系统消息: "
        user_sep = f" 在 {room.name} 房间发言: "

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

    def _build_send_tool(self):
        @tool("send_chat_msg", "向聊天室发送消息", _SEND_CHAT_MSG_TOOL_SCHEMA)
        async def _send(args):
            room_name = args.get("room_name", "")
            msg = args.get("msg", "")
            logger.info(
                f"SDK MCP tool called: send_chat_msg, agent={self.host.key}, room={room_name}, msg_len={len(msg)}"
            )
            result = await self.host.send_chat_message(room_name, msg)
            return {
                "content": [{"type": "text", "text": result.message}],
                "isError": not result.ok,
            }

        return _send

    def _build_skip_tool(self):
        @tool("skip_chat_msg", "跳过本轮发言", _SKIP_CHAT_MSG_TOOL_SCHEMA)
        async def _skip(args):
            logger.info(f"SDK MCP tool called: skip_chat_msg, agent={self.host.key}")
            result = self.host.skip_chat_turn()
            return {
                "content": [{"type": "text", "text": result.message}],
                "isError": not result.ok,
            }

        return _skip

    async def _run_turn_sdk(self, room_key: str, room: ChatRoom, prompt_lines: list[str]) -> None:
        context_text = "\n".join(prompt_lines) if prompt_lines else "(无新消息)"
        turn_prompt = (
            f"新收到的消息：\n{context_text}\n\n"
            f"现在轮到你（{self.host.name}）在 {room.name} 发言。"
            f"你必须调用 send_chat_msg 发送消息或 skip_chat_msg 跳过本轮发言。"
        )

        client: Any = self._sdk_client
        logger.info(f"SDK 注入增量消息: agent={self.host.key}, room={room_key}, new_msgs={len(prompt_lines)}")

        try:
            await client.query(turn_prompt)
            logger.info(f"SDK prompt 已发送，等待响应: agent={self.host.key}")
            hint = f"你必须调用 send_chat_msg 将回复发送到 {room.name} 聊天室，或调用 skip_chat_msg 跳过本轮。直接输出的文字不会出现在聊天室里。"
            max_retries = 3
            for attempt in range(max_retries):
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

                    elif isinstance(msg, UserMessage):
                        parts = _format_sdk_blocks(msg.content)
                        logger.info(f"SDK UserMessage: agent={self.host.key}, content=[{', '.join(parts)}]")

                        if self.host.current_room is None and not interrupted:
                            logger.info(f"SDK 发言完成，主动中断会话: agent={self.host.key}")
                            await client.interrupt()
                            interrupted = True

                    elif isinstance(msg, SystemMessage):
                        logger.info(f"SDK SystemMessage: agent={self.host.key}, subtype={msg.subtype}, data={msg.data}")

                    elif isinstance(msg, ResultMessage):
                        if msg.is_error:
                            logger.error(f"SDK 执行失败: agent={self.host.key}, room={room_key}, result={msg.result}")
                        else:
                            logger.info(
                                f"SDK 会话完成: agent={self.host.key}, num_turns={msg.num_turns}, duration_ms={msg.duration_ms}, cost_usd={msg.total_cost_usd}"
                            )

                    else:
                        logger.debug(f"SDK 未知消息: agent={self.host.key}, type={type(msg).__name__}, data={msg}")

                logger.info(
                    f"SDK receive_response 结束: agent={self.host.key}, total_msgs={msg_count}, attempt={attempt}"
                )

                if self.host.current_room is None:
                    break

                logger.warning(
                    f"SDK agent 未调用发言工具（可能只输出 thinking 或纯文字）: agent={self.host.key}, attempt={attempt}"
                )
        except Exception as e:
            logger.error(f"SDK 会话异常: agent={self.host.key}, room={room_key}, error={e}", exc_info=True)
            raise

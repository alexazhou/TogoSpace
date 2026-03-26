from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from typing import Any, Optional

from pytspclient import TSPClient, TSPException, TSPInitializeResult, TSPToolResponse
from service.agentService.driver.base import AgentDriverConfig

from exception import TeamAgentException
from service import funcToolService
from service.roomService import ChatContext, ChatRoom
from util import llmApiUtil

from .base import AgentDriver

logger = logging.getLogger(__name__)

_LOCAL_CHAT_TOOL_NAMES = ["send_chat_msg", "finish_chat_turn"]
_DEFAULT_PROTOCOL_VERSION = "0.3"
_DEFAULT_REQUEST_TIMEOUT_SEC = 30
_RUN_CHAT_TURN_MAX_RETRIES = 3
_RUN_CHAT_TURN_HINT = (
    "你必须通过调用工具来行动。如果你不需要发言，或者已经完成了所有行动，"
    "请务必调用 finish_chat_turn 结束本轮（即跳过）。"
)


def build_gtsp_command(raw_command: Optional[list[str]], workdir: str) -> list[str]:
    if raw_command is None:
        default_binary = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "../../../../assert/execute/gtsp")
        )
        command = [default_binary, "--mode", "stdio"]
    else:
        command = list(raw_command)

    if "--workdir" not in command and workdir:
        command.extend(["--workdir", workdir])
    return command


class TspAgentDriver(AgentDriver):
    def __init__(self, host: Any, config: AgentDriverConfig) -> None:
        super().__init__(host, config)
        self._client: Optional[TSPClient] = None
        self._tsp_tools: dict[str, llmApiUtil.OpenAITool] = {}
        _local = funcToolService.get_tools_by_names(_LOCAL_CHAT_TOOL_NAMES)
        self._local_tools: dict[str, llmApiUtil.OpenAITool] = {t.function.name: t for t in _local}

    async def startup(self) -> None:
        options = self.config.options
        work_dir = str(options.get("workdir") or self.host.team_workdir)
        command = build_gtsp_command(options.get("command"), work_dir)

        timeout_sec = int(options.get("request_timeout_sec", _DEFAULT_REQUEST_TIMEOUT_SEC))
        include: Optional[list[str]] = options.get("tool_include") or None
        exclude: Optional[list[str]] = options.get("tool_exclude") or None

        client = TSPClient(command=command, request_timeout_sec=timeout_sec)
        await client.connect()
        try:
            result: TSPInitializeResult = await client.initialize(
                client_info={"name": "agent_team.tsp_driver"},
                include=include,
                exclude=exclude,
            )
            self._load_tsp_tools(result)
        except Exception:
            await client.disconnect()
            raise

        self._client = client
        logger.info(
            "TSP driver initialized: agent=%s command=%s tools=%s",
            self.host.key,
            command,
            len(self._tsp_tools),
        )

    async def shutdown(self) -> None:
        if self._client is None:
            return
        try:
            await self._client.shutdown()
        finally:
            self._client = None

    async def run_chat_turn(self, room: ChatRoom, synced_count: int, max_function_calls: int = 5) -> None:
        for _ in range(_RUN_CHAT_TURN_MAX_RETRIES):
            turn_done = await self._run_until_reply(
                room=room,
                tools=[*self._tsp_tools.values(), *self._local_tools.values()],
                max_function_calls=max_function_calls,
            )
            if turn_done:
                break
            await self.host.append_history_message(
                llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiLLMApiRole.USER, _RUN_CHAT_TURN_HINT)
            )

    async def _run_until_reply(
        self,
        room: ChatRoom,
        tools: Optional[list[llmApiUtil.OpenAITool]] = None,
        max_function_calls: int = 5,
    ) -> bool:
        if self._client is None:
            raise RuntimeError(f"TSP client 尚未初始化: agent={self.host.key}")

        for _ in range(max_function_calls):
            assistant_message = await self.host._infer(tools)
            tool_calls = assistant_message.tool_calls
            if not tool_calls:
                return False

            turn_done = await self._execute_tool_calls(tool_calls)
            if turn_done:
                return True

        logger.warning("达到最大函数调用次数: agent=%s max=%s", self.host.key, max_function_calls)
        return False

    async def _execute_tool_calls(self, tool_calls: list[llmApiUtil.OpenAIToolCall]) -> bool:
        turn_done = False
        for tool_call in tool_calls:
            function = tool_call.function
            function_name = str(function.get("name", ""))
            function_args = str(function.get("arguments", "{}"))
            tool_call_id = str(tool_call.id or uuid.uuid4().hex)

            if function_name in self._local_tools:
                context = ChatContext(
                    agent_name=self.host.name,
                    team_name=self.host.team_name,
                    chat_room=self.host.current_room,
                )
                result_json = await funcToolService.run_tool_call(function_name, function_args, context=context)
                await self.host.append_history_message(llmApiUtil.OpenAIMessage.tool_result(tool_call_id, result_json))
                if function_name == "finish_chat_turn" and _is_tool_call_succeeded(result_json):
                    turn_done = True
                continue

            if function_name in self._tsp_tools:
                result_dict = await self._execute_tsp_tool(function_name, function_args)
                result_json = json.dumps(result_dict, ensure_ascii=False)
                await self.host.append_history_message(llmApiUtil.OpenAIMessage.tool_result(tool_call_id, result_json))
                continue

            result_json = json.dumps({"success": False, "message": f"未知工具: {function_name}"}, ensure_ascii=False)
            await self.host.append_history_message(llmApiUtil.OpenAIMessage.tool_result(tool_call_id, result_json))

        return turn_done

    async def _execute_tsp_tool(self, function_name: str, function_args: str) -> dict[str, Any]:
        assert self._client is not None, "TSP client 尚未初始化"
        try:
            parsed_args = json.loads(function_args)
        except json.JSONDecodeError as e:
            return {"success": False, "message": f"TSP 参数 JSON 解析失败: {e}"}

        try:
            response: TSPToolResponse = await self._client.tool(function_name, parsed_args)
            return response.to_dict()
        except TSPException as e:
            return {"success": False, "code": e.code, "message": e.message}
        except Exception as e:
            return {"success": False, "message": f"TSP 工具调用失败: {e}"}

    def _load_tsp_tools(self, initialize_result: TSPInitializeResult) -> None:
        resolved: dict[str, llmApiUtil.OpenAITool] = {}

        # 使用 pyTSPClient 暴露的 dataclass 结构
        for tool in initialize_result.capabilities.tools:
            name = tool.name
            input_schema = tool.input_schema

            resolved[name] = llmApiUtil.OpenAITool(
                function=llmApiUtil.OpenAIFunction(
                    name=name,
                    description=tool.description,
                    parameters=llmApiUtil.OpenAIFunctionParameter(**input_schema),
                )
            )

        self._tsp_tools = resolved


def _is_tool_call_succeeded(result_json: str) -> bool:
    try:
        data = json.loads(result_json)
    except json.JSONDecodeError:
        return False
    return bool(data.get("success"))

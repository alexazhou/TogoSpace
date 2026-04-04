from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

from pytspclient import TSPClient, TSPException, TSPInitializeResult, TSPToolResponse
from service.agentService.driver.base import AgentDriverConfig

from service import funcToolService
from service.roomService import ToolCallContext, ChatRoom
from util import llmApiUtil

from .base import AgentDriver, AgentTurnSetup

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
        work_dir = str(options.get("workdir") or self.host.agent_workdir)
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
        self._register_host_tools()
        logger.info(
            "TSP driver initialized: agent=%s command=%s tools=%s",
            self.host.gt_agent.id,
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

    @property
    def host_managed_turn_loop(self) -> bool:
        return True

    def _register_host_tools(self) -> None:
        if self._client is None:
            raise RuntimeError(f"TSP client 尚未初始化: agent_id={self.host.gt_agent.id}")
        self.host.tool_registry.clear()

        for function_name, tool in self._local_tools.items():
            self.host.tool_registry.register(
                tool,
                funcToolService.run_tool_call,
                marks_turn_finish=function_name == "finish_chat_turn",
            )

        for tool in self._tsp_tools.values():
            self.host.tool_registry.register(tool, self._execute_tsp_tool)

    @property
    def turn_setup(self) -> AgentTurnSetup:
        return AgentTurnSetup(
            max_retries=_RUN_CHAT_TURN_MAX_RETRIES,
            hint_prompt=_RUN_CHAT_TURN_HINT,
        )

    async def run_chat_turn(self, room: ChatRoom, synced_count: int, max_function_calls: int = 5) -> None:
        raise RuntimeError("TspAgentDriver 不再直接执行 run_chat_turn，请使用 Agent.run_chat_turn")

    async def _execute_tsp_tool(
        self,
        function_args: str,
        context: ToolCallContext | None = None,
    ) -> dict[str, Any]:

        assert self._client is not None, "TSP client 尚未初始化"
        function_name = context.tool_name if context is not None else ""

        if not function_name:
            return {"success": False, "message": "TSP 工具调用失败: tool_name 为空"}
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

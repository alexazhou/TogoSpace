from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Optional

from pytspclient import TSPClient, TSPException, ToolResult, ToolCall

import appPaths
from service.agentService.driver.base import AgentDriverConfig

from service import funcToolService
from service.roomService import ToolCallContext
from model.dbModel.gtAgentTask import GtAgentTask
from util import llmApiUtil

from .base import AgentDriver, AgentTurnSetup

logger = logging.getLogger(__name__)

_LOCAL_TOOL_NAMES = [
    "get_time",
    "get_dept_info",
    "get_room_info",
    "get_agent_info",
    "wake_up_agent",
    "send_chat_msg",
    "finish_chat_turn",
]
_DEFAULT_REQUEST_TIMEOUT_SEC = 30
_RUN_CHAT_TURN_MAX_RETRIES = 3
_RUN_CHAT_TURN_HINT = (
    "你必须通过调用工具来行动。如果你不需要发言，或者已经完成了所有行动，"
    "请务必调用 finish_chat_turn 结束本轮（即跳过）。"
)
_RUN_CHAT_TURN_ERROR_ACTION_HINT = (
    "系统提示: 检测到你将工具调用以 JSON 格式写入了消息文本，这将无法被送达用户。"
    "你必须通过调用 send_chat_msg / finish_chat_turn 工具，而不是在消息内容中输出 JSON。"
    "请重新行动，直接调用相应的工具。"
)


def build_gtsp_command(raw_command: Optional[list[str]], workdir: str) -> list[str]:
    if raw_command is None:
        default_binary = appPaths.get_gtsp_binary_path()
        command = [default_binary, "--mode", "stdio"]
    else:
        command = list(raw_command)

    if "--workdir" not in command and workdir:
        command.extend(["--workdir", workdir])

    # 添加日志路径，使用用户可写目录（避免 App Translocation 只读问题）
    if "--log-path" not in command:
        log_path = os.path.join(appPaths.LOGS_DIR, "gtsp")
        os.makedirs(log_path, exist_ok=True)
        command.extend(["--log-path", log_path])

    return command


class TspAgentDriver(AgentDriver):
    def __init__(self, host: Any, config: AgentDriverConfig) -> None:
        super().__init__(host, config)
        self._client: Optional[TSPClient] = None
        self._tsp_tools: dict[str, llmApiUtil.OpenAITool] = {}
        _local = funcToolService.get_tools_by_names(_LOCAL_TOOL_NAMES)
        self._local_tools: dict[str, llmApiUtil.OpenAITool] = {t.function.name: t for t in _local}
        self._connect_lock = asyncio.Lock()

        # 构建连接参数（用于首次连接和后续按需重连）
        options = config.options
        work_dir = str(options.get("workdir") or host.agent_workdir)
        command_list = build_gtsp_command(options.get("command"), work_dir)
        timeout_sec = int(options.get("request_timeout_sec", _DEFAULT_REQUEST_TIMEOUT_SEC))
        self._connect_params: dict[str, Any] = {
            "command": command_list,
            "timeout_sec": timeout_sec,
            "include": options.get("tool_include") or None,
            "exclude": options.get("tool_exclude") or None,
        }

    def _is_client_connected(self) -> bool:
        """检查 TSP client 进程是否仍然存活。

        gtsp 进程可能因异常退出或被终止而留下僵尸 client 对象，
        此时 _client 非空但 process.returncode 已有值。后续 tool() 调用会
        因写入已关闭的 stdin 而超时或抛异常，需提前检测避免无效等待。
        """
        if self._client is None:
            return False
        process = self._client.process
        if process is None:
            return False
        # returncode 为 None 表示进程仍在运行，非 None 表示已退出
        return process.returncode is None

    async def _ensure_connected(self) -> bool:
        """确保 TSP client 已连接，若断开则重新连接。

        使用锁防止并发连接，连接成功后重新注册工具。
        返回 True 表示连接成功（或已连接），False 表示失败。
        """
        async with self._connect_lock:
            # 再次检查，防止其他协程已完成连接
            if self._is_client_connected():
                return True

            logger.info("TSP 服务断开，尝试重新连接: agent_id=%s", self.host.gt_agent.id)

            # 清理旧 client
            if self._client is not None:
                try:
                    await self._client.disconnect()
                except Exception:
                    pass
                self._client = None

            try:
                # 使用新的工厂方法创建 client
                client = await TSPClient.from_stdio(
                    self._connect_params["command"],
                    self._connect_params["timeout_sec"],
                ).start()
                await client.initialize(
                    client_info={"name": "agent_team.tsp_driver"},
                    include=self._connect_params.get("include"),
                    exclude=self._connect_params.get("exclude"),
                )
                self._load_tsp_tools(client.tools)
                self._client = client
                self._register_host_tools()
                logger.info("TSP 连接成功: agent_id=%s, tools=%s", self.host.gt_agent.id, len(self._tsp_tools))
                return True
            except Exception as e:
                logger.error("TSP 连接失败: agent_id=%s, error=%s", self.host.gt_agent.id, e)
                return False

    async def startup(self) -> None:
        await super().startup()

        # 使用 _ensure_connected 完成首次连接
        connected = await self._ensure_connected()
        if not connected:
            raise RuntimeError(f"TSP 首次连接失败: agent_id={self.host.gt_agent.id}")

        logger.info(f"TSP driver initialized: agent={self.host.gt_agent.id} command={self._connect_params['command']} tools={len(self._tsp_tools)}")

    async def shutdown(self) -> None:
        if self._client is None:
            await super().shutdown()
            return
        client = self._client
        self._client = None

        # 直接 disconnect 终止进程（不等待 shutdown request）
        # 避免 wait_for 取消导致 disconnect 也被取消
        try:
            await client.disconnect()
        except Exception as e:
            logger.warning("TSP disconnect 异常: agent_id=%s, error=%s", self.host.gt_agent.id, e)

        await super().shutdown()

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
            hint_prompt_error_action=_RUN_CHAT_TURN_ERROR_ACTION_HINT,
        )

    async def run_chat_turn(self, task: GtAgentTask, synced_count: int) -> None:
        raise RuntimeError("TspAgentDriver 不再直接执行 run_chat_turn，请使用 Agent.run_chat_turn")

    async def _execute_tsp_tool(
        self,
        function_args: str,
        context: ToolCallContext | None = None,
    ) -> dict[str, Any]:

        # 确保连接，若断开则自动重连
        if not self._is_client_connected():
            connected = await self._ensure_connected()
            if not connected:
                return {"success": False, "message": "TSP 服务已断开且重连失败，请重启 Agent 或联系管理员"}

        function_name = context.tool_name if context is not None else ""

        if not function_name:
            return {"success": False, "message": "TSP 工具调用失败: tool_name 为空"}
        try:
            parsed_args = json.loads(function_args)
        except json.JSONDecodeError as e:
            return {"success": False, "message": f"TSP 参数 JSON 解析失败: {e}"}

        try:
            call = ToolCall(name=function_name, input=parsed_args)
            result: ToolResult = await self._client.call_tool(call)
            # ToolResult.output 是原始类型（dict / list / str）
            return result.output
        except TSPException as e:
            return {"success": False, "code": e.code, "message": e.message}
        except Exception as e:
            # 工具执行过程中可能因 gtsp 再次退出而失败，记录日志
            logger.warning("TSP 工具执行异常: agent_id=%s, tool=%s, error=%s", self.host.gt_agent.id, function_name, e)
            return {"success": False, "message": f"TSP 工具调用失败: {e}"}

    def _load_tsp_tools(self, tools: list[Any]) -> None:
        resolved: dict[str, llmApiUtil.OpenAITool] = {}

        for tool in tools:
            # tools 是 TSPTool 对象，直接访问属性
            name = tool.name
            input_schema = tool.input_schema

            # JSON Schema 中 required 字段是可选的，OpenAI API 需要，默认为空列表
            if "required" not in input_schema:
                input_schema["required"] = []

            resolved[name] = llmApiUtil.OpenAITool(
                function=llmApiUtil.OpenAIFunction(
                    name=name,
                    description=tool.description,
                    parameters=llmApiUtil.OpenAIFunctionParameter(**input_schema),
                )
            )

        self._tsp_tools = resolved
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from typing import Any, Optional

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


class _TspStdioClient:
    def __init__(self, command: list[str], request_timeout_sec: int = _DEFAULT_REQUEST_TIMEOUT_SEC):
        self.command = command
        self.request_timeout_sec = request_timeout_sec
        self.process: Optional[asyncio.subprocess.Process] = None
        self._in_flight: dict[str, asyncio.Future] = {}
        self._read_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None

    async def connect(self) -> None:
        self.process = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._read_task = asyncio.create_task(self._read_stdout_loop())
        self._stderr_task = asyncio.create_task(self._read_stderr_loop())

    async def disconnect(self) -> None:
        if self._read_task is not None:
            self._read_task.cancel()
            await asyncio.gather(self._read_task, return_exceptions=True)
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            await asyncio.gather(self._stderr_task, return_exceptions=True)
        self._read_task = None
        self._stderr_task = None

        if self.process is not None:
            try:
                self.process.terminate()
                await self.process.wait()
            except ProcessLookupError:
                pass
            except Exception:
                self.process.kill()
                await self.process.wait()
            finally:
                self.process = None

        self._fail_pending(RuntimeError("TSP connection closed"))

    async def initialize(
        self,
        protocol_version: str,
        include: Optional[list[str]] = None,
        exclude: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "protocolVersion": protocol_version,
            "clientInfo": {"name": "agent_team.tsp_driver"},
        }
        tools_capability: dict[str, list[str]] = {}
        if include:
            tools_capability["include"] = include
        if exclude:
            tools_capability["exclude"] = exclude
        if tools_capability:
            payload["capabilities"] = {"tools": tools_capability}
        return await self.request("initialize", payload)

    async def tool(self, tool_name: str, input_params: dict[str, Any]) -> dict[str, Any]:
        return await self.request("tool", input_params, tool=tool_name)

    async def shutdown(self) -> None:
        try:
            await self.request("shutdown", {})
        except Exception as e:
            logger.warning("TSP shutdown request failed: %s", e)

    async def request(self, method: str, input_params: dict[str, Any], tool: str | None = None) -> dict[str, Any]:
        if self.process is None or self.process.stdin is None:
            raise RuntimeError("TSP client is not connected")

        request_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._in_flight[request_id] = fut

        payload: dict[str, Any] = {
            "id": request_id,
            "method": method,
            "input": input_params,
        }
        if tool is not None:
            payload["tool"] = tool

        data = json.dumps(payload, ensure_ascii=False) + "\n"
        self.process.stdin.write(data.encode("utf-8"))
        await self.process.stdin.drain()

        try:
            response = await asyncio.wait_for(fut, timeout=self.request_timeout_sec)
        finally:
            self._in_flight.pop(request_id, None)

        if response.get("type") == "error":
            raise TeamAgentException(
                error_message=str(response.get("error", "unknown error")),
                error_code=str(response.get("code", "tsp/error")),
            )

        return response.get("result", {}) or {}

    async def _read_stdout_loop(self) -> None:
        try:
            while self.process is not None and self.process.stdout is not None:
                line = await self.process.stdout.readline()
                if not line:
                    break

                try:
                    msg = json.loads(line.decode("utf-8").strip())
                except json.JSONDecodeError:
                    logger.warning("TSP stdout JSON decode failed: %r", line)
                    continue

                if msg.get("type") == "event":
                    logger.debug("TSP event: %s", msg)
                    continue

                msg_id = msg.get("id")
                if msg_id is None:
                    logger.warning("TSP response without id: %s", msg)
                    continue

                msg_id = str(msg_id)
                future = self._in_flight.get(msg_id)
                if future is None or future.done():
                    logger.warning("TSP response for unknown id: %s", msg_id)
                    continue
                future.set_result(msg)
        except asyncio.CancelledError:
            pass
        finally:
            self._fail_pending(RuntimeError("TSP stdout closed"))

    async def _read_stderr_loop(self) -> None:
        try:
            while self.process is not None and self.process.stderr is not None:
                line = await self.process.stderr.readline()
                if not line:
                    break
                logger.debug("gtsp stderr: %s", line.decode("utf-8", errors="ignore").rstrip("\n"))
        except asyncio.CancelledError:
            pass

    def _fail_pending(self, exc: Exception) -> None:
        for future in self._in_flight.values():
            if not future.done():
                future.set_exception(exc)
        self._in_flight.clear()



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
    def __init__(self, host, config):
        super().__init__(host, config)
        self._client: Optional[_TspStdioClient] = None
        self._tsp_tools: dict[str, llmApiUtil.Tool] = {}
        _local = funcToolService.get_tools_by_names(_LOCAL_CHAT_TOOL_NAMES)
        self._local_tools: dict[str, llmApiUtil.Tool] = {t.function.name: t for t in _local}

    async def startup(self) -> None:
        options = self.config.options
        work_dir = str(options.get("workdir") or self.host.team_workdir)
        command = build_gtsp_command(options.get("command"), work_dir)

        timeout_sec = int(options.get("request_timeout_sec", _DEFAULT_REQUEST_TIMEOUT_SEC))
        include: Optional[list[str]] = options.get("tool_include") or None
        exclude: Optional[list[str]] = options.get("tool_exclude") or None

        client = _TspStdioClient(command=command, request_timeout_sec=timeout_sec)
        await client.connect()
        try:
            result = await client.initialize(protocol_version=_DEFAULT_PROTOCOL_VERSION, include=include, exclude=exclude)
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
            await self._client.disconnect()
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
                llmApiUtil.LlmApiMessage.text(llmApiUtil.OpenaiLLMApiRole.USER, _RUN_CHAT_TURN_HINT)
            )

    async def _run_until_reply(
        self,
        room: ChatRoom,
        tools: Optional[list[llmApiUtil.Tool]] = None,
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

    async def _execute_tool_calls(self, tool_calls: list[llmApiUtil.ToolCall]) -> bool:
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
                await self.host.append_history_message(llmApiUtil.LlmApiMessage.tool_result(tool_call_id, result_json))
                if function_name == "finish_chat_turn" and _is_tool_call_succeeded(result_json):
                    turn_done = True
                continue

            if function_name in self._tsp_tools:
                result_dict = await self._execute_tsp_tool(function_name, function_args)
                result_json = json.dumps(result_dict, ensure_ascii=False)
                await self.host.append_history_message(llmApiUtil.LlmApiMessage.tool_result(tool_call_id, result_json))
                continue

            result_json = json.dumps({"success": False, "message": f"未知工具: {function_name}"}, ensure_ascii=False)
            await self.host.append_history_message(llmApiUtil.LlmApiMessage.tool_result(tool_call_id, result_json))

        return turn_done

    async def _execute_tsp_tool(self, function_name: str, function_args: str) -> dict[str, Any]:
        assert self._client is not None, "TSP client 尚未初始化"
        try:
            parsed_args = json.loads(function_args)
        except json.JSONDecodeError as e:
            return {"success": False, "message": f"TSP 参数 JSON 解析失败: {e}"}

        try:
            result = await self._client.tool(function_name, parsed_args)
            if isinstance(result, dict):
                return result
            return {"success": True, "result": result}
        except TeamAgentException as e:
            return {"success": False, "code": e.error_code, "message": e.error_message}
        except Exception as e:
            return {"success": False, "message": f"TSP 工具调用失败: {e}"}

    def _load_tsp_tools(self, initialize_result: dict[str, Any]) -> None:
        capabilities = initialize_result.get("capabilities") or {}
        tools = capabilities.get("tools") or []
        resolved: dict[str, llmApiUtil.Tool] = {}

        for item in tools:
            assert isinstance(item, dict)
            assert (name := item["name"])

            input_schema = item.get("input_schema") or item.get("inputSchema") or {}
            assert isinstance(input_schema, dict)

            resolved[name] = llmApiUtil.Tool(
                function=llmApiUtil.Function(
                    name=name,
                    description=str(item.get("description", "")),
                    parameters=llmApiUtil.FunctionParameter(**input_schema),
                )
            )

        self._tsp_tools = resolved


def _is_tool_call_succeeded(result_json: str) -> bool:
    try:
        data = json.loads(result_json)
    except json.JSONDecodeError:
        return False
    return bool(data.get("success"))

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any, Awaitable, Callable

from constants import AgentHistoryTag
from service.roomService import ToolCallContext
from util import llmApiUtil

ToolHandler = Callable[[str, ToolCallContext], Awaitable[dict[str, Any]]]


@dataclass
class ToolExecutionResult:
    tool_call_id: str
    result_json: str
    success: bool | None = None
    error_message: str | None = None
    tags: list[AgentHistoryTag] | None = None
    turn_finished: bool = False


@dataclass
class RegisteredTool:
    tool: llmApiUtil.OpenAITool
    handler: ToolHandler
    marks_turn_finish: bool = False


class AgentToolRegistry:
    """管理当前轮次可用工具及其执行器。"""

    def __init__(self) -> None:
        self._tools_by_name: dict[str, RegisteredTool] = {}

    def clear(self) -> None:
        self._tools_by_name = {}

    def register(
        self,
        tool: llmApiUtil.OpenAITool,
        handler: ToolHandler,
        *,
        marks_turn_finish: bool = False,
    ) -> None:
        name = tool.function.name
        self._tools_by_name[name] = RegisteredTool(
            tool=tool,
            handler=handler,
            marks_turn_finish=marks_turn_finish,
        )

    def export_openai_tools(self) -> list[llmApiUtil.OpenAITool]:
        return [item.tool for item in self._tools_by_name.values()]

    async def execute_tool_call(self, tool_call: llmApiUtil.OpenAIToolCall, context: ToolCallContext) -> ToolExecutionResult:
        assert tool_call.function, "tool_call.function 不应为空"
        function = tool_call.function
        function_name = str(function.get("name", ""))
        assert function_name, "tool_call.function.name 不应为空"
        function_args = str(function.get("arguments", "{}"))
        assert tool_call.id, "tool_call.id 不应为空"
        tool_call_id = str(tool_call.id)

        registered = self._tools_by_name.get(function_name)
        if registered is None:
            result = {"success": False, "message": f"未知工具: {function_name}"}
            return ToolExecutionResult(
                tool_call_id=tool_call_id,
                result_json=json.dumps(result, ensure_ascii=False),
                success=False,
                error_message=str(result["message"]),
            )

        try:
            enriched_context = replace(context, tool_name=function_name)
            result = await registered.handler(function_args, enriched_context)
            assert isinstance(result, dict), f"tool result must be dict, got {type(result).__name__}"
        except Exception as e:
            result = {"success": False, "message": f"工具调用失败: {e}"}

        raw_success = result.get("success")
        success = None if raw_success is None else bool(raw_success)
        tool_succeeded = success is True
        error_message = None
        if success is False and result.get("message") is not None:
            error_message = str(result.get("message"))
        result_json = json.dumps(result, ensure_ascii=False)
        turn_finished = registered.marks_turn_finish
        tags = [AgentHistoryTag.ROOM_TURN_FINISH] if (turn_finished and tool_succeeded) else None
        return ToolExecutionResult(
            tool_call_id=tool_call_id,
            result_json=result_json,
            success=success,
            error_message=error_message,
            tags=tags,
            turn_finished=turn_finished,
        )

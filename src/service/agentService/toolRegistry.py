from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any, Awaitable, Callable, Iterable

from constants import ToolCategory
from service.roomService import ToolCallContext
from util import llmApiUtil, jsonUtil

ToolHandler = Callable[[str, ToolCallContext], Awaitable[dict[str, Any]]]

CATEGORY_CONFIG: dict[str, ToolCategory] = {
    # Local tools
    "get_time": ToolCategory.BASIC,
    "web_search": ToolCategory.READ,
    "get_dept_info": ToolCategory.BASIC,
    "get_room_info": ToolCategory.BASIC,
    "get_agent_info": ToolCategory.BASIC,
    "wake_up_agent": ToolCategory.BASIC,
    "start_chat": ToolCategory.BASIC,
    "send_chat_msg": ToolCategory.BASIC,
    "finish_action": ToolCategory.BASIC,
    "create_task": ToolCategory.BASIC,
    "update_task": ToolCategory.BASIC,
    "get_task": ToolCategory.BASIC,
    "list_tasks": ToolCategory.BASIC,
    "load_skill": ToolCategory.BASIC,
    "reload_team": ToolCategory.ADMIN,
    "list_role_templates": ToolCategory.ADMIN,
    "get_role_template": ToolCategory.ADMIN,
    "save_agent": ToolCategory.ADMIN,
    "save_dept": ToolCategory.ADMIN,
    "delete_dept": ToolCategory.ADMIN,
    "save_room": ToolCategory.ADMIN,
    "delete_room": ToolCategory.ADMIN,
    "save_role_template": ToolCategory.ADMIN,
    "delete_role_template": ToolCategory.ADMIN,
    # TSP tools
    "list_dir": ToolCategory.READ,
    "read_file": ToolCategory.READ,
    "write_file": ToolCategory.WRITE,
    "edit": ToolCategory.WRITE,
    "grep_search": ToolCategory.READ,
    "glob": ToolCategory.READ,
    "execute_bash": ToolCategory.EXECUTE,
    "process_output": ToolCategory.EXECUTE,
    "process_stop": ToolCategory.EXECUTE,
    "process_list": ToolCategory.EXECUTE,
}


def validate_tool_allow_specs(allow_specs: list[str]) -> str | None:
    """检查工具规格列表中是否包含 ADMIN 类别或工具。
    若包含，返回错误描述字符串；否则返回 None。用于在保存配置时进行拦截。
    """
    for spec in allow_specs:
        category = ToolCategory.from_spec(spec)
        if category == ToolCategory.ADMIN:
            return f"不允许分配管理员类别权限: {spec}"
        if CATEGORY_CONFIG.get(spec) == ToolCategory.ADMIN:
            return f"不允许分配管理员工具权限: {spec}"
    return None


def build_runtime_allow_specs(
    allowed_tools: list[str] | None,
    *,
    is_root_leader: bool,
) -> list[str]:
    """根据 allowed_tools 和角色构建实际生效的运行时工具规格列表。"""
    if allowed_tools is None:
        effective_specs = ["Category:Basic", "Category:Read", "Category:Write", "Category:Execute"]
    else:
        effective_specs = list(allowed_tools)

    # 强制包含基础类别
    if "Category:Basic" not in effective_specs:
        effective_specs.append("Category:Basic")

    if is_root_leader:
        if "Category:Admin" not in effective_specs:
            effective_specs.append("Category:Admin")
    
    return effective_specs


@dataclass
class ToolExecutionResult:
    tool_call_id: str
    result: dict[str, Any]
    success: bool = True
    error_message: str | None = None


@dataclass
class RegisteredTool:
    tool: llmApiUtil.OpenAITool
    handler: ToolHandler
    category: ToolCategory | None = None
    marks_turn_finish: bool = False
    self_interrupt: bool = False
    enabled: bool = True


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
        self_interrupt: bool = False,
    ) -> None:
        name = tool.function.name
        category = tool.category or CATEGORY_CONFIG.get(name)
        tool.category = category
        self._tools_by_name[name] = RegisteredTool(
            tool=tool,
            handler=handler,
            category=category,
            marks_turn_finish=marks_turn_finish,
            self_interrupt=self_interrupt,
        )

    def export_openai_tools(self) -> list[llmApiUtil.OpenAITool]:
        return [item.tool for item in self._tools_by_name.values() if item.enabled]

    def get_registered_tool(self, tool_name: str) -> RegisteredTool | None:
        return self._tools_by_name.get(tool_name)

    def list_enabled_tool_names(self) -> list[str]:
        return [name for name, item in self._tools_by_name.items() if item.enabled]

    def list_registered_tool_names(self) -> list[str]:
        return list(self._tools_by_name)

    def _set_enabled_tool_names(self, tool_names: list[str]) -> None:
        enabled_names = set(tool_names)
        for name, item in self._tools_by_name.items():
            item.enabled = name in enabled_names

    def resolve_enabled_tool_names(
        self,
        allow_specs: list[str],
    ) -> list[str]:
        """根据 allow_specs 解析出实际启用的工具名列表。"""
        ordered_names = list(self._tools_by_name)
        categories = set()
        explicit_names: set[str] = set()

        for spec in allow_specs:
            category = ToolCategory.from_spec(spec)
            if category is not None:
                categories.add(category)
                continue
            if spec in ordered_names:
                explicit_names.add(spec)

        resolved = []
        for name in ordered_names:
            registered = self._tools_by_name[name]
            if registered.category in categories:
                resolved.append(name)
            elif name in explicit_names:
                resolved.append(name)
        return resolved

    def apply_tool_allow_specs(self, allow_specs: list[str]) -> None:
        enabled_names = self.resolve_enabled_tool_names(allow_specs)
        self._set_enabled_tool_names(enabled_names)

    async def execute_tool_call(self, tool_call: llmApiUtil.OpenAIToolCall, context: ToolCallContext) -> ToolExecutionResult:
        tool_call.verify()
        function_name = tool_call.function_name
        function_args = tool_call.function_args
        tool_call_id = tool_call.tool_call_id

        registered = self._tools_by_name.get(function_name)
        if registered is None:
            result = {"success": False, "message": f"未知工具: {function_name}"}
            return ToolExecutionResult(
                tool_call_id=tool_call_id,
                result=result,
                success=False,
                error_message=str(result["message"]),
            )
        if registered.enabled is False:
            result = {"success": False, "message": f"工具无权限使用: {function_name}"}
            return ToolExecutionResult(
                tool_call_id=tool_call_id,
                result=result,
                success=False,
                error_message=str(result["message"]),
            )

        try:
            enriched_context = replace(context, tool_name=function_name)
            result = await registered.handler(function_args, enriched_context)
            assert isinstance(result, dict), f"tool result must be dict, got {type(result).__name__}"
            
            # 关键修复：使用 jsonUtil 确保结果是纯 JSON 类型（处理 datetime 等）
            try:
                result = jsonUtil.object_to_json_data(result)
            except Exception as e:
                result = {
                    "success": False, 
                    "message": f"工具返回结果处理失败 (Serialization Error): {e}"
                }
        except Exception as e:
            result = {"success": False, "message": f"工具调用失败: {e}"}

        raw_success = result.get("success")
        tool_succeeded = raw_success is not False
        error_message = None
        if not tool_succeeded and result.get("message") is not None:
            error_message = str(result.get("message"))
        return ToolExecutionResult(
            tool_call_id=tool_call_id,
            result=result,
            success=tool_succeeded,
            error_message=error_message,
        )

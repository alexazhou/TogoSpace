from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Any, Awaitable, Callable, Iterable

from constants import ToolCategory, ToolVisibility
from exception import ForwardToolCall
from toolResult import error_result
from service.roomService import ToolCallContext
from util import llmApiUtil, jsonUtil

logger = logging.getLogger(__name__)

ToolHandler = Callable[[str | dict[str, Any], ToolCallContext], Awaitable[dict[str, Any]]]

#: 转发链最大跳数（#160）。第 1 跳是入口工具 `console`，第 2 跳是它等价的原工具。
#: 写死 2 而不是可配：转发只允许「入口 → 原工具」这一段；更长的链说明实现已跑偏
#: （例如两个入口互相转发），此时报错比悄悄多跑几层安全。
MAX_FORWARD_HOPS = 2

CATEGORY_CONFIG: dict[str, ToolCategory] = {
    # Local tools
    "get_time": ToolCategory.BASIC,
    "web_search": ToolCategory.READ,
    "webfetch": ToolCategory.READ,
    "get_dept_info": ToolCategory.BASIC,
    "get_room_info": ToolCategory.BASIC,
    "get_agent_info": ToolCategory.BASIC,
    "wake_up_agent": ToolCategory.BASIC,
    "start_chat": ToolCategory.BASIC,
    "send_chat_msg": ToolCategory.BASIC,
    "finish_action": ToolCategory.BASIC,
    "create_task": ToolCategory.BASIC,
    "update_task": ToolCategory.BASIC,
    "get_task_detail": ToolCategory.BASIC,
    "list_task_events": ToolCategory.BASIC,
    "get_task_event": ToolCategory.BASIC,
    "list_tasks": ToolCategory.BASIC,
    "load_skill": ToolCategory.BASIC,
    # 管理控制台（#154）：注册为 BASIC，否则所有没有 Admin 的 Agent 连 `dept ls` /
    # `get_time` 这类只读查询都失去。这里的类别只决定「console 这个入口是否可用」，
    # **不决定「哪条子命令可用」**——子命令的权限由本模块 `execute_tool_call` 在转发
    # 后的第 2 跳按目标工具自己的 `enabled` 判定（#160：判定只有这一份实现）。
    "console": ToolCategory.BASIC,
    # 长期记忆工具：与任务/技能同属基础能力，默认对所有 Agent 生效
    "memory_list": ToolCategory.BASIC,
    "memory_read": ToolCategory.BASIC,
    "memory_search": ToolCategory.BASIC,
    "memory_create": ToolCategory.BASIC,
    "memory_update": ToolCategory.BASIC,
    # 容器沙箱工具：只对 TspAgentDriver 暴露（§6.1），全部归为执行类
    "create_container": ToolCategory.EXECUTE,
    "enter_container": ToolCategory.EXECUTE,
    "exit_to_host": ToolCategory.EXECUTE,
    "list_containers": ToolCategory.EXECUTE,
    "delete_container": ToolCategory.EXECUTE,
    # 改容器说明（#203）：由 `container note` 转发，模型面不可见（CONSOLE_ONLY）。
    # 分类照旧是执行类 —— 它改的是自己名下容器的元数据，落在同一权限域里；
    # 真正的「模型不可见」由 `funcToolService.core._CONTAINER_TOOL_VISIBILITY` 表达，
    # 这里只回答「要不要 Admin」。
    "update_container_note": ToolCategory.EXECUTE,
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
    "read_image": ToolCategory.READ,
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


def _execution_result(tool_call_id: str, result: dict[str, Any]) -> ToolExecutionResult:
    """把工具返回体包装成 `ToolExecutionResult`（成功判定与错误消息的唯一口径）。

    返回体**必须是 dict**，但签名上的 `dict` 只是注解，没有任何运行时强制力（#164 V7c）：
    `post_process` 是外部回调，返回 None / list / str 时，`result.get(...)` 会抛
    AttributeError —— 而这里在调用链的最外层、`try` 之外，一抛就是 turn 级崩溃。
    所以先判类型，坏形状降级成「结果格式异常」的失败返回体，口径与同函数族里
    「序列化失败」的处理一致：**工具级失败，不外抛**。
    """
    if not isinstance(result, dict):
        logger.warning(
            "工具返回体不是 dict: tool_call_id=%s, type=%s, value=%r",
            tool_call_id, type(result).__name__, result,
        )
        result = {
            "success": False,
            "message": f"工具结果格式异常: 期望 dict，实际 {type(result).__name__}",
        }

    tool_succeeded = result.get("success") is not False
    error_message = None
    if not tool_succeeded and result.get("message") is not None:
        error_message = str(result.get("message"))
    return ToolExecutionResult(
        tool_call_id=tool_call_id,
        result=result,
        success=tool_succeeded,
        error_message=error_message,
    )


@dataclass
class RegisteredTool:
    tool: llmApiUtil.OpenAITool
    handler: ToolHandler
    category: ToolCategory | None = None
    marks_turn_finish: bool = False
    self_interrupt: bool = False
    #: 动态自中断判定（#154 D6）：同一个工具「有时自中断、有时不自中断」时用它。
    #: 例如 `console` 只有路由到 `reload` 子命令才自中断，其余子命令都是普通返回；
    #: 这种情况静态 `self_interrupt` 表达不了 —— 它在注册时一次性定死，而这时
    #: 还不知道模型会传什么参数。判定器接收本次 tool_call，返回是否需要自中断。
    self_interrupt_predicate: Callable[[Any], bool] | None = None
    enabled: bool = True
    #: 静态基准可见性（`ToolVisibility`）。实际可见性按 D2 开关动态解析，见
    #: `effective_visibility`。
    visibility: ToolVisibility = ToolVisibility.TOOL_ONLY

    @property
    def effective_visibility(self) -> ToolVisibility:
        """当前环境下的实际可见性（动态读取 D2 开关）。

        延迟 import 避免 `toolRegistry` ↔ `funcToolService.core` 的模块级循环：
        本模块被 core 的调用方（Driver）使用，但 core 自己也 import console 等模块。
        """
        from service.funcToolService.core import resolve_effective_visibility

        return resolve_effective_visibility(self.tool.function.name, self.visibility)

    def triggers_self_interrupt(self, tool_call: Any) -> bool:
        """本次调用是否需要自中断（静态标志优先，其次看动态判定器）。"""
        if self.self_interrupt:
            return True
        if self.self_interrupt_predicate is None:
            return False
        return bool(self.self_interrupt_predicate(tool_call))


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
        self_interrupt_predicate: Callable[[Any], bool] | None = None,
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
            self_interrupt_predicate=self_interrupt_predicate,
            visibility=tool.visibility,
        )

    def export_openai_tools(self) -> list[llmApiUtil.OpenAITool]:
        """模型暴露面：`enabled` 且 `tool_visible`（D2 隐藏模式下收敛工具不出现）。"""
        return [
            item.tool for item in self._tools_by_name.values()
            if item.enabled and item.effective_visibility.tool_visible
        ]

    def get_registered_tool(self, tool_name: str) -> RegisteredTool | None:
        return self._tools_by_name.get(tool_name)

    def list_enabled_tool_names(self) -> list[str]:
        """权限面：`enabled` 的工具名（不按可见性过滤）。"""
        return [name for name, item in self._tools_by_name.items() if item.enabled]

    def list_exposed_tool_names(self) -> list[str]:
        """模型暴露面：`enabled` 且 `tool_visible` 的工具名（Claude SDK MCP 用）。"""
        return [
            name for name, item in self._tools_by_name.items()
            if item.enabled and item.effective_visibility.tool_visible
        ]

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
        """执行一次工具调用；工具可抛 `ForwardToolCall` 把调用转交给另一个工具（#160）。

        转发循环存在的理由：`console` 的每条子命令都等价于一个原工具（`save_agent`
        / `get_dept_info` …）。改造前它自己重算一遍权限（`consoleParsing._assert_permitted`），
        于是同一个判定有了两份实现，一旦 `CATEGORY_CONFIG` 或运行时 spec 口径变化，
        就会出现「原工具调不了、console 却能调」（或反过来）。改造后 console 只抛
        `ForwardToolCall`，**鉴权与执行都回到这里**，只有一份实现。

        循环上限固定 2 跳（`MAX_FORWARD_HOPS`）：第 1 跳是入口工具（`console`），
        第 2 跳是它等价的原工具。第 2 跳再抛说明实现跑偏，直接返回明确错误，
        绝不继续追——否则一个自指转发就是死循环。

        每一跳都重新做 `registered` / `enabled` 检查，所以「转发目标」与「直接调用」
        走的是同一条权限判定，这正是本设计要的性质。

        `post_process` 回调在**转发成功后**按逆序应用（后转发的先处理）：它只用于
        「依赖目标工具返回体」的本地逻辑（`rm` 把 impact 并回结果、`agent get` 的
        id 一致性守卫），**不做权限判定**——鉴权在上面的循环里已经完成。
        """
        tool_call.verify()
        tool_call_id = tool_call.tool_call_id
        function_name = tool_call.function_name
        # 显式声明为 `str | dict`：第 1 跳从 tool_call 取到的是 JSON 字符串，第 2 跳
        # 起被下面的 `forward.tool_args` 换成 dict（省掉一次 json 往返）。handler 契约
        # 就是 `str | dict`（见 `ToolHandler`），这里不声明就只剩 `str`，转发那一行
        # 会与声明冲突（#205 修的 mypy error；只加宽局部声明，运行时行为不变）。
        function_args: str | dict[str, Any] = tool_call.function_args

        post_processors: list[Callable[[dict[str, Any]], dict[str, Any]]] = []

        for hop in range(MAX_FORWARD_HOPS):
            # 每一跳都按当前 function_name 重新查注册表：转发目标与直接调用同一条判定。
            registered = self._tools_by_name.get(function_name)
            if registered is None:
                return _execution_result(
                    tool_call_id, {"success": False, "message": f"未知工具: {function_name}"}
                )
            if registered.enabled is False:
                return _execution_result(
                    tool_call_id, {"success": False, "message": f"工具无权限使用: {function_name}"}
                )

            # 可见性按跳数校验（与 `enabled` 正交）：第 1 跳是模型直接发起，要求该工具
            # 出现在模型暴露面；第 2 跳是 console 转发，要求目标工具对 console 可见。
            # 隐藏模式下已收敛工具是 CONSOLE_ONLY：模型直调被拒，console 转发仍可达。
            visibility = registered.effective_visibility
            if hop == 0 and not visibility.tool_visible:
                return _execution_result(
                    tool_call_id,
                    {
                        "success": False,
                        "message": (
                            f"工具不能直接调用，请通过 console 命令使用: {function_name}"
                        ),
                    },
                )
            if hop > 0 and not visibility.console_visible:
                return _execution_result(
                    tool_call_id,
                    {"success": False, "message": f"工具不支持通过 console 调用: {function_name}"},
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
                        "message": f"工具返回结果处理失败 (Serialization Error): {e}",
                    }
            except ForwardToolCall as forward:
                if forward.tool_name == "console":
                    # 自指死循环的唯一入口：console 的某条子命令又转发回 console 自己。
                    # 必须在**执行前**拒绝，否则第 2 跳会再进 console、再抛、被跳数上限兜住，
                    # 错误信息变成「链过长」，把实现缺陷伪装成配置问题。
                    return _execution_result(
                        tool_call_id,
                        {
                            "success": False,
                            "message": f"工具转发被拒绝: {function_name} 不允许转发到 console 自身",
                        },
                    )
                if hop + 1 >= MAX_FORWARD_HOPS:
                    return _execution_result(
                        tool_call_id,
                        {
                            "success": False,
                            "message": (
                                f"工具转发链过长（最多 {MAX_FORWARD_HOPS} 跳）: "
                                f"{function_name} -> {forward.tool_name}"
                            ),
                        },
                    )
                if forward.post_process is not None:
                    post_processors.append(forward.post_process)
                function_name = forward.tool_name
                # 参数字典由转发方给出（**不含** `_context`：它由 run_tool_call 按当前
                # 上下文注入）。直接以 dict 交给下一跳，省掉一次 `json.dumps` →
                # `json.loads` 往返；handler 契约是 `str | dict`，模型侧仍传 str。
                function_args = forward.tool_args
                continue
            except Exception as e:
                # 这是 handler 之外的兜底（`run_tool_call` 已把自身异常转成返回体，能到这里的
                # 是取 schema / 注入上下文时的意外，或第三方 handler 自己抛的）。
                # #189：形状与 error_code 口径收敛到 `toolResult.error_result`，不再自己拼；
                # 「工具调用失败:」前缀保留 —— 它是既有对外文案（`test_tool_registry` 有断言）。
                result = error_result(e, prefix="工具调用失败: ")

            # 正常返回：把转发链上积攒的后处理按**逆序**应用（后转发的先处理）。
            #
            # 为什么要在这里兜 `except`（#164）：`post_process` 是外部回调，抛异常时
            # 原来会穿透 `execute_tool_call` → `_run_tool_to_item` → `_advance_step` →
            # `_run_turn_loop` → `run_task_turn`（只捕 CancelledError）→ `jobConsumer`
            # 的 `except Exception`，结果是 **job FAILED + agent 置 FAILED**，属 turn 级
            # 崩溃而非工具级失败。更糟的是崩在 `finalize_history_item` 之前，TOOL 条目
            # 永久停在 INIT，续跑时会命中 `_advance_step` 的 TOOL/INIT 分支把**同一个
            # tool_call 重放一遍**（实测 1→2，非幂等命令 = 重复副作用）。
            # 降级成工具失败返回后：条目正常 finalize 成 FAILED，而 FAILED 的 tool
            # 不重试（`_advance_step` 的 FAILED/CANCELLED 分支），执行次数停在 1。
            for post_process in reversed(post_processors):
                # 入参侧只留证据不强制（#164 C）：入参是上一步的产出，真出问题在
                # 返回值侧兜；这里若 raise 就又制造了一个外抛点，与本次修复目的相反。
                if not isinstance(result, dict):
                    logger.debug(
                        "post_process 入参不是 dict: tool_call_id=%s, type=%s",
                        tool_call_id, type(result).__name__,
                    )
                try:
                    result = post_process(result)
                except Exception as e:  # noqa: BLE001 —— 外部回调，任何异常都只能降级
                    logger.warning(
                        "工具结果后处理失败: tool_call_id=%s, tool=%s, post_process=%r, error=%r",
                        tool_call_id, function_name, post_process, e,
                    )
                    result = {"success": False, "message": f"工具结果后处理失败: {e}"}
                    # 后续 post_process 面对的是失败返回体，继续应用只会叠加误导信息。
                    break
            return _execution_result(tool_call_id, result)

        # 理论不可达：循环体内每条路径都 return 或 continue，且 continue 只在
        # `hop + 1 < MAX_FORWARD_HOPS` 时发生。
        raise AssertionError(f"转发循环异常退出: tool={function_name}, hops={MAX_FORWARD_HOPS}")


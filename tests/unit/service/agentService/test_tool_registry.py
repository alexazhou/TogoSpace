import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from exception import ForwardToolCall
from constants import ToolCategory
from service.agentService.toolRegistry import (
    CATEGORY_CONFIG,
    AgentToolRegistry,
    validate_tool_allow_specs,
    build_runtime_allow_specs,
)
from service.roomService import ToolCallContext
from util import llmApiUtil

def _register_tools(registry: AgentToolRegistry, *names: str) -> AsyncMock:
    handler = AsyncMock(return_value={"success": True})
    for name in names:
        # 直接构造 OpenAITool
        tool = llmApiUtil.OpenAITool(
            function=llmApiUtil.OpenAIFunction(
                name=name,
                description="",
                parameters=llmApiUtil.OpenAIFunctionParameter(type="object", properties={}, required=[])
            )
        )
        registry.register(tool, handler, marks_turn_finish=name == "finish_action")
    return handler

def test_validate_tool_allow_specs() -> None:
    # 正常情况 (list_dir 是 READ)
    assert validate_tool_allow_specs(["Category:Read", "list_dir"]) is None
    # 包含 ADMIN 类别
    assert "不允许分配管理员类别权限" in validate_tool_allow_specs(["Category:Admin"])
    # 包含 ADMIN 工具
    assert "不允许分配管理员工具权限" in validate_tool_allow_specs(["save_role_template"])

def test_build_runtime_allow_specs() -> None:
    # 默认权限
    specs = build_runtime_allow_specs(None, is_root_leader=False)
    assert set(specs) == {"Category:Basic", "Category:Read", "Category:Write", "Category:Execute"}
    
    # 指定权限，应自动补齐 Basic
    # list_dir 是 READ
    specs = build_runtime_allow_specs(["list_dir"], is_root_leader=False)
    assert set(specs) == {"list_dir", "Category:Basic"}
    
    # Root Leader 自动补齐 Admin 和 Basic
    specs = build_runtime_allow_specs(None, is_root_leader=True)
    assert "Category:Admin" in specs
    assert "Category:Basic" in specs

def test_registry_register_and_list() -> None:
    registry = AgentToolRegistry()
    # list_dir 是 READ, send_chat_msg 是 BASIC
    _register_tools(registry, "list_dir", "send_chat_msg")
    
    assert set(registry.list_registered_tool_names()) == {"list_dir", "send_chat_msg"}
    assert set(registry.list_enabled_tool_names()) == {"list_dir", "send_chat_msg"}
    
    tool = registry.get_registered_tool("list_dir")
    assert tool.category == ToolCategory.READ
    assert tool.marks_turn_finish is False

def test_registry_clear() -> None:
    registry = AgentToolRegistry()
    _register_tools(registry, "list_dir")
    registry.clear()
    assert registry.list_registered_tool_names() == []

def test_apply_tool_allow_specs() -> None:
    registry = AgentToolRegistry()
    _register_tools(registry, "list_dir", "send_chat_msg", "save_role_template")
    
    # 仅开启 Basic
    registry.apply_tool_allow_specs(["Category:Basic"])
    # send_chat_msg 是 BASIC, list_dir 是 READ
    assert registry.list_enabled_tool_names() == ["send_chat_msg"]
    
    # 仅开启指定工具
    registry.apply_tool_allow_specs(["list_dir"])
    assert registry.list_enabled_tool_names() == ["list_dir"]
    
    # 开启 Read 和具体工具
    registry.apply_tool_allow_specs(["Category:Read", "save_role_template"])
    assert set(registry.list_enabled_tool_names()) == {"list_dir", "save_role_template"}

def test_read_image_categorized_and_enabled_under_read() -> None:
    """read_image 应归入 READ 类别，Category:Read 策略下被启用（防止漏配 CATEGORY_CONFIG）。"""
    registry = AgentToolRegistry()
    _register_tools(registry, "read_image", "read_file")
    registry.apply_tool_allow_specs(["Category:Read"])
    assert "read_image" in registry.list_enabled_tool_names()


def test_basic_category_has_no_get_task() -> None:
    """#71：废弃别名 get_task 已删除，Category:Basic 与注册表都不再出现它，正式工具仍在。"""
    from service.funcToolService import core

    core.load_func_tools()
    registry = AgentToolRegistry()
    for tool in core.get_tools():
        registry.register(tool, lambda args_str, ctx: None)

    basic_names = registry.resolve_enabled_tool_names(["Category:Basic"])

    assert "get_task" not in basic_names
    assert "get_task_detail" in basic_names
    assert "list_task_events" in basic_names
    # 别名已彻底移除：不再可被注册表解析，也不在任何类别里
    assert "get_task" not in registry.list_registered_tool_names()
    assert "get_task" not in registry.resolve_enabled_tool_names(["get_task"])
    assert "get_task" not in registry.resolve_enabled_tool_names(["Category:Read"])


@pytest.mark.asyncio
async def test_execute_tool_call_success() -> None:
    registry = AgentToolRegistry()

    # 显式定义的 handler，方便断言参数
    async def mock_handler(args_str: str, context: ToolCallContext) -> dict:
        # 不要返回 context 对象，会引起 jsonUtil 递归（尤其是 mock 对象）
        return {"success": True, "time": "12:00", "handled_tool": context.tool_name}

    tool = llmApiUtil.OpenAITool(
        function=llmApiUtil.OpenAIFunction(
            name="list_dir",
            description="",
            parameters=llmApiUtil.OpenAIFunctionParameter(type="object", properties={}, required=[])
        )
    )
    registry.register(tool, mock_handler)

    mock_room = MagicMock()
    ctx = ToolCallContext(agent_id=1, team_id=1, chat_room=mock_room)

    result = await registry.execute_tool_call(
        llmApiUtil.OpenAIToolCall(id="tc_1", function={"name": "list_dir", "arguments": "{}"}),
        context=ctx
    )

    assert result.success is True
    assert result.result["time"] == "12:00"
    assert result.result["handled_tool"] == "list_dir"


@pytest.mark.asyncio
async def test_execute_tool_call_unknown_tool() -> None:
    registry = AgentToolRegistry()
    ctx = ToolCallContext(agent_id=1, team_id=1, chat_room=MagicMock())
    result = await registry.execute_tool_call(
        llmApiUtil.OpenAIToolCall(id="tc_1", function={"name": "unknown", "arguments": "{}"}),
        context=ctx
    )
    assert result.success is False
    assert "未知工具" in result.error_message

@pytest.mark.asyncio
async def test_execute_tool_call_disabled_tool() -> None:
    registry = AgentToolRegistry()
    _register_tools(registry, "list_dir")
    registry.apply_tool_allow_specs([]) # 全部禁用
    
    ctx = ToolCallContext(agent_id=1, team_id=1, chat_room=MagicMock())
    result = await registry.execute_tool_call(
        llmApiUtil.OpenAIToolCall(id="tc_1", function={"name": "list_dir", "arguments": "{}"}),
        context=ctx
    )
    assert result.success is False
    assert "工具无权限使用" in result.error_message

@pytest.mark.asyncio
async def test_execute_tool_call_exception() -> None:
    registry = AgentToolRegistry()
    handler = _register_tools(registry, "list_dir")
    handler.side_effect = Exception("boom")
    
    ctx = ToolCallContext(agent_id=1, team_id=1, chat_room=MagicMock())
    result = await registry.execute_tool_call(
        llmApiUtil.OpenAIToolCall(id="tc_1", function={"name": "list_dir", "arguments": "{}"}),
        context=ctx
    )
    assert result.success is False
    assert "工具调用失败" in result.result["message"]


# ---------------------------------------------------------------------------
# #160：转发（`ForwardToolCall`）——console 的鉴权与执行回到唯一入口
#
# 这几条为什么必须存在：console 改造后「自己的权限实现」被删掉了，正确性完全押在
# 转发循环上。三种典型错法都不会让 console 的单测变红：
#   ① 循环只跑 1 跳 ⇒ 转发目标根本没执行，console 变成「永远返回空」；
#   ② 没有「目标是 console 自身」的拒绝 ⇒ 自指转发被跳数上限兜住，报错变成
#      「链过长」，把实现缺陷伪装成配置问题；
#   ③ 第 2 跳不查 enabled ⇒ console 成为绕过权限的后门（本单最严重的一种）。
# ---------------------------------------------------------------------------


def _register_tool(registry: AgentToolRegistry, name: str, handler) -> None:
    registry.register(
        llmApiUtil.OpenAITool(
            function=llmApiUtil.OpenAIFunction(
                name=name,
                description="",
                parameters=llmApiUtil.OpenAIFunctionParameter(
                    type="object", properties={}, required=[]
                ),
            )
        ),
        handler,
    )


def _ctx() -> ToolCallContext:
    return ToolCallContext(agent_id=1, team_id=1, chat_room=MagicMock())


def _call(name: str, args: dict | None = None) -> llmApiUtil.OpenAIToolCall:
    return llmApiUtil.OpenAIToolCall(
        id="tc_forward", function={"name": name, "arguments": json.dumps(args or {})}
    )


@pytest.mark.asyncio
async def test_forward_executes_target_tool_with_forwarded_args() -> None:
    """第 1 跳抛 `ForwardToolCall` ⇒ 第 2 跳必须真的执行目标工具（参数原样过去）。"""
    registry = AgentToolRegistry()
    seen: dict = {}

    async def console_like(args_str: str, context: ToolCallContext) -> dict:
        raise ForwardToolCall("get_time", {"timezone": "UTC"})

    async def target(args_str: str, context: ToolCallContext) -> dict:
        seen["args"] = json.loads(args_str)
        # 上下文里的 tool_name 也必须切到目标工具：`run_tool_call` 靠它选函数。
        seen["tool_name"] = context.tool_name
        return {"success": True, "message": "utc"}

    _register_tool(registry, "console", console_like)
    _register_tool(registry, "get_time", target)

    result = await registry.execute_tool_call(_call("console", {"command": "time get"}), _ctx())

    assert result.success is True
    assert result.result["message"] == "utc"
    assert seen == {"args": {"timezone": "UTC"}, "tool_name": "get_time"}


@pytest.mark.asyncio
async def test_forward_applies_post_process_to_target_result() -> None:
    """`post_process` 必须被应用到**目标工具的返回体**上（`rm` 的 impact 靠它回来）。"""
    registry = AgentToolRegistry()

    async def console_like(args_str: str, context: ToolCallContext) -> dict:
        raise ForwardToolCall(
            "delete_room",
            {"name": "r1"},
            post_process=lambda r: {**r, "impact": ["房间 r1"]},
        )

    _register_tool(registry, "console", console_like)
    _register_tool(registry, "delete_room", lambda *_: _ok({"deleted": True}))

    result = await registry.execute_tool_call(_call("console"), _ctx())

    assert result.success is True
    assert result.result == {"success": True, "deleted": True, "impact": ["房间 r1"]}


async def _ok(payload: dict) -> dict:
    return {"success": True, **payload}


@pytest.mark.asyncio
async def test_forward_to_console_itself_is_rejected_before_execution() -> None:
    """目标为 `console` 自身 ⇒ 直接拒绝，**不得**进第 2 跳（否则是自指死循环）。"""
    registry = AgentToolRegistry()
    hops: list[str] = []

    async def console_like(args_str: str, context: ToolCallContext) -> dict:
        hops.append(context.tool_name or "")
        raise ForwardToolCall("console", {"command": "help"})

    _register_tool(registry, "console", console_like)

    result = await registry.execute_tool_call(_call("console"), _ctx())

    assert result.success is False
    assert "不允许转发到 console 自身" in result.result["message"]
    assert hops == ["console"], f"只应跑第 1 跳，实际跑了 {hops}"


@pytest.mark.asyncio
async def test_forward_chain_longer_than_two_hops_is_rejected() -> None:
    """两次转发 ⇒ 明确报「链过长」（而不是无限循环或静默成功）。"""
    registry = AgentToolRegistry()

    async def first(args_str: str, context: ToolCallContext) -> dict:
        raise ForwardToolCall("second", {})

    async def second(args_str: str, context: ToolCallContext) -> dict:
        raise ForwardToolCall("third", {})

    _register_tool(registry, "first", first)
    _register_tool(registry, "second", second)
    _register_tool(registry, "third", lambda *_: _ok({}))

    result = await registry.execute_tool_call(_call("first"), _ctx())

    assert result.success is False
    assert "转发链过长" in result.result["message"]


@pytest.mark.asyncio
async def test_forward_target_respects_enabled_flag() -> None:
    """**本单的核心性质**：转发目标被禁用时同样拒绝——转发不能成为绕权限的后门。"""
    registry = AgentToolRegistry()
    executed: list[str] = []

    async def console_like(args_str: str, context: ToolCallContext) -> dict:
        raise ForwardToolCall("save_agent", {"name": "x", "role_template_name": "r"})

    async def save_agent(args_str: str, context: ToolCallContext) -> dict:
        executed.append(context.tool_name or "")
        return {"success": True}

    _register_tool(registry, "console", console_like)
    _register_tool(registry, "save_agent", save_agent)
    # console 是 BASIC；只有它活着，才证明拒绝来自**第 2 跳**而不是入口就被挡。
    registry.apply_tool_allow_specs(["Category:Basic"])

    result = await registry.execute_tool_call(_call("console"), _ctx())

    assert result.success is False
    assert "工具无权限使用: save_agent" in result.result["message"]
    assert executed == [], f"被拒的目标不得执行，实际执行了 {executed}"


# ---------------------------------------------------------------------------
# #75：CATEGORY_CONFIG 分类归属的字面量契约
# ---------------------------------------------------------------------------

# 分类表（37 条 = tools.py 本地 26 + TSP 11），**刻意写死字面量**：
# 分类归属决定 `Category:*` 展开出哪些工具（见 `build_runtime_allow_specs`），
# 把 `update_task` 挪出 BASIC 等于悄悄收走 Agent 的核心协作能力（`Category:Basic`
# 下模型就看不到任务工具了），而 tool schema 快照对此完全沉默。故这里把归属
# 变成显式契约，让「归错 / 漏登记 / 多残留」都以代码 diff 的形式出现在 review 里。
#
# 分布（按 `ToolCategory.name` 统计，**不要按 `.value` 反推** ——
# 枚举声明序是 ADMIN=1 / BASIC=2 / READ=3 / WRITE=4 / EXECUTE=5）：
#   BASIC 16 / ADMIN 10 / READ 6 / EXECUTE 4 / WRITE 2
#   （#154 起 `console` 计入 BASIC：它只是入口；#160 起子命令权限由
#    `execute_tool_call` 在转发后的第 2 跳按**目标工具自己的** category 判）
EXPECTED_TOOL_CATEGORIES = {
    # 管理控制台（#154）：注册为 BASIC 才不会让没有 Admin 的 Agent 连 `dept ls`
    # 都失去。**子命令级权限由 `execute_tool_call` 在转发后的第 2 跳按目标工具的
    # enabled 判**（#160：`consoleParsing._assert_permitted` 已删除，判定只有一份实现）
    # —— 这一行不代表「console 的子命令都是 BASIC」。
    "console": ToolCategory.BASIC,
    "create_container": ToolCategory.EXECUTE,
    "create_task": ToolCategory.BASIC,
    "delete_container": ToolCategory.EXECUTE,
    "delete_dept": ToolCategory.ADMIN,
    "delete_role_template": ToolCategory.ADMIN,
    "delete_room": ToolCategory.ADMIN,
    "edit": ToolCategory.WRITE,
    "enter_container": ToolCategory.EXECUTE,
    "execute_bash": ToolCategory.EXECUTE,
    "exit_to_host": ToolCategory.EXECUTE,
    "finish_action": ToolCategory.BASIC,
    "get_agent_info": ToolCategory.BASIC,
    "get_dept_info": ToolCategory.BASIC,
    "get_role_template": ToolCategory.ADMIN,
    "get_room_info": ToolCategory.BASIC,
    "get_task_detail": ToolCategory.BASIC,
    "get_task_event": ToolCategory.BASIC,
    "get_time": ToolCategory.BASIC,
    "glob": ToolCategory.READ,
    "grep_search": ToolCategory.READ,
    "list_dir": ToolCategory.READ,
    "list_containers": ToolCategory.EXECUTE,
    "list_role_templates": ToolCategory.ADMIN,
    "list_task_events": ToolCategory.BASIC,
    "list_tasks": ToolCategory.BASIC,
    "load_skill": ToolCategory.BASIC,
    # 长期记忆工具（#134）：与任务 / 技能同属基础能力，默认对所有 Agent 生效
    "memory_create": ToolCategory.BASIC,
    "memory_list": ToolCategory.BASIC,
    "memory_read": ToolCategory.BASIC,
    "memory_search": ToolCategory.BASIC,
    "memory_update": ToolCategory.BASIC,
    "process_list": ToolCategory.EXECUTE,
    "process_output": ToolCategory.EXECUTE,
    "process_stop": ToolCategory.EXECUTE,
    "read_file": ToolCategory.READ,
    "read_image": ToolCategory.READ,
    "reload_team": ToolCategory.ADMIN,
    "save_agent": ToolCategory.ADMIN,
    "save_dept": ToolCategory.ADMIN,
    "save_role_template": ToolCategory.ADMIN,
    "save_room": ToolCategory.ADMIN,
    "send_chat_msg": ToolCategory.BASIC,
    "start_chat": ToolCategory.BASIC,
    "update_task": ToolCategory.BASIC,
    "wake_up_agent": ToolCategory.BASIC,
    "web_search": ToolCategory.READ,
    "write_file": ToolCategory.WRITE,
}


def test_category_config_matches_expected_contract() -> None:
    """`CATEGORY_CONFIG` 必须与字面量契约**双向相等**。

    用 dict 相等而不是逐个 lookup：单向查表会放过「新增工具漏登记」（期望表里没有
    就不会被检查），而漏登记正是最容易悄悄发生的漂移。dict 相等把「漏登记」
    「多残留」「类别改错」三种情况一次拦住，26/37 条的规模下 diff 依然可读。
    """
    assert dict(CATEGORY_CONFIG) == EXPECTED_TOOL_CATEGORIES
    assert set(CATEGORY_CONFIG) == set(EXPECTED_TOOL_CATEGORIES)


# ---------------------------------------------------------------------------
# #164：post_process 是外部回调，抛异常/返回非 dict 都不得穿透 execute_tool_call
#
# 缺陷原状：`for post_process in reversed(...)` 与 `_execution_result(...)` 都在
# `try` 块**之外**（`except Exception` 早已闭合），因此回调一抛异常就穿透本函数，
# 一路冒到 `jobConsumer` 的 `except Exception` ⇒ job FAILED + agent 置 FAILED。
# 这不是「工具调用失败」，是 **turn 级崩溃**；更糟的是崩在 `finalize_history_item`
# 之前，TOOL 条目停在 INIT，续跑会把同一个 tool_call 再执行一遍（#163 V7b 实测 1→2）。
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_process_exception_is_downgraded_not_propagated() -> None:
    """post_process 抛异常 ⇒ `execute_tool_call` **正常返回**失败体，异常不外抛（P1）。

    这是本修复的全部意义：把 turn 级崩溃降级成工具级失败。若异常还能穿透，
    下面的 `await` 就会直接把异常抛给用例——断言「能走到 assert」本身就是判据。
    """
    registry = AgentToolRegistry()

    def _boom(_result: dict) -> dict:
        raise RuntimeError("#164: post_process 故意抛异常")

    async def console_like(args_str: str, context: ToolCallContext) -> dict:
        raise ForwardToolCall("delete_room", {"name": "r1"}, post_process=_boom)

    _register_tool(registry, "console", console_like)
    _register_tool(registry, "delete_room", lambda *_: _ok({"deleted": True}))

    result = await registry.execute_tool_call(_call("console"), _ctx())

    assert result.success is False, f"后处理失败必须降级成工具失败：{result}"
    assert result.result["success"] is False
    assert "工具结果后处理失败" in result.result["message"]
    assert result.error_message is not None, "失败体必须带 error_message（finalize 要写库）"


@pytest.mark.asyncio
async def test_post_process_exception_stops_remaining_processors(monkeypatch) -> None:
    """一个 post_process 抛异常后，**后续 post_process 不再应用**（break 语义）。

    继续应用会拿「失败返回体」去当正常结果处理（例如 `{**r, "impact": ...}` 会把
    impact 叠到错误体上），最终返回体既像失败又带业务字段，误导模型。

    **为什么要 monkeypatch 跳数上限**：`post_processors` 只在 `continue`（成功转发）
    时入栈，而第 2 跳就撞上 `hop + 1 >= MAX_FORWARD_HOPS` 早退——所以默认 2 跳下
    **最多只有 1 个 post_process 能进栈**，`break` 分支根本不可达。本用例原先把链
    写成 3 跳、不 patch 上限，结果 `_boom` 压根没入栈（返回的是「转发链过长」），
    **在修复前的代码上也是绿的**——假绿。改成 3 跳后才真正咬住：修复前 `_boom` 的
    RuntimeError 直接穿透（本用例 ERROR 在 `execute_tool_call` 那行），修复后绿。
    这也顺带证明 `MAX_FORWARD_HOPS` 若调大，break 语义仍然正确。
    """
    from service.agentService import toolRegistry as tool_registry_module

    monkeypatch.setattr(tool_registry_module, "MAX_FORWARD_HOPS", 3)

    registry = AgentToolRegistry()
    applied: list[str] = []

    def _boom(_result: dict) -> dict:
        raise RuntimeError("boom")

    def _after(result: dict) -> dict:
        applied.append("after")
        return {**result, "should_not_appear": True}

    async def inner(args_str: str, context: ToolCallContext) -> dict:
        # 第 1 跳转发给 outer，outer 再转发给真实目标：制造两级 post_process。
        raise ForwardToolCall("mid", {"x": 1}, post_process=_after)

    async def mid(args_str: str, context: ToolCallContext) -> dict:
        raise ForwardToolCall("delete_room", {"name": "r1"}, post_process=_boom)

    _register_tool(registry, "console", inner)
    _register_tool(registry, "mid", mid)
    _register_tool(registry, "delete_room", lambda *_: _ok({"deleted": True}))

    result = await registry.execute_tool_call(_call("console"), _ctx())

    # 逆序应用 ⇒ 先 _boom，_after 不该被调用。
    assert applied == [], f"异常之后的 post_process 不得再应用：{applied}"
    assert result.success is False
    assert "should_not_appear" not in result.result


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_return", [None, "str", [], 123], ids=["None", "str", "list", "int"])
async def test_post_process_returning_non_dict_does_not_raise_attribute_error(bad_return) -> None:
    """post_process 返回非 dict ⇒ 降级成失败体，**不得** AttributeError 穿透（P3）。

    原状：`_execution_result` 第一行 `result.get("success")` 直接 AttributeError
    （`AttributeError: 'NoneType' object has no attribute 'get'`），同在 try 之外。
    `Callable[[dict], dict]` 的返回值类型只是注解，没有任何运行时强制力。
    """
    registry = AgentToolRegistry()

    async def console_like(args_str: str, context: ToolCallContext) -> dict:
        raise ForwardToolCall(
            "delete_room", {"name": "r1"}, post_process=lambda _r: bad_return
        )

    _register_tool(registry, "console", console_like)
    _register_tool(registry, "delete_room", lambda *_: _ok({"deleted": True}))

    result = await registry.execute_tool_call(_call("console"), _ctx())

    assert result.success is False
    assert result.result["success"] is False
    assert "工具结果格式异常" in result.result["message"]


@pytest.mark.asyncio
async def test_execution_result_wraps_non_dict_directly() -> None:
    """`_execution_result` 自身对非 dict 兜底（B 的第一道闸，不依赖调用点）。

    直接测这个私有函数是必要的：`execute_tool_call` 里 `assert isinstance(result, dict)`
    只覆盖 handler 那一段，post_process 之后没有再校验；而 `_execution_result` 是
    **所有**返回路径的收口，把校验放这里才保证「任何来源的坏形状都挡住了」。
    """
    from service.agentService.toolRegistry import _execution_result

    for bad in (None, [], "x", 1, object()):
        wrapped = _execution_result("tc", bad)  # type: ignore[arg-type]
        assert wrapped.success is False
        assert wrapped.result["success"] is False
        assert "工具结果格式异常" in wrapped.result["message"]

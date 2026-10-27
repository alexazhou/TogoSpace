"""#67：任务工具签名与工具面（去 `result`/`block_reason`、`new_status`、`get_task_detail`、`list_task_events`）。"""

from __future__ import annotations

import inspect
from typing import Optional

import pytest

from service.funcToolService import tools
from service.funcToolService.funcToolType import get_function_metadata


def test_update_task_signature_uses_new_status_and_note():
    """update_task 入参 = task_id / new_status / note；无 result / block_reason / status。"""
    params = list(inspect.signature(tools.update_task).parameters)
    assert params == ["task_id", "new_status", "note", "_context"]
    assert "result" not in params
    assert "block_reason" not in params
    assert "status" not in params


def test_update_task_rejects_unknown_param_result():
    """无兼容别名：传旧字段 result 直接报错（TypeError）。"""
    with pytest.raises(TypeError):
        tools.update_task(task_id=1, result="x")  # type: ignore[call-arg]


def test_update_task_rejects_unknown_param_block_reason():
    with pytest.raises(TypeError):
        tools.update_task(task_id=1, block_reason="x")  # type: ignore[call-arg]


def test_list_tasks_signature_uses_assignee_and_reviewer():
    params = list(inspect.signature(tools.list_tasks).parameters)
    assert "assignee" in params and "reviewer" in params
    assert "assignee_id" not in params and "reviewer_id" not in params


def test_list_tasks_exposes_limit_with_none_default():
    """工具层对 Agent 开放 limit：签名为 `Optional[int] = None`（不传 ⇒ 内部默认 20）。

    #62 时故意不开放（Agent 无需控制页大小），Operator 现在要求开放；但**不传时的行为
    必须零变化**，所以默认值是 None 而不是 20 —— 由工具层收敛函数补内部默认值。
    """
    params = list(inspect.signature(tools.list_tasks).parameters)
    assert params == [
        "assignee", "reviewer", "scope", "status", "name_keyword",
        "open_only", "limit", "_context",
    ]
    assert inspect.signature(tools.list_tasks).parameters["limit"].default is None

    import service.taskService as taskService

    assert "limit" in inspect.signature(taskService.list_tasks).parameters


def test_list_tasks_signature_has_open_only_default_true():
    """工具层 list_tasks 新增 open_only，默认 True（Agent 默认不看已完结）。"""
    sig = inspect.signature(tools.list_tasks)
    assert "open_only" in sig.parameters
    assert sig.parameters["open_only"].default is True


def test_list_tasks_metadata_exposes_open_only_and_limit():
    """工具 schema 由 docstring 解析：properties 含 open_only / limit（均带说明），且全部非必填。"""
    meta = get_function_metadata("list_tasks", tools.list_tasks)
    props = meta["parameters"]["properties"]
    assert set(props) == {
        "assignee", "reviewer", "status", "name_keyword", "open_only", "scope", "limit",
    }
    assert props["open_only"]["type"] == "boolean"
    assert props["open_only"].get("description")
    assert props["limit"]["type"] == "integer"
    assert props["limit"].get("description")
    assert props["name_keyword"]["type"] == "string"
    assert props["name_keyword"].get("description")
    assert meta["parameters"]["required"] == []


def test_list_tasks_scope_is_literal_enum_without_default():
    """`scope` 是**过滤条件**之一：`Optional[Literal[...]]` 且**无默认值**。

    有默认值会让「传 scope」失去意义（不传也能跑）；现在三者都不传才报
    `missing_target`，并在首段把规则一次说清。schema 出 enum，模型不容易传错。
    这也是 #75 快照里 `schema` 字段的第一个真实 enum 用例（此前 26 个工具无 Literal）。
    """
    sig = inspect.signature(tools.list_tasks)
    assert sig.parameters["scope"].default is None

    meta = get_function_metadata("list_tasks", tools.list_tasks)
    scope_schema = meta["parameters"]["properties"]["scope"]
    assert scope_schema["enum"] == ["self", "team"]
    assert scope_schema.get("description")
    # 非必填：老调用方（只传 assignee）不受影响
    assert "scope" not in meta["parameters"]["required"]


@pytest.mark.asyncio
async def test_list_tasks_scope_team_without_filters_is_forwarded(monkeypatch):
    """`scope="team"` 且不给过滤器 ⇒ 不再报 missing_target，而是带上 caller_id 交给服务层判权限。"""
    from service.roomService.core import ToolCallContext
    import service.taskService as taskService

    captured: dict = {}

    async def _fake(**kwargs):
        captured.update(kwargs)
        return {"success": True, "tasks": [], "total": 0}

    monkeypatch.setattr(taskService, "list_tasks", _fake)
    ctx = ToolCallContext(agent_id=7, team_id=1, tool_name="list_tasks")
    res = await tools.list_tasks(scope="team", _context=ctx)

    assert res["success"] is True
    assert captured["scope"] == "team"
    assert captured["caller_id"] == 7
    assert captured["assignee_id"] is None and captured["reviewer_id"] is None


@pytest.mark.asyncio
async def test_list_tasks_rejects_when_no_filter_at_all():
    """#62 回归 + #76 新口径：assignee / reviewer / **scope** 三者都不传 ⇒ `missing_target`。

    这是防滥用契约，不许放松：想拿范围视角必须显式传 `scope`。
    """
    from service.roomService.core import ToolCallContext

    ctx = ToolCallContext(agent_id=8, team_id=1, tool_name="list_tasks")
    res = await tools.list_tasks(_context=ctx)

    assert res["success"] is False
    assert res["error_code"] == "missing_target"
    assert "assignee" in res["message"] and "scope" in res["message"]


@pytest.mark.asyncio
async def test_list_tasks_scope_self_alone_is_accepted(monkeypatch):
    """只传 `scope="self"` 即可：不再要求 assignee/reviewer，`caller_id` 由上下文给。"""
    from service.roomService.core import ToolCallContext
    import service.taskService as taskService

    captured: dict = {}

    async def _fake(**kwargs):
        captured.update(kwargs)
        return {"success": True, "tasks": [], "total": 0}

    monkeypatch.setattr(taskService, "list_tasks", _fake)
    ctx = ToolCallContext(agent_id=8, team_id=1, tool_name="list_tasks")
    res = await tools.list_tasks(scope="self", _context=ctx)

    assert res["success"] is True
    assert captured["scope"] == "self"
    assert captured["caller_id"] == 8
    assert captured["assignee_id"] is None and captured["reviewer_id"] is None


async def _call_tool_list_tasks(monkeypatch, **kwargs) -> dict:
    """调工具层 `list_tasks` 并返回服务层收到的 kwargs（服务层用假实现截获）。"""
    from service.roomService.core import ToolCallContext
    import service.taskService as taskService

    captured: dict = {}

    async def _fake(**service_kwargs):
        captured.update(service_kwargs)
        return {"success": True, "tasks": [], "count": 0, "total": 0, "limit": service_kwargs["limit"]}

    monkeypatch.setattr(taskService, "list_tasks", _fake)
    ctx = ToolCallContext(agent_id=8, team_id=1, tool_name="list_tasks")
    kwargs.setdefault("assignee", 8)
    res = await tools.list_tasks(_context=ctx, **kwargs)
    assert res["success"] is True
    return captured


@pytest.mark.asyncio
async def test_list_tasks_forwards_internal_default_limit(monkeypatch):
    """不传 limit ⇒ 仍透传内部默认 20（#62 的兼容口径不变），open_only 默认 True。"""
    captured = await _call_tool_list_tasks(monkeypatch)

    assert captured["assignee_id"] == 8
    assert captured["open_only"] is True
    assert captured["limit"] == tools.LIST_TASKS_DEFAULT_LIMIT == 20


@pytest.mark.asyncio
async def test_list_tasks_forwards_explicit_limit(monkeypatch):
    """传 limit=5 ⇒ 原样透传 5（区间内不收敛）。"""
    captured = await _call_tool_list_tasks(monkeypatch, limit=5)

    assert captured["limit"] == 5


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("requested", "effective"),
    [
        (0, tools.LIST_TASKS_MIN_LIMIT),        # 下界收敛：0 → 1
        (-5, tools.LIST_TASKS_MIN_LIMIT),       # 负数同下界
        (10000, tools.LIST_TASKS_MAX_LIMIT),    # 上界收敛：10000 → 50
        (51, tools.LIST_TASKS_MAX_LIMIT),       # 刚超一点也收敛
        (50, tools.LIST_TASKS_MAX_LIMIT),       # 边界值不动
        (1, tools.LIST_TASKS_MIN_LIMIT),        # 下边界值不动
    ],
)
async def test_list_tasks_clamps_out_of_range_limit(monkeypatch, requested, effective):
    """超界收敛到边界（不是报错）：模型传 10000 不该让查询失败，但也不能真查 10000 条。"""
    captured = await _call_tool_list_tasks(monkeypatch, limit=requested)

    assert captured["limit"] == effective


def test_list_tasks_limit_description_says_50_not_200():
    """上限是 **50**（Operator 中途从 200 改的），描述里不得再出现 200。"""
    meta = get_function_metadata("list_tasks", tools.list_tasks)
    desc = meta["parameters"]["properties"]["limit"]["description"]

    assert "1-50" in desc
    assert "200" not in desc


def test_list_tasks_clamp_helper_is_the_single_entry():
    """收敛逻辑只在一处：`_clamp_list_tasks_limit`（常量与工具层都指向它）。"""
    assert tools._clamp_list_tasks_limit(None) == 20
    assert tools._clamp_list_tasks_limit(0) == 1
    assert tools._clamp_list_tasks_limit(10000) == 50
    assert tools.LIST_TASKS_DEFAULT_LIMIT == 20
    assert tools.LIST_TASKS_MIN_LIMIT == 1
    assert tools.LIST_TASKS_MAX_LIMIT == 50


@pytest.mark.asyncio
async def test_list_tasks_echoes_effective_limit_in_result(monkeypatch):
    """返回体回显**生效值**（不是入参值）：模型才看得出超界被收敛了。"""
    from service.roomService.core import ToolCallContext
    import service.taskService as taskService

    async def _fake(**kwargs):
        return {"success": True, "tasks": [], "count": 0, "total": 0, "limit": kwargs["limit"]}

    monkeypatch.setattr(taskService, "list_tasks", _fake)
    ctx = ToolCallContext(agent_id=8, team_id=1, tool_name="list_tasks")
    res = await tools.list_tasks(assignee=8, limit=10000, _context=ctx)

    assert res["limit"] == 50


_ALL_STATES = ["TODO", "PENDING", "IN_PROGRESS", "ON_HOLD", "REVIEWING", "DONE", "CANCELLED"]


def test_list_tasks_status_schema_is_array_with_seven_item_enum():
    """`status` 导出 schema **必须带 enum**：`{"type":"array","items":{"enum":[7 项]}}`。

    Operator 已裁定 B（`Optional[list[Literal[...]]]`）：宁可
    `claude_sdk` 路径只支持列表，也不动全局 schema 生成器。
    只断言 `type == array` 是不够的——`list[str]` 也能过，那样模型拿到的是
    「任意字符串数组」，7 个合法值一个都没告诉它，等于本单要根治的问题没治。
    所以这里**逐项断言 enum 的内容与顺序**。
    """
    meta = get_function_metadata("list_tasks", tools.list_tasks)
    status_schema = meta["parameters"]["properties"]["status"]

    assert status_schema["type"] == "array"
    assert status_schema["items"]["enum"] == _ALL_STATES
    assert "anyOf" not in status_schema          # A 方案（anyOf）已作废，不得回潮
    assert "status" not in meta["parameters"]["required"]


def test_list_tasks_status_enum_guard_can_turn_red():
    """护栏能红：注解退化成 `list[str]` 时，enum 消失 ⇒ 上面那条断言必然失败。

    「永远不会红」的断言等于没有。这里用一个临时函数造出退化形态，证明差异确实可见：
    `list[str]` ⇒ `items == {"type": "string"}`（没有 enum）。
    """
    def degenerate_tool(status: Optional[list[str]] = None) -> dict:
        """退化成 list[str] 的反面样例（仅用于验证护栏能红）。

        Args:
            status: 退化后的状态参数
        """
        return {}

    meta = get_function_metadata("degenerate_tool", degenerate_tool)
    degenerate = meta["parameters"]["properties"]["status"]

    assert degenerate["type"] == "array"
    assert "enum" not in degenerate["items"]                    # 退化形态：没有 enum
    assert degenerate["items"] != {"enum": _ALL_STATES}         # ⇒ 正常形态的断言会红


def test_list_tasks_status_description_does_not_promise_single_string():
    """描述**不得**承诺「可传单个状态」：`claude_sdk` 路径下做不到（Operator 明示取舍）。

    native / tsp 驱动无 JSON Schema 校验，实现层 `isinstance(status, str)` 仍兼容裸字符串；
    但那是**安全垫，不进描述**——写进去就是对 claude_sdk 模型承诺一个会被 MCP 层拒掉的用法。
    """
    meta = get_function_metadata("list_tasks", tools.list_tasks)
    desc = meta["parameters"]["properties"]["status"]["description"]

    assert "数组" in desc
    assert "单个状态" not in desc and "可传单个" not in desc
    for state in _ALL_STATES:
        assert state in desc
    assert "整单报错" in desc                        # 不静默丢弃非法项
    assert "open_only" in desc                      # 与 open_only 的互斥关系不变


def test_list_tasks_status_description_does_not_promise_case_insensitive():
    """描述**不得**写「不区分大小写」：`enum` 只有 7 个纯大写值，claude_sdk 会把 `"todo"` 拒掉。

    这处是本轮新发现的「描述与 claude_sdk 实际行为不一致」：实现层 `TaskStatus.value_of(
    status.upper())` 接受小写（native 路径可用），但导出的 `enum` 是纯大写，
    MCP 层 `jsonschema.validate` 在进实现之前就拒了 `"todo"`。
    所以描述里已把「不区分大小写 / \"todo\" 等价 \"TODO\"」删掉，改为明确标注大写；
    下面同时钉住「不得回潮」与「必须写明大写」两侧。
    """
    meta = get_function_metadata("list_tasks", tools.list_tasks)
    desc = meta["parameters"]["properties"]["status"]["description"]

    assert "不区分大小写" not in desc
    assert '"todo"' not in desc and "todo" not in desc   # 不得给模型小写示例
    assert "大写" in desc                                # 必须明确只收大写，避免模型试小写


def test_list_tasks_status_schema_behavior_under_mcp_validation():
    """按 MCP 层同一口径（`jsonschema.validate`）实测导出 schema 的实际后果，留档。

    Operator 裁定 B 时明确接受「`claude_sdk` 下只支持传列表」。这里把后果钉死：
    - 数组（大写）⇒ 通过；
    - **裸字符串 ⇒ 被拒**（`'TODO' is not of type 'array'`）；
    - **数组里的小写 ⇒ 也被拒**（enum 只有大写）。
    第三条是本轮新发现的**描述与 claude_sdk 实际行为的不一致**（描述写了
    「不区分大小写」，但 enum 会把 `"todo"` 拒掉），已在回报里单独提出待 Operator 定夺。
    本用例只记录事实，不代表我们认可这处不一致。
    """
    import jsonschema

    schema = get_function_metadata("list_tasks", tools.list_tasks)["parameters"]["properties"]["status"]

    jsonschema.validate(["TODO", "DONE"], schema)                     # 正常用法
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate("TODO", schema)                           # 裸字符串被拒（Operator 接受）
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(["done"], schema)                         # 小写被 enum 拒（待定夺）


@pytest.mark.asyncio
async def test_list_tasks_forwards_status_as_is(monkeypatch):
    """工具层不解析 status：单值/数组都原样透传，解析与报错统一由服务层负责。"""
    single = await _call_tool_list_tasks(monkeypatch, status="TODO")
    assert single["status"] == "TODO"

    array = await _call_tool_list_tasks(monkeypatch, status=["todo", "REVIEWING"])
    assert array["status"] == ["todo", "REVIEWING"]


def test_list_tasks_order_is_id_desc_for_agent_side():
    """工具层显式要求 `id_desc`（最新在前）；DAL 默认值必须仍是优先级优先。

    这里断言的是**工具层传什么**，不是 DAL 的默认值——后者由
    `test_list_tasks_default_order_is_unchanged` 单独钉住（Web 列表接口依赖它）。
    """
    import inspect as _inspect

    from dal.db import gtAgentTaskManager

    # DAL 默认值 = 优先级优先（Web 列表接口零影响）
    order_param = _inspect.signature(gtAgentTaskManager.list_tasks).parameters["order"]
    assert order_param.default == gtAgentTaskManager.ORDER_PRIORITY_FIRST
    assert gtAgentTaskManager.ORDER_PRIORITY_FIRST == "priority_first"
    assert gtAgentTaskManager.ORDER_ID_DESC == "id_desc"


@pytest.mark.asyncio
async def test_list_tasks_forwards_id_desc_order(monkeypatch):
    """工具层调服务层时显式传 `order="id_desc"`。"""
    from dal.db import gtAgentTaskManager

    captured = await _call_tool_list_tasks(monkeypatch)

    assert captured["order"] == gtAgentTaskManager.ORDER_ID_DESC


@pytest.mark.asyncio
async def test_list_tasks_forwards_name_keyword(monkeypatch):
    """`name_keyword` 原样透传给服务层（匹配逻辑在服务层，工具层不解析）。"""
    captured = await _call_tool_list_tasks(monkeypatch, name_keyword="网关")

    assert captured["name_keyword"] == "网关"


@pytest.mark.asyncio
async def test_list_tasks_name_keyword_alone_is_a_valid_target(monkeypatch):
    """只传 `name_keyword` 也应被接受（不再强制 assignee/reviewer/scope）。"""
    captured = await _call_tool_list_tasks(monkeypatch, assignee=None, name_keyword="网关")

    assert captured["name_keyword"] == "网关"
    assert captured["assignee_id"] is None


def test_get_task_detail_exists():
    assert callable(tools.get_task_detail)


def test_get_task_detail_event_limit_default_is_three():
    """工具层 event_limit 默认 3，用独立常量 DEFAULT_EVENT_LIMIT 承载。"""
    assert tools.DEFAULT_EVENT_LIMIT == 3
    sig = inspect.signature(tools.get_task_detail)
    assert sig.parameters["event_limit"].default == 3
    assert list(sig.parameters) == ["task_id", "event_limit", "_context"]


def test_get_task_detail_metadata_exposes_event_limit():
    """schema 暴露 event_limit（含说明）；task_id 必填，event_limit 非必填。"""
    meta = get_function_metadata("get_task_detail", tools.get_task_detail)
    props = meta["parameters"]["properties"]
    assert set(props) == {"task_id", "event_limit"}
    assert props["event_limit"]["type"] == "integer"
    assert props["event_limit"].get("description")
    assert meta["parameters"]["required"] == ["task_id"]


@pytest.mark.asyncio
async def test_get_task_detail_forwards_event_limit(monkeypatch):
    """工具层把 event_limit 透传给服务层。"""
    from service.roomService.core import ToolCallContext
    import service.taskService as taskService

    captured: dict = {}

    async def _fake(**kwargs):
        captured.update(kwargs)
        return {"success": True, "task": {"id": 1}}

    monkeypatch.setattr(taskService, "get_task", _fake)
    ctx = ToolCallContext(agent_id=8, team_id=1, tool_name="get_task_detail")

    await tools.get_task_detail(task_id=1, _context=ctx)
    assert captured["event_limit"] == tools.DEFAULT_EVENT_LIMIT

    await tools.get_task_detail(task_id=1, event_limit=7, _context=ctx)
    assert captured["event_limit"] == 7


def test_list_task_events_signature():
    params = list(inspect.signature(tools.list_task_events).parameters)
    assert params == ["task_id", "limit", "before_id", "_context"]


def test_tool_metadata_exposes_new_status_only():
    """工具 schema：properties 含 new_status/note、不含 result/block_reason；required 仅 task_id。"""
    meta = get_function_metadata("update_task", tools.update_task)
    props = meta["parameters"]["properties"]
    assert "new_status" in props
    assert "note" in props
    assert "result" not in props
    assert "block_reason" not in props
    assert meta["parameters"]["required"] == ["task_id"]


def test_registry_contains_expected_task_tool_names():
    """core 注册表含 get_task_detail / list_task_events，且不含已删除的 get_task 别名。"""
    from service.funcToolService import core

    registry = core.load_func_tools()
    assert "get_task_detail" in registry
    assert "list_task_events" in registry
    assert "get_task" not in registry  # 废弃别名已删除（#71）


def test_get_task_event_signature_exposes_only_event_id():
    """get_task_event 入参 = event_id / _context（无 limit / task_id 等）。"""
    params = list(inspect.signature(tools.get_task_event).parameters)
    assert params == ["event_id", "_context"]


def test_get_task_event_metadata_only_exposes_required_event_id():
    """工具 schema 由 docstring 解析：properties 只有 event_id，且必填、含说明。"""
    meta = get_function_metadata("get_task_event", tools.get_task_event)
    props = meta["parameters"]["properties"]
    assert set(props) == {"event_id"}
    assert props["event_id"]["type"] == "integer"
    assert props["event_id"].get("description")
    assert meta["parameters"]["required"] == ["event_id"]
    # description 只取 docstring 首段
    assert "读取当前团队的一条任务事件" in meta["description"]
    # 事件 ID 与任务 ID 的区别必须写给模型看（#78 明确补强）
    assert "不是任务 ID" in props["event_id"]["description"]


def test_get_task_event_docstring_states_full_content_semantics():
    """docstring 须写明「返回完整原文、不受摘要截断限制」（仅本工具不截断）。"""
    doc = tools.get_task_event.__doc__ or ""
    assert "完整原文" in doc
    assert "截断" in doc

    meta = get_function_metadata("get_task_event", tools.get_task_event)
    assert "完整原文" in meta["description"]


def test_get_task_event_registered_in_core():
    """core 注册表含 get_task_event。"""
    from service.funcToolService import core

    assert "get_task_event" in core.load_func_tools()


def test_get_task_event_categorized_basic_and_resolvable():
    """归入 BASIC 类别，Category:Basic 策略下可解析到（防止漏配 CATEGORY_CONFIG）。"""
    from service.agentService.toolRegistry import CATEGORY_CONFIG, AgentToolRegistry
    from service.funcToolService import core
    from constants import ToolCategory

    assert CATEGORY_CONFIG["get_task_event"] == ToolCategory.BASIC

    core.load_func_tools()
    registry = AgentToolRegistry()
    for tool in core.get_tools():
        registry.register(tool, lambda args_str, ctx: None)

    assert "get_task_event" in registry.resolve_enabled_tool_names(["Category:Basic"])


@pytest.mark.asyncio
async def test_get_task_event_forwards_event_id_and_team(monkeypatch):
    """工具层把 team_id（来自上下文）与 event_id 透传给服务层。"""
    from service.roomService.core import ToolCallContext
    import service.taskService as taskService

    captured: dict = {}

    async def _fake(**kwargs):
        captured.update(kwargs)
        return {"success": True, "event": {"id": 5}}

    monkeypatch.setattr(taskService, "get_task_event", _fake)
    ctx = ToolCallContext(agent_id=8, team_id=1, tool_name="get_task_event")

    res = await tools.get_task_event(event_id=5, _context=ctx)

    assert res["success"] is True
    assert captured == {"team_id": 1, "event_id": 5}


@pytest.mark.asyncio
async def test_get_task_event_without_team_context_returns_failure():
    """无团队上下文 ⇒ 失败，不抛异常。"""
    res = await tools.get_task_event(event_id=5)

    assert res["success"] is False
    assert "团队上下文" in res["message"]


def test_get_task_detail_docstring_states_truncation_and_points_to_full_read():
    """#65：docstring 须说明「>100 字符截断」并指向 get_task_event 取完整原文。

    且该散文化说明放在 Args 段之外，不得破坏 schema 解析（参数照常暴露）。
    """
    doc = tools.get_task_detail.__doc__ or ""
    assert "100 字符" in doc
    assert "get_task_event" in doc
    assert "截断" in doc
    # 参数段照常解析（说明须在 Args 段之外的散文里）
    meta = get_function_metadata("get_task_detail", tools.get_task_detail)
    props = meta["parameters"]["properties"]
    assert set(props) == {"task_id", "event_limit"}
    assert props["event_limit"].get("description")
    assert meta["parameters"]["required"] == ["task_id"]


def test_list_task_events_docstring_states_truncation_and_points_to_full_read():
    """#65：list_task_events docstring 同样补截断说明，schema 解析不受影响。"""
    doc = tools.list_task_events.__doc__ or ""
    assert "100 字符" in doc
    assert "get_task_event" in doc

    meta = get_function_metadata("list_task_events", tools.list_task_events)
    props = meta["parameters"]["properties"]
    assert set(props) == {"task_id", "limit", "before_id"}
    assert props["limit"].get("description")
    assert props["before_id"].get("description")
    assert meta["parameters"]["required"] == ["task_id"]

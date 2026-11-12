"""#189：tool call 参数 schema 校验的判据（覆盖级 / 行为级 / 结构级 / 兜底）。

## 判据与用例的对应

- 覆盖级（判据 1）：`test_all_default_tools_reject_invalid_args` /
  `test_all_container_tools_reject_invalid_args` / `test_all_tsp_remote_tools_reject_invalid_args`
  —— 三组**全遍历**，逐个断言被拦，并断言遍历计数 == 该组工具数（禁抽样）。
- 行为级（小马哥 1020 替代判据）：`test_tightened_schema_rejects` /
  `test_relaxed_schema_allows` / `test_unavailable_schema_skips_but_does_not_disable_others`
- 缓存：`test_schema_is_cached_per_callable_identity`（先证明缓存真的生效，否则
  「清缓存后施加 monkeypatch」是空操作）
- 结构级：`test_schema_equals_what_is_sent_to_the_model`（同源证明，报告里写明是
  **等值**而非同一对象）
- TSP 兜底：`test_tsp_malformed_schema_still_calls_through`（缺键 / 非 object / 非法 schema）
- JSON：`test_invalid_json_is_structured_and_does_not_execute`
- **根节点 `null`（H1，38 打回）**：`test_root_null_rejected_default_tools` /
  `test_root_null_rejected_container_tools` / `test_root_null_rejected_tsp_remote_tools`
  —— 三组各一条，断言 `invalid_tool_args` 且文案**不含** `Invalid arguments for function`
  （即不许退化成「英文解释器原话 + 无 error_code」的改造前形态）。
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from service import funcToolService
from service.agentService.driver import claudeSdkDriver as csd_module
from service.funcToolService import core as funcCore
from service.funcToolService import funcToolType, toolArgsValidation
from service.funcToolService.funcToolType import FuncTool
from service.roomService import ToolCallContext
from toolResult import INVALID_JSON_ARGS

WARNING_KEYWORD = "已跳过校验"

#: 工具总数（`python scripts/tool_schema_snapshot.py` 口径）。写死是为了让「少遍历一组」
#: 或「抽样」立刻变红——动态用 `len()` 自证会随实现一起漂移，等于没断言。
#: 32 → 33：webfetch（#191）进默认工具组，故默认组 33、合计 33+5=38。
DEFAULT_TOOL_COUNT = 33
CONTAINER_TOOL_COUNT = 5


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _cold_schema_cache():
    """每个用例都在**冷缓存**下开始，结束后也清干净（不污染别的用例）。

    1020 明确要求：monkeypatch 类判据必须在缓存未热时施加，否则「读不到新实现」会被
    误判成「实现里有第二份 schema」。
    """

    funcCore.load_func_tools()
    funcCore.load_container_func_tools()
    toolArgsValidation.clear_schema_cache()
    yield
    toolArgsValidation.clear_schema_cache()


def _ctx(tool_name: str) -> ToolCallContext:
    return ToolCallContext(agent_id=1, team_id=1, tool_name=tool_name)


#: schema 层拒绝文案的固定前缀（`toolArgsValidation.validate_tool_args` 里那一条）。
#:
#: #196 起 `error_code == "invalid_tool_args"` 有**两个**产出层：
#: - schema 层（`toolArgsValidation`）：文案 `参数校验失败（tool=<name>）：…`
#: - 运行时层（`tools.py` 的函数体内，locator / 新建必填缺失）：文案点名参数
#:   （如 `请用 agent_id 或 name 指定成员…`）。两者同码是**有意**的（小马哥 12:02
#:   硬条件 1「与 schema 层同码」，让调用方一个码就能判「补参数重试」）。
#:
#: ⇒ 凡是要测「**schema 层**放不放行 null」的判据，不能只看 `error_code`
#: （会把运行时拒绝误认成 schema 拒绝 ⇒ P1 注入表出现 27 处假红）；必须同时匹配层文案。
#: 只看层文案不判码则更糟：schema 层一旦丢码，判据仍绿。两者一起判。
#:
#: ⚠️ 单一来源（小马哥 23:08 第②件）：直接取源码常量，本文件不再硬编码。
#: 与 `test_tool_schema_nullable.py` 共用同一个来源，文案变更只需改
#: `toolArgsValidation.py` 一处（并有 meta 断言钉住它没漂）。
SCHEMA_LAYER_MARK = toolArgsValidation.SCHEMA_LAYER_MARK


def _rejected_by_schema(result: dict) -> bool:
    """该返回体是否由 **schema 层**（而非运行时必填校验）拒绝。"""
    return (
        result.get("error_code") == toolArgsValidation.INVALID_TOOL_ARGS
        and SCHEMA_LAYER_MARK in result.get("message", "")
    )


def _schema_of(func: Any) -> dict:
    from service.funcToolService.funcToolType import get_function_metadata

    return get_function_metadata("x", func)["parameters"]


def _declared_types(prop: dict) -> set[str]:
    """取某个属性 schema 声明的 `type` 集合（`type` 可能是字符串或字符串数组）。

    #195 之后 `Optional[T]` 产 `{"type": ["string", "null"]}`，`type` 不再是单值字符串；
    沿用 `prop.get("type") == "array"` 这种写法会让**所有** array/object 型参数从扫描
    结果里静默消失（判据变成空转还全绿）。所以这里统一归一成集合，调用方必须显式写清
    自己认哪些类型（例如 `"array" in _declared_types(prop)`）。
    """

    declared = prop.get("type")
    if isinstance(declared, str):
        return {declared}
    if isinstance(declared, list):
        return {item for item in declared if isinstance(item, str)}
    return set()


def _non_null_type(prop: dict) -> str | None:
    """取非 `null` 的那个类型名（造合法值 / 造非法值都按它来）。"""

    types = _declared_types(prop) - {"null"}
    return next(iter(types)) if len(types) == 1 else None


def _invalid_args_for(schema: dict) -> Any:
    """按 schema 造一份**必定非法**的参数。

    - 有必填 ⇒ 空对象（触发 required）；
    - 否则取第一个可违规的属性喂错类型/错枚举；
    - 一个属性都没有（零参工具）⇒ 喂 JSON 数组，触发根节点的 `type: object`。

    这样每组工具（含零参工具）都能拿到一条真实非法参数，不必为了「有东西可喂」而给
    零参工具编造参数。

    #195：`type` 可能已是 `["string", "null"]`，按**非 null 分支**造错值（喂 `null`
    对可空参数是合法的，那不叫非法参数）。
    """

    required = schema.get("required") or []
    if required:
        return {}

    for name, prop in (schema.get("properties") or {}).items():
        if "enum" in prop:
            return {name: "__value_not_in_enum__"}
        declared = _non_null_type(prop)
        replacement = {
            "string": 12345,
            "integer": "not-an-integer",
            "number": "not-a-number",
            "boolean": "not-a-boolean",
            "array": "not-an-array",
            "object": "not-an-object",
        }.get(declared)
        if replacement is not None:
            return {name: replacement}
    return []


# --------------------------------------------------------------------------- #
# 覆盖级：三组工具全遍历
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_all_default_tools_reject_invalid_args() -> None:
    """默认工具组全遍历：每个工具喂非法参数都被校验拦下，且计数等于工具总数。"""

    from service.funcToolService.funcToolType import get_function_metadata

    # 用**注册面**枚举：本判据测的是「每个工具的 schema 校验」，与 D2 暴露面无关
    # （隐藏模式下 `get_tools()` 会少掉 16 个已收敛工具，计数断言会误红）。
    tools = funcCore.get_all_tools()
    assert len(tools) == DEFAULT_TOOL_COUNT, [tool.function.name for tool in tools]

    covered = 0
    for tool in tools:
        name = tool.function.name
        func = funcCore.get_func_tool(name)
        assert func is not None, name
        invalid_args = _invalid_args_for(get_function_metadata(name, func.callable)["parameters"])

        result = await funcCore.run_tool_call(json.dumps(invalid_args), _ctx(name))

        assert result["success"] is False, (name, invalid_args)
        assert result.get("error_code") == toolArgsValidation.INVALID_TOOL_ARGS, (name, result)
        # 结构化的实质要求：文案里必须能定位「哪个参数、期望什么、收到什么」，
        # 不许退化成 `Invalid arguments for function xxx` 这种无字段名的旧文案。
        assert "参数校验失败" in result["message"], (name, result)
        covered += 1

    assert covered == DEFAULT_TOOL_COUNT


@pytest.mark.asyncio
async def test_all_container_tools_reject_invalid_args() -> None:
    """容器工具组全遍历（判据 1 的「不许只覆盖一组」）。"""

    from service.funcToolService.funcToolType import get_function_metadata

    names = list(funcToolService.CONTAINER_TOOL_NAMES)
    assert len(names) == CONTAINER_TOOL_COUNT, names

    covered = 0
    for name in names:
        func_tool = funcCore.get_func_tool(name)
        assert func_tool is not None, name
        invalid_args = _invalid_args_for(
            get_function_metadata(name, func_tool.callable)["parameters"]
        )

        result = await funcCore.run_tool_call(json.dumps(invalid_args), _ctx(name))

        assert result["success"] is False, (name, invalid_args)
        assert result.get("error_code") == toolArgsValidation.INVALID_TOOL_ARGS, (name, result)
        covered += 1

    assert covered == CONTAINER_TOOL_COUNT


def _make_tsp_driver(remote_tools: list[Any]):
    """构造一个只走 `_execute_tsp_tool` 的 driver（client 为 MagicMock，进程视为存活）。"""

    from service.agentService.driver.base import AgentDriverConfig
    from service.agentService.driver.tspDriver import TspAgentDriver

    host = MagicMock()
    host.gt_agent = MagicMock(id=1)
    host.tool_registry = MagicMock()
    driver = TspAgentDriver(host, AgentDriverConfig(driver_type="tsp", options={}))
    driver._client = MagicMock()
    driver._client.process = MagicMock()
    driver._client.process.returncode = None
    driver._client.call_tool = AsyncMock(return_value=MagicMock(output={"success": True}))
    driver._tsp_tools = {
        tool.function.name: tool for tool in remote_tools
    }
    return driver


def _remote_tool(name: str, parameters: Any) -> Any:
    from util import llmApiUtil

    tool = llmApiUtil.OpenAITool(
        function=llmApiUtil.OpenAIFunction(
            name=name,
            description=f"remote {name}",
            parameters=llmApiUtil.OpenAIFunctionParameter(
                type="object",
                properties={},
                required=[],
            ),
        )
    )
    # 直接替换成「远端给什么就是什么」的原始值：真实路径由 `_load_tsp_tools` 校验成
    # pydantic 模型，畸形场景则不会通过那一步校验，这里直接构造畸形对象。
    tool.function.parameters = parameters  # type: ignore[assignment]
    return tool


@pytest.mark.asyncio
async def test_all_tsp_remote_tools_reject_invalid_args() -> None:
    """TSP 远端组全遍历：非法参数在 `call_tool` 之前被拦，计数等于远端工具数。"""

    from util import llmApiUtil

    specs = {
        "remote_write": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "force": {"type": "boolean"}},
            "required": ["path"],
        },
        "remote_read": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        "remote_list": {
            "type": "object",
            "properties": {"mode": {"enum": ["a", "b"]}},
            "required": [],
        },
    }
    remote_tools = [
        llmApiUtil.OpenAITool(
            function=llmApiUtil.OpenAIFunction(
                name=name,
                description=f"remote {name}",
                parameters=llmApiUtil.OpenAIFunctionParameter(
                    type=parameters["type"],
                    properties=parameters["properties"],
                    required=parameters["required"],
                ),
            )
        )
        for name, parameters in specs.items()
    ]
    driver = _make_tsp_driver(remote_tools)
    assert len(driver._tsp_tools) == len(specs)

    covered = 0
    for name, parameters in specs.items():
        invalid_args = _invalid_args_for(parameters)

        result = await driver._execute_tsp_tool(json.dumps(invalid_args), _ctx(name))

        assert result["success"] is False, (name, invalid_args)
        assert result.get("error_code") == toolArgsValidation.INVALID_TOOL_ARGS, (name, result)
        covered += 1

    assert covered == len(specs)
    # 「校验发生在转发之前」的硬证据：一条都没进 `call_tool`。
    driver._client.call_tool.assert_not_called()


# --------------------------------------------------------------------------- #
# 行为级：收紧 / 放宽 / 异常兜底
# --------------------------------------------------------------------------- #


def _stub_tool(name: str, recorder: dict) -> Any:
    def stub() -> dict:
        recorder["called"] = recorder.get("called", 0) + 1
        return {"success": True, "result": "ok"}

    stub.__name__ = name
    stub.__qualname__ = name
    return stub


@pytest.mark.asyncio
async def test_tightened_schema_rejects(monkeypatch) -> None:
    """a) 收紧：无参工具被插上 `required: ["x"]` ⇒ 空参数**确实被拒绝**。"""

    recorder: dict = {}
    stub = _stub_tool("stub_tighten", recorder)
    monkeypatch.setattr(funcCore, "get_func_tool", lambda name: FuncTool(name=name, callable=stub))
    tightened = {
        "type": "object",
        "properties": {"x": {"type": "string"}},
        "required": ["x"],
    }
    monkeypatch.setattr(
        funcToolType, "get_function_metadata",
        lambda name, func: {"name": name, "description": "", "parameters": tightened},
    )

    result = await funcCore.run_tool_call("{}", _ctx("stub_tighten"))

    assert result["success"] is False
    assert result["error_code"] == toolArgsValidation.INVALID_TOOL_ARGS
    assert "'x'" in result["message"]
    assert recorder.get("called", 0) == 0, "校验没拦住，工具被执行了"


@pytest.mark.asyncio
async def test_relaxed_schema_allows(monkeypatch) -> None:
    """b) 放宽：把同一份 schema 的必填删掉 ⇒ 空参数**确实放行**。

    a + b 一起才能证明「校验行为真的跟着 schema 走」：只测其中一个，无论实现是「恒拒」
    还是「恒放」都能过。
    """

    recorder: dict = {}
    stub = _stub_tool("stub_relax", recorder)
    monkeypatch.setattr(funcCore, "get_func_tool", lambda name: FuncTool(name=name, callable=stub))
    relaxed = {"type": "object", "properties": {"x": {"type": "string"}}, "required": []}
    monkeypatch.setattr(
        funcToolType, "get_function_metadata",
        lambda name, func: {"name": name, "description": "", "parameters": relaxed},
    )

    result = await funcCore.run_tool_call("{}", _ctx("stub_relax"))

    assert result["success"] is True, result
    assert recorder["called"] == 1


@pytest.mark.asyncio
async def test_unavailable_schema_skips_but_does_not_disable_others(
    monkeypatch, caplog
) -> None:
    """c) 异常兜底：schema 提取抛异常 ⇒ ①该工具放行 ②一条 warning ③**别的工具照旧被拦**。

    ③ 是精髓：如果实现写成在校验入口整体 `except: pass`，①②照样绿，但③会红 ——
    「单点兜底」与「全局失能」必须被区分开。
    """

    recorder: dict = {}
    stub = _stub_tool("stub_boom_schema", recorder)
    real_get = funcToolType.get_function_metadata
    real_lookup = funcCore.get_func_tool

    def _maybe_boom(name: str, func: Any) -> dict:
        if name == "stub_boom_schema":
            raise RuntimeError("提取签名失败")
        return real_get(name, func)

    def _lookup(name: str) -> Any:
        # 只替换被测的那个名字：另一条断言要打在**真工具** `get_time` 上，否则
        # 「别的工具仍被拦」会变成「那个替身仍被拦」，判据就空了。
        if name == "stub_boom_schema":
            return FuncTool(name=name, callable=stub)
        return real_lookup(name)

    monkeypatch.setattr(funcCore, "get_func_tool", _lookup)
    monkeypatch.setattr(funcToolType, "get_function_metadata", _maybe_boom)

    with caplog.at_level("WARNING", logger="service.funcToolService.toolArgsValidation"):
        boom_result = await funcCore.run_tool_call("{}", _ctx("stub_boom_schema"))
        # 同一次运行里，另一条**真工具**（schema 正常）的非法参数必须仍被拦下。
        other = await funcCore.run_tool_call('{"timezone": 123}', _ctx("get_time"))

    assert boom_result["success"] is True, boom_result
    assert recorder["called"] == 1

    warnings = [record for record in caplog.records if WARNING_KEYWORD in record.getMessage()]
    assert len(warnings) == 1, [record.getMessage() for record in caplog.records]
    assert "stub_boom_schema" in warnings[0].getMessage()

    assert other["success"] is False, other
    assert other["error_code"] == toolArgsValidation.INVALID_TOOL_ARGS


def test_schema_is_cached_per_callable_identity(monkeypatch) -> None:
    """缓存证明（先证明缓存真的生效，否则「清缓存后 monkeypatch」是空操作）。

    同时断言缓存键与**可调用对象身份**绑定：同一份函数定义连调两次只提取一次。
    """

    calls: list[str] = []
    real_get = funcToolType.get_function_metadata

    def _counting(name: str, func: Any) -> dict:
        calls.append(name)
        return real_get(name, func)

    monkeypatch.setattr(funcToolType, "get_function_metadata", _counting)

    func = funcCore.get_func_tool("get_time").callable  # type: ignore[union-attr]
    first = toolArgsValidation.get_tool_parameters_schema("get_time", func)
    second = toolArgsValidation.get_tool_parameters_schema("get_time", func)

    assert calls == ["get_time"], f"schema 没被缓存：{calls}"
    assert first == second
    assert toolArgsValidation._callable_identity(func) in toolArgsValidation.get_cached_schema_keys()


def test_schema_cache_key_changes_with_function_definition(monkeypatch) -> None:
    """热重载场景：函数体变了 ⇒ 缓存键必须变（`id()` 会复用地址，挡不住这种情况）。"""

    def tool_a() -> dict:
        return {}

    def tool_b() -> dict:
        return {"success": True}

    assert toolArgsValidation._callable_identity(tool_a) != toolArgsValidation._callable_identity(tool_b)


# --------------------------------------------------------------------------- #
# 结构级：与下发给模型的 schema 同源
# --------------------------------------------------------------------------- #


def test_schema_equals_what_is_sent_to_the_model() -> None:
    """结构级同源：校验用 schema == `to_openai_tool()` 下发给模型的 parameters。

    证法是**等值**（两个独立调用产出的 dict 逐键相等），不是「同一对象」——
    `get_function_metadata` 每次现造 dict，对象同一性在这里不成立，报告里写明这一点。
    """

    # 注册面枚举（与 D2 暴露面无关）：schema 同源是每个已注册工具的性质。
    names = [tool.function.name for tool in funcCore.get_all_tools(include_container_tools=True)]
    assert len(names) == DEFAULT_TOOL_COUNT + CONTAINER_TOOL_COUNT

    for name in names:
        func_tool = funcCore.get_func_tool(name)
        assert func_tool is not None, name
        from_model = func_tool.to_openai_tool().function.parameters.model_dump()
        used_by_validation = toolArgsValidation.get_tool_parameters_schema(name, func_tool.callable)

        assert used_by_validation == from_model, name


# --------------------------------------------------------------------------- #
# TSP 畸形 schema 兜底
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "parameters, reason",
    [
        (None, "缺少 function.parameters"),
        (["type", "object"], "parameters 不是 dict"),
        ({"type": "object", "properties": {"x": {"type": "no-such-type"}}}, "check_schema 判定非法"),
    ],
)
async def test_tsp_malformed_schema_still_calls_through(parameters, reason, caplog) -> None:
    """远端 schema 畸形 ⇒ **工具仍可调用 + 一条 warning**，绝不拦死正常 TSP 调用。

    三种畸形分别对应三处兜底：取不到键、取到非 dict、`check_schema` 判非法。
    """

    driver = _make_tsp_driver([_remote_tool("remote_odd", parameters)])

    with caplog.at_level("WARNING", logger="service.funcToolService.toolArgsValidation"):
        result = await driver._execute_tsp_tool("{}", _ctx("remote_odd"))

    assert result == {"success": True}, result
    driver._client.call_tool.assert_awaited_once()

    warnings = [record.getMessage() for record in caplog.records if WARNING_KEYWORD in record.getMessage()]
    assert len(warnings) == 1, warnings
    assert "remote_odd" in warnings[0]
    assert reason in warnings[0]


# --------------------------------------------------------------------------- #
# JSON 解析失败：结构化 + 不执行
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_invalid_json_is_structured_and_does_not_execute(monkeypatch) -> None:
    """坏 JSON 不许再「静默当空参数继续执行」，返回结构化错误且工具零调用。"""

    recorder: dict = {}

    def stub(room_name: str, msg: str) -> dict:
        recorder["called"] = recorder.get("called", 0) + 1
        return {"success": True}

    monkeypatch.setattr(funcCore, "get_func_tool", lambda name: FuncTool(name=name, callable=stub))

    result = await funcCore.run_tool_call("not json", _ctx("stub_json"))

    assert result["success"] is False
    assert result["error_code"] == INVALID_JSON_ARGS
    assert "not json" in result["message"]
    assert recorder.get("called", 0) == 0


@pytest.mark.asyncio
async def test_tsp_invalid_json_is_structured() -> None:
    """TSP 路径同一份构造函数：错误码一致，且不进 `call_tool`。"""

    driver = _make_tsp_driver([_remote_tool("remote_odd", {"type": "object"})])

    result = await driver._execute_tsp_tool("invalid json", _ctx("remote_odd"))

    assert result["success"] is False
    assert result["error_code"] == INVALID_JSON_ARGS
    driver._client.call_tool.assert_not_called()


# --------------------------------------------------------------------------- #
# 根节点 null（H1，38 打回 04:48）：整份参数不是 JSON 对象
# --------------------------------------------------------------------------- #

#: 「改造前形态」的文案特征：英文解释器原话，且不带 error_code。
#: 根节点 null 若被 `_is_ignorable_null` 误豁免，就会退化成它 —— 这是本组判据的红线。
_LEGACY_MESSAGE_MARK = "Invalid arguments for function"


@pytest.mark.asyncio
async def test_root_null_rejected_default_tools() -> None:
    """默认工具组：`function_args` 字面量为 `null` ⇒ **每一个**工具都被拦下。

    H1 缺陷复现：修复前 `get_tools(include_container_tools=True)` 的 37 个工具逐个喂
    `'null'` **漏网 37/37** —— 因为根节点的 `type: object` 错误 `absolute_path == []`、
    `instance is None`，被 `_is_ignorable_null` 当成「非必填字段的 null」豁免掉了。

    ⚠️ 必须断言**文案不含** `Invalid arguments for function`：只断言 `success is False`
    不够 —— 漏网时 `func(**None)` 抛 `TypeError` 也会返回 `success: False`，只是丢了
    `error_code` 且文案是英文原话。那样断言会**假绿**（这正是 H1 之前漏掉的原因）。
    """

    tools = funcCore.get_all_tools()
    assert len(tools) == DEFAULT_TOOL_COUNT

    covered = 0
    for tool in tools:
        name = tool.function.name
        result = await funcCore.run_tool_call("null", _ctx(name))

        assert result["success"] is False, (name, result)
        assert result.get("error_code") == toolArgsValidation.INVALID_TOOL_ARGS, (name, result)
        assert _LEGACY_MESSAGE_MARK not in result["message"], (name, result)
        assert "参数校验失败" in result["message"], (name, result)
        covered += 1

    assert covered == DEFAULT_TOOL_COUNT


@pytest.mark.asyncio
async def test_root_null_rejected_container_tools() -> None:
    """容器工具组：同样的根节点 null，一个都不许漏。"""

    names = list(funcToolService.CONTAINER_TOOL_NAMES)
    assert len(names) == CONTAINER_TOOL_COUNT

    covered = 0
    for name in names:
        result = await funcCore.run_tool_call("null", _ctx(name))

        assert result["success"] is False, (name, result)
        assert result.get("error_code") == toolArgsValidation.INVALID_TOOL_ARGS, (name, result)
        assert _LEGACY_MESSAGE_MARK not in result["message"], (name, result)
        covered += 1

    assert covered == CONTAINER_TOOL_COUNT


@pytest.mark.asyncio
async def test_root_null_rejected_tsp_remote_tools() -> None:
    """TSP 远端组：与本地共用 `_is_ignorable_null`，同一漏洞必须同步堵住。

    远端路径不走 `run_tool_call`，所以这条打在 `_execute_tsp_tool` 上，并断言
    **没有进 `call_tool`**（校验发生在转发之前）。
    """

    specs = {
        "remote_write": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        "remote_read": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": [],
        },
    }
    remote_tools = [
        _remote_tool(name, parameters) for name, parameters in specs.items()
    ]
    driver = _make_tsp_driver(remote_tools)

    for name in specs:
        result = await driver._execute_tsp_tool("null", _ctx(name))

        assert result["success"] is False, (name, result)
        assert result.get("error_code") == toolArgsValidation.INVALID_TOOL_ARGS, (name, result)
        assert _LEGACY_MESSAGE_MARK not in result["message"], (name, result)

    driver._client.call_tool.assert_not_called()


# --------------------------------------------------------------------------- #
# 容器元素级 null（H2，38 二次打回）：`{"tags": [null]}`
# --------------------------------------------------------------------------- #

#: H2 的复现靶子：`create_task.depends_on` 是 `list[int]`，喂 `[None]` 曾整类放行。
#: ⚠️ 必须带**完整的前置必填参数**（`title` / `assignee_id`）：否则 jsonschema 先报
#: `required`，那条错误本身就会被正常拦下，**后置的元素级 null 错误被前置遮住**，
#: 判据会假绿（我第一次复现就踩了这个，得到 2/10 而实际 10/10）。
_FULL_ARGS_FOR_ELEMENT_NULL = {
    "create_task": {"title": "t", "assignee_id": 38},
    "list_role_templates": {},
    "list_tasks": {},
    "memory_create": {"title": "t", "content": "c"},
    "memory_update": {"memory_id": 1},
    "save_agent": {"name": "a"},
    "save_dept": {"name": "d"},
    "save_room": {"key": "r"},
    "update_task": {"task_id": 1},
}


@pytest.mark.asyncio
async def test_element_null_rejected_for_all_array_params() -> None:
    """本地：**全部** array 型参数喂 `[None]` 都必须被拦（不只是 `depends_on` 一个）。

    为什么遍历而不是只测 `depends_on`：H2 是「`path` 长度 ≥2 一律豁免」的**整类**
    缺陷，只钉一个参数不足以证明整类被堵住。这里按 schema 现扫出全部 array 参数，
    逐个断言，并断言扫描计数 == 13（少扫一个就说明判据退化了）。
    """

    from service.funcToolService.funcToolType import get_function_metadata

    # 先扫出所有 array 型参数（(tool_name, param_name)）
    array_params: list[tuple[str, str]] = []
    for tool in funcCore.get_all_tools(include_container_tools=True):
        name = tool.function.name
        func_tool = funcCore.get_func_tool(name)
        if func_tool is None:
            continue
        try:
            parameters = get_function_metadata(name, func_tool.callable)["parameters"]
        except Exception:  # noqa: BLE001 —— schema 不可用的工具不参与本判据
            continue
        for param_name, prop in (parameters.get("properties") or {}).items():
            if "array" in _declared_types(prop):
                array_params.append((name, param_name))

    assert len(array_params) == 13, array_params

    blocked = 0
    for name, param_name in array_params:
        args = dict(_FULL_ARGS_FOR_ELEMENT_NULL.get(name, {}))
        args[param_name] = [None]  # 元素级 null

        result = await funcCore.run_tool_call(json.dumps(args), _ctx(name))

        assert result["success"] is False, (name, param_name, result)
        assert result.get("error_code") == toolArgsValidation.INVALID_TOOL_ARGS, (
            name, param_name, result,
        )
        assert _LEGACY_MESSAGE_MARK not in result["message"], (name, param_name, result)
        blocked += 1

    assert blocked == 13


@pytest.mark.asyncio
async def test_element_null_rejected_tsp_remote() -> None:
    """TSP 远端：元素级 null 必须**在转发前**被拦下（`call_tool` 零调用）。

    这是 H2 里最严重的一格：修前远端 `{"tags":[null]}` 返回 `success: true` 且
    `call_tool` 真的被调用了 —— 非法参数**已经越过进程边界**发给对端，对端可能
    已按自己逻辑落库/建容器，**副作用收不回**。所以这里必须同时断言
    `call_tool.assert_not_called()`，光断言 `success is False` 不够。
    """

    schema = {
        "type": "object",
        "properties": {"tags": {"type": "array", "items": {"type": "string"}}},
        "required": [],
    }
    driver = _make_tsp_driver([_remote_tool("remote_tags", schema)])

    result = await driver._execute_tsp_tool('{"tags": [null]}', _ctx("remote_tags"))

    assert result["success"] is False, result
    assert result.get("error_code") == toolArgsValidation.INVALID_TOOL_ARGS, result
    assert _LEGACY_MESSAGE_MARK not in result["message"], result
    # 硬证据：脏参数没有越过进程边界。
    driver._client.call_tool.assert_not_called()


@pytest.mark.asyncio
async def test_field_level_null_still_accepted_after_element_fix() -> None:
    """反向对照：收紧豁免面后，**字段整体为 null** 仍必须放行（防下次把豁免整个删掉）。

    这条盯着两个方向：
    - `{"timezone": null}`（非必填 str 参数整体 null）⇒ 放行
    - `{"depends_on": null}`（非必填 array 参数整体 null）⇒ 放行
      —— 后者尤其重要：H2 的修法若写成「array 一律不许 null」就会误杀它。
    """

    first = await funcCore.run_tool_call('{"timezone": null}', _ctx("get_time"))
    assert first.get("error_code") != toolArgsValidation.INVALID_TOOL_ARGS, first

    second = await funcCore.run_tool_call(
        '{"title": "t", "assignee_id": 38, "depends_on": null}', _ctx("create_task")
    )
    assert second.get("error_code") != toolArgsValidation.INVALID_TOOL_ARGS, second


# --------------------------------------------------------------------------- #
# 穷举型判据（H3 熔断前置）：按 schema 递归生成**所有**可注入 null 的位置
# --------------------------------------------------------------------------- #


def _build_valid_args(parameters: dict) -> dict:
    """按 schema 造一份**合法**参数（合法打底）。

    纪律（#189 实测教训）：判据造参必须「合法打底 + 单点注入」—— 否则 `required`
    错误会**前置遮住**后置缺陷（我先前的 2/10 vs 实际 10/10 就是这么来的）。
    """

    args: dict = {}
    for name in parameters.get("required") or []:
        prop = (parameters.get("properties") or {}).get(name) or {}
        args[name] = _sample_value(prop)
    return args


def _sample_value(prop: dict) -> Any:
    """按单个属性的 schema 造一个合法值。"""

def _sample_value(prop: dict) -> Any:
    """按单个属性的 schema 造一个合法值（按**非 null 分支**造，`type` 可能是数组）。"""

    if "enum" in prop:
        return prop["enum"][0]
    type_name = _non_null_type(prop)
    if type_name == "string":
        return "x"
    if type_name == "integer":
        return 1
    if type_name == "number":
        return 1.5
    if type_name == "boolean":
        return True
    if type_name == "array":
        return [_sample_value(prop.get("items") or {})]
    if type_name == "object":
        return {}
    return "x"


def _allows_null(prop: dict) -> bool:
    """该属性 schema **是否声明允许 null**（#195 之后唯一正确的判定依据）。

    改造前判据只能按「是否必填」猜 nullability；现在 schema 自己说了算：
    `Optional[T]` ⇒ `{"type": [T, "null"]}` 或 `{"anyOf": [..., {"type": "null"}]}`。
    """
    if "null" in _declared_types(prop):
        return True
    any_of = prop.get("anyOf")
    return isinstance(any_of, list) and {"type": "null"} in any_of


def _injection_points(parameters: dict) -> list[tuple[str, str, bool]]:
    """枚举所有可注入 null 的位置 ⇒ [(位置标识, 要发送的 function_args 原文, 是否应放行)]。

    返回的是**已序列化的 function_args 字符串**，因为 P0（根节点）要发的字面量就是
    `null`，不是 `{}` —— 用 dict 承载会把「整份参数为 null」偷偷变成「空对象」。

    - **P0 根节点**：整份参数为 `null` ⇒ 永远应拦
    - **P1 顶层字段**：`required` ⇒ 应拦；非必填 ⇒ 应放行（`Optional` 语义）
    - **P2 数组元素**：`items` 声明不允许 null ⇒ 应拦
    - **P3 嵌套 object 内部 key**：schema 为裸 `{"type":"object"}` 时**无可枚举的
      `properties`** ⇒ 本单表达力边界，不计入可测位置（不手写规则去补）
    """

    points: list[tuple[str, str, bool]] = []
    required = set(parameters.get("required") or [])
    props = parameters.get("properties") or {}

    # P0：根节点 —— 直接发字面量 `null`（不是 `{}`）
    points.append(("P0:root", "null", False))

    for name, prop in props.items():
        # P1：顶层字段整体 null
        base = _build_valid_args(parameters)
        base.pop(name, None)  # 注入位置本身先摘掉，避免"必填缺失"与"注入 null"混为一谈
        args = {**base}
        args[name] = None
        # 必填 ⇒ 应拦；非必填 ⇒ **由 schema 自己说**能不能 null（#195）。
        # #195 之前这里是「非必填 ⇒ 放行」——那条线是按默认值切的，而 nullability 该按
        # 注解切；两条线不重合的 19 个字段（非必填、注解不含 None）就是本单的缺陷面。
        points.append((f"P1:{name}", json.dumps(args), _allows_null(prop)))

        # P2：数组元素（每个 array 取「第 2 个元素」注入 null，保留 1 个合法元素；
        # 本单 13 个 array 的 items 类型各自统一，同一 array 内元素类型不异构，
        # 故单点注入足以覆盖整类。`items` 缺 `type` 但有 `enum` 的 2 个
        # （update_task.clear_fields / list_tasks.status）由 `_sample_value` 取首枚举值。）
        if "array" in _declared_types(prop):
            items = prop.get("items") or {}
            element = _sample_value(items)
            args2 = _build_valid_args(parameters)
            args2[name] = [element, None]
            points.append((f"P2:{name}[1]", json.dumps(args2), False))

        # P3：嵌套 object 内部 key —— 只有 schema 真的给了 `properties` 才可枚举
        if "object" in _declared_types(prop):
            inner_props = prop.get("properties") or {}
            inner_required = set(prop.get("required") or [])
            for inner_name in inner_props:
                args3 = _build_valid_args(parameters)
                args3[name] = {inner_name: None}
                points.append(
                    (f"P3:{name}.{inner_name}", json.dumps(args3), inner_name not in inner_required)
                )
    return points


@pytest.mark.asyncio
async def test_null_injection_exhaustive_table() -> None:
    """**穷举**：按 schema 递归枚举所有可注入 null 的位置，逐处断言「是否声明允许」并成表。

    目的不是抓当前 bug，而是**证明不存在第三格** —— 本单已因「按 `path` 长度
    枚举漏格」被打回两次（`len==0` 修了、`len>=2` 漏了）。人肉枚举长度不可靠，
    所以这里按 schema 结构枚举位置。

    ## 四个数（与三个锚点交叉校验枚举器自身）
    - `P0` 根节点 = 工具数
    - `P1-required` / `P1-optional`（预期相反：前者拦、后者放行）
    - `P2` 数组元素
    - `P3` 嵌套 object 字段数（内部 key 因 schema 为裸 `{"type":"object"}` 不可枚举）

    ## 枚举器自校验
    `get_all_tools()` == 33、`get_all_tools(include_container_tools=True)` == 38 ——
    任一不等说明枚举器自己漏了工具（38 踩过：只拿 `_func_tools` 漏掉容器组 5 个）。
    （#189 时为 32/37；webfetch 进默认组后为 33/38。）
    """

    from service.funcToolService.funcToolType import get_function_metadata

    # ---- 锚点自校验：枚举器不许漏工具（用注册面，不受 D2 暴露面影响）----
    default_tools = funcCore.get_all_tools()
    all_tools = funcCore.get_all_tools(include_container_tools=True)
    assert len(default_tools) == DEFAULT_TOOL_COUNT, len(default_tools)
    assert len(all_tools) == DEFAULT_TOOL_COUNT + CONTAINER_TOOL_COUNT, len(all_tools)

    p0 = p1_required = p1_optional = p2 = p3_fields = p3_enumerable = 0
    mismatched: list[str] = []
    table: list[str] = []

    for tool in all_tools:
        name = tool.function.name
        func_tool = funcCore.get_func_tool(name)
        assert func_tool is not None, name
        parameters = get_function_metadata(name, func_tool.callable)["parameters"]

        points = _injection_points(parameters)
        for label, raw_args, should_pass in points:
            if label == "P0:root":
                p0 += 1
            elif label.startswith("P1:"):
                if should_pass:
                    p1_optional += 1
                else:
                    p1_required += 1
            elif label.startswith("P2:"):
                p2 += 1
            elif label.startswith("P3:"):
                p3_enumerable += 1

            result = await funcCore.run_tool_call(raw_args, _ctx(name))
            rejected = _rejected_by_schema(result)

            ok = rejected != should_pass  # 应拦 ⇒ rejected True；应放行 ⇒ rejected False
            if not ok:
                mismatched.append(
                    f"{name} {label} 期望{'放行' if should_pass else '拦'} "
                    f"实际{'拦' if rejected else '放行'} {result.get('message', '')[:60]}"
                )
            table.append(
                f"{name:22s} {label:26s} 期望={'放行' if should_pass else '拦  '} "
                f"实际={'拦  ' if rejected else '放行'} {'OK' if ok else '**不符**'}"
            )

        p3_fields += sum(
            1 for prop in (parameters.get("properties") or {}).values()
            if "object" in _declared_types(prop) and not (prop.get("properties") or {})
        )

    # ---- 四个数独立成表（拆开报，不许只报总数）----
    assert p0 == DEFAULT_TOOL_COUNT + CONTAINER_TOOL_COUNT, f"P0={p0}"
    # 108 = 43 + 65。**这里必须拆开看两个口径**（否则「总数对」会掩盖反向变动）：
    #   · `p1_required` 数的是「P1 注入后**应被拦**的字段」，不是 `required` 列表长度：
    #     24 个 required + 19 个「有默认值但注解不含 None」= 43
    #     （`save_role_template.type/soul` 改为 Optional 后 required 少了 2；
    #      #196 前：36 required + 19 = 55，见
    #      `test_tool_schema_nullable.NON_OPTIONAL_BUT_DEFAULTED` 注释）
    #   · `p1_optional` = 108 - 43 = 65（#196 前 100 - 55 = 45）
    # 108 = 全部参数数（38 工具，含容器组）。#196 前为 100。
    assert p1_required + p1_optional == 108, (p1_required, p1_optional)
    assert p1_required == 43, p1_required
    assert p1_optional == 65, p1_optional
    assert p2 == 13, f"P2={p2}"
    assert p3_fields == 4, f"P3字段={p3_fields}"
    # P3 内部 key 为 0 ⇒ 本单表达力边界（裸 {"type":"object"} 无 properties），不是漏点
    assert p3_enumerable == 0, f"P3内部key={p3_enumerable}"

    total = p0 + p1_required + p1_optional + p2 + p3_enumerable
    # 159 = 38 + 43 + 65 + 13 + 0
    # （#196 前 148 = 38 + 55 + 45 + 10 + 0；#189 时 146 = 37 + 35 + 64 + 10 + 0）
    assert total == 159, total
    assert not mismatched, f"{len(mismatched)} 处不符预期：\n" + "\n".join(mismatched)


# --------------------------------------------------------------------------- #
# 既有行为不被误杀
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_explicit_null_on_optional_param_is_still_accepted() -> None:
    """显式传 `null` 给可选参数仍是合法调用。

    schema 生成器把 `Optional[T]` 剥成 `T`，`null` 的合法性在 schema 里已经丢失；若照
    schema 硬拦，「传 null 走默认值」就从合法变成非法 —— 那是本单新增的误杀。
    """

    result = await funcCore.run_tool_call('{"timezone": null}', _ctx("get_time"))

    assert result.get("error_code") != toolArgsValidation.INVALID_TOOL_ARGS, result


# --------------------------------------------------------------------------- #
# 单点收口的旁路证明（小马哥 1007 决策 B 的验收条件）
# --------------------------------------------------------------------------- #


def _registry_host() -> Any:
    """构造一个只带 tool_registry 的 host（driver 启动时用它注册 handler）。"""

    from service.agentService.toolRegistry import AgentToolRegistry

    host = MagicMock()
    host.gt_agent = MagicMock(id=1)
    host.tool_registry = AgentToolRegistry()
    host.system_prompt = "p"
    host.agent_workdir = "/tmp"
    return host


def _handlers_of(host: Any) -> dict[str, Any]:
    registry = host.tool_registry
    return {
        name: registry.get_registered_tool(name).handler
        for name in registry.list_registered_tool_names()
    }


@pytest.mark.asyncio
async def test_local_tool_handlers_all_route_to_run_tool_call() -> None:
    """**无旁路证明**：三个 driver 注册的本地工具 handler 都是 `run_tool_call` 本身。

    小马哥 1007 决策 B：校验只做单点（`run_tool_call`），因此必须证明「不存在直接
    `func(**args)` 的旁路」。这里用**身份相等**（`is`）而不是行为等价 —— 行为等价
    在「handler 是包装函数、内部又调了一次 func」时也会成立，挡不住旁路。

    三个 driver 各自的注册点：
    - `NativeAgentDriver.startup` / `ClaudeSdkAgentDriver.startup`：`get_tools()`
    - `TspAgentDriver._register_host_tools`：`get_tools(include_container_tools=True)`
    - `TspAgentDriver._register_host_tools` 末尾另把远端工具注册成 `_execute_tsp_tool`
      （那条路径的校验在 #189 里由 `tspDriver` 自己调同一个 `validate_tool_args`，见
      `test_all_tsp_remote_tools_reject_invalid_args`）。
    """

    from service.agentService.driver.base import AgentDriverConfig
    from service.agentService.driver.claudeSdkDriver import ClaudeSdkAgentDriver
    from service.agentService.driver.nativeDriver import NativeAgentDriver
    from service.agentService.driver.tspDriver import TspAgentDriver

    expected = funcToolService.run_tool_call

    # native
    native_host = _registry_host()
    await NativeAgentDriver(
        native_host, AgentDriverConfig(driver_type="native", options={})
    ).startup()
    native_handlers = _handlers_of(native_host)
    assert len(native_handlers) == DEFAULT_TOOL_COUNT
    assert all(handler is expected for handler in native_handlers.values()), [
        name for name, handler in native_handlers.items() if handler is not expected
    ]

    # tsp：本地组含容器工具（38 个），远端组此时为空
    tsp_host = _registry_host()
    tsp_driver = TspAgentDriver(tsp_host, AgentDriverConfig(driver_type="tsp", options={}))
    tsp_driver._client = MagicMock()
    tsp_driver._register_host_tools()
    tsp_handlers = _handlers_of(tsp_host)
    assert len(tsp_handlers) == DEFAULT_TOOL_COUNT + CONTAINER_TOOL_COUNT
    assert all(handler is expected for handler in tsp_handlers.values()), [
        name for name, handler in tsp_handlers.items() if handler is not expected
    ]

    # claude sdk：启动要连 SDK，这里把 client / options 都替掉（只关心注册）
    claude_host = _registry_host()
    with patch.object(csd_module, "ClaudeSDKClient") as client_cls, patch.object(
        csd_module, "ClaudeAgentOptions", MagicMock()
    ):
        client_cls.return_value.connect = AsyncMock()
        driver = ClaudeSdkAgentDriver(
            claude_host, AgentDriverConfig(driver_type="claude_sdk", options={})
        )
        await driver.startup()
    claude_handlers = _handlers_of(claude_host)
    assert len(claude_handlers) == DEFAULT_TOOL_COUNT
    assert all(handler is expected for handler in claude_handlers.values()), [
        name for name, handler in claude_handlers.items() if handler is not expected
    ]


@pytest.mark.asyncio
async def test_invalid_args_are_rejected_through_registry_entry_point() -> None:
    """端到端：从**注册表入口**（模型 tool_call 的真实入口）喂非法参数 ⇒ 被校验拦下。

    上一条证明「handler 身份是 `run_tool_call`」，这一条证明「经过那个身份之后确实拦了」。
    两条合起来才能排除「注册的是 run_tool_call，但它内部没校验」或「校验了但没走这条路径」。
    """

    from service.agentService.driver.base import AgentDriverConfig
    from service.agentService.driver.nativeDriver import NativeAgentDriver
    from util import llmApiUtil

    host = _registry_host()
    await NativeAgentDriver(host, AgentDriverConfig(driver_type="native", options={})).startup()

    # 用 `get_task_detail`（不在收敛清单内，两种 D2 模式下都可直接调用）：
    # 它的 `task_id` 是 int，喂 str 必须被拦（走 schema 的 type 约束）。
    tool_call = llmApiUtil.OpenAIToolCall(
        id="call_1", function={"name": "get_task_detail", "arguments": '{"task_id": "not-an-int"}'}
    )
    executed = await host.tool_registry.execute_tool_call(tool_call, _ctx("get_task_detail"))

    assert executed.success is False, executed.result
    assert executed.result["error_code"] == toolArgsValidation.INVALID_TOOL_ARGS, executed.result
    assert "参数校验失败" in executed.result["message"], executed.result
    # 失败文案要能定位字段：`task_id` 必须出现在里面（不许退化成无字段名的旧文案）。
    assert "task_id" in executed.result["message"], executed.result

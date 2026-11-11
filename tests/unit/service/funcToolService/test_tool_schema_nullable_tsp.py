"""#195 的 TSP 路径判据：null 豁免按 **schema 来源** 分流，不按「字段是否必填」。

## 为什么这条单独立出来（本单 §4.7.5 的硬要求）

`toolArgsValidation` 有**两个** schema 来源：

- 本地路径：`get_tool_parameters_schema(name, func)` —— 走 `get_function_metadata`，
  schema 由**本仓库** `python_type_to_json_schema` 产出（#195 起改用 TypeAdapter，
  能表达 nullability）；
- 外部路径：`validate_tool_args(name, args, schema=...)` —— schema 由调用方给
  （目前唯一调用方 `tspDriver._execute_tsp_tool`，取自 `_tsp_tools[name].function.parameters`，
  即**对端**给的 schema，生成器不归本仓库管）。

## 分流规则（38 B1 打回后定，取代「远端也严格」的旧写法）

| 路径 | null 豁免 |
| --- | --- |
| 本地（`func=`）| **不豁免**：19 拒 / 41 放行（`test_tool_schema_nullable.py`）|
| 外部（`schema=`）| **保留** `_is_ignorable_null`：单个非必填字段整体 null 放行；根节点 / 元素级 / 必填字段仍拒 |

### 为什么外部路径必须保留豁免（B1 的实测依据，不许再改回去）

gtsp v0.4.19（`assets/execute/gtsp/gtsp-darwin-arm64`，md5 `b32f5cdc…`）`schema -o`
的 11 个工具 / 39 个 property 里，**`default` 0 个、schema 含 null 的 0 个**，
其中 **26 个「非必填但 schema 无 null」**字段。这 26 个从基线「26/26 放行」变成
交付「26/26 全拒」就是 B1。而直连 gtsp 本体实测 14/26 **ACCEPT** null
（`read_file.encoding`、`grep_search.path` 等）⇒ 严格校验打死了对端合法写法。

### 反面：豁免恢复不能把 H1/H2 一起放回来

所以本文件里除了「26 个放行」，还并列了「根节点 null 仍拒 / 元素级 null 仍拒 /
必填字段 null 仍拒」——豁免面必须**恰为路径长度 1 的非必填字段**。
"""

from __future__ import annotations

import gc
import json

import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from service.funcToolService import core as funcCore
from service.funcToolService import toolArgsValidation
from service.roomService import ToolCallContext

_REPO_ROOT = Path(__file__).resolve().parents[4]
#: 真实 gtsp schema 的快照（`gtsp-* schema -o` 的产物，含 `__provenance__`）。
#: 用它是为了「不依赖运行时能起 gtsp」也能复现 B1；另有 `test_gtsp_live_schema_matches_fixture`
#: 保证快照没腐化。
_GTSPS_SCHEMA_FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "gtsp_tool_schema.json"

#: 38 报的 B1 名单：gtsp 11 工具中「非必填但 schema 无 null」的 26 个字段。
#: 写死作**外部锚点**（从 schema 现算会跟着被测代码一起漂移）。
_GTSPS_NULL_CASES: list[tuple[str, str]] = [
    ("list_dir", "depth"),
    ("list_dir", "ignore_patterns"),
    ("list_dir", "limit"),
    ("list_dir", "recursive"),
    ("read_file", "encoding"),
    ("read_file", "end_line"),
    ("read_file", "start_line"),
    ("write_file", "encoding"),
    ("edit", "allow_multiple"),
    ("edit", "instruction"),
    ("grep_search", "case_sensitive"),
    ("grep_search", "context"),
    ("grep_search", "exclude_pattern"),
    ("grep_search", "fixed_strings"),
    ("grep_search", "include_pattern"),
    ("grep_search", "max_matches_per_file"),
    ("grep_search", "path"),
    ("grep_search", "total_max_matches"),
    ("glob", "case_sensitive"),
    ("glob", "path"),
    ("execute_bash", "description"),
    ("execute_bash", "run_in_background"),
    ("execute_bash", "task_timeout"),
    ("execute_bash", "timeout_action"),
    ("process_output", "block"),
    ("process_output", "timeout"),
]


def _ctx(tool_name: str) -> ToolCallContext:
    return ToolCallContext(agent_id=1, team_id=1, tool_name=tool_name)


def _rebuild_driver():
    """构造一个 TspAgentDriver（client 用 MagicMock，进程视为存活）。"""

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
    return driver


@pytest.fixture(autouse=True)
def _loaded_tools():
    funcCore.load_func_tools()
    funcCore.load_container_func_tools()
    toolArgsValidation.clear_schema_cache()
    yield
    toolArgsValidation.clear_schema_cache()


@pytest.fixture(scope="module")
def gtsp_tools() -> list[dict[str, Any]]:
    payload = json.loads(_GTSPS_SCHEMA_FIXTURE.read_text(encoding="utf-8"))
    tools = payload["tools"]
    assert isinstance(tools, list) and len(tools) == 11, len(tools)
    return tools


@pytest.fixture(scope="module")
def gtsp_schemas(gtsp_tools: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {tool["name"]: tool["input_schema"] for tool in gtsp_tools}


def _legal_value(prop: dict[str, Any]) -> Any:
    """按 gtsp schema 造一个**合法值**（按非 null 分支造）。"""

    declared = prop.get("type")
    type_name = next((t for t in (declared if isinstance(declared, list) else [declared]) if t and t != "null"), None)
    if type_name == "array":
        return []
    if type_name == "object":
        return {}
    if type_name == "integer":
        return 1
    if type_name == "number":
        return 1.5
    if type_name == "boolean":
        return False
    return "x"


def _gtsp_args(schema: dict[str, Any], field: str) -> dict[str, Any]:
    """合法打底（补齐全部 required）+ 单点把 `field` 置 `null`。"""

    properties = schema.get("properties") or {}
    required = list(schema.get("required") or [])
    assert field not in required, f"{field} 是必填，不属于 B1 名单"
    args = {name: _legal_value(properties[name]) for name in required}
    args[field] = None
    return args


# --------------------------------------------------------------------------- #
# ① 本地路径：TSP driver 拿到的本地工具 schema 来自新生成器
# --------------------------------------------------------------------------- #


def test_tsp_driver_local_tools_carry_new_schema() -> None:
    """TSP driver 拿到的本地工具 schema 必须是新形态（nullability 已在 schema 里）。

    `_local_tools` 就是 `_register_host_tools()` 注册给模型的那批工具；它们的
    `parameters.model_dump()` 与校验层用的是同一份结构，所以这里直接断言形态：
    - `get_time.timezone`（`Optional[str]`）⇒ `type` 含 `"null"`；
    - `create_task.priority`（`str`，有默认值）⇒ `type` 恰为 `"string"`，**不含** null。
    若将来有人给 TSP 路径塞回一份旧形态 schema，这条立刻红。
    """

    driver = _rebuild_driver()
    by_name = {tool.function.name: tool for tool in driver._local_tools}

    assert "get_time" in by_name and "create_task" in by_name

    timezone = by_name["get_time"].function.parameters.model_dump()["properties"]["timezone"]
    assert timezone["type"] == ["string", "null"], timezone

    priority = by_name["create_task"].function.parameters.model_dump()["properties"]["priority"]
    assert priority["type"] == "string", priority


# --------------------------------------------------------------------------- #
# ② 分流判据（本文件的核心）：**同一份 schema、同一个字段**，两条入口给相反结论
# --------------------------------------------------------------------------- #


def test_null_policy_is_decided_by_schema_source_not_by_requiredness() -> None:
    """同一个「非必填但 schema 无 null」的字段：本地拒、外部放行。

    ⚠️ 这是分辨「按来源分流」与「按必填分流」的关键一条：

    - 旧实现（基线）：两条入口都**放行**；
    - #195 首版（被 B1 打回）：两条入口都**拒**；
    - 现在：本地**拒**（schema 能表达 null，不该放）、外部**放行**（schema 表达不了）。

    只断言一侧会漏掉「两条入口行为相同」的错误实现。
    """

    # 本地路径：`create_task.description` 是 `str` + 有默认值 ⇒ schema 无 null ⇒ 必须拒
    local = funcCore.get_func_tool("create_task")
    assert local is not None
    local_rejected = toolArgsValidation.validate_tool_args(
        "create_task",
        {"title": "t", "assignee_id": 38, "description": None},
        func=local.callable,
    )
    assert local_rejected is not None, "本地路径未拦住 create_task.description=null"
    assert local_rejected.get("error_code") == toolArgsValidation.INVALID_TOOL_ARGS

    # 外部路径：拿到同一份「表达不出 null」的 schema ⇒ 必须放行（否则就是 B1）
    local_schema = toolArgsValidation.get_tool_parameters_schema("create_task", local.callable)
    assert local_schema is not None
    external_accepted = toolArgsValidation.validate_tool_args(
        "create_task",
        {"title": "t", "assignee_id": 38, "description": None},
        schema=local_schema,
    )
    assert external_accepted is None, (
        "外部 schema 路径把「非必填字段整体 null」拒了 —— 这是 B1 的形态："
        f"{external_accepted}"
    )


# --------------------------------------------------------------------------- #
# ③ 真实 gtsp schema：26 个字段传 null 必须放行（B1 的正面判据）
# --------------------------------------------------------------------------- #


def test_gtsp_real_schema_accepts_null_for_all_26_fields(gtsp_schemas) -> None:
    """用**真实 gtsp schema**（非手造）逐个覆盖 B1 名单的 26 个字段 ⇒ 全部放行。

    手造 schema 发现不了 B1（首版那条用例就是手造的、断言「必拒」，把错误行为钉成了
    期望值）。这里的数据来自 `gtsp-* schema -o`，是**外部锚点**。

    三色读数（实测，见 report §B1）：
    - 基线 `e2d63303`：26/26 放行；
    - 首版交付 `2b90245b`：**26/26 全拒**（B1 就是这里暴露的）；
    - 本版：26/26 放行。
    """

    assert len(_GTSPS_NULL_CASES) == 26

    failures: list[str] = []
    for tool_name, field in _GTSPS_NULL_CASES:
        schema = gtsp_schemas[tool_name]
        args = _gtsp_args(schema, field)
        result = toolArgsValidation.validate_tool_args(tool_name, args, schema=schema)
        if result is not None:
            failures.append(f"{tool_name}.{field} 被拒：{result.get('message')}")

    assert not failures, "gtsp 合法 null 被误杀：\n" + "\n".join(failures)


def test_gtsp_schema_really_cannot_express_null(gtsp_tools) -> None:
    """B1 的**前提**判据：gtsp 这份 schema 自身一个 null 都没表达。

    如果哪天 gtsp 升级到能表达 null（`type: [t, "null"]`），那「保留豁免」的理由就
    弱了 —— 这条会红，提醒重新评估分流规则，而不是让理由悄悄过期。
    """

    null_expressible: list[str] = []
    total_props = 0
    for tool in gtsp_tools:
        schema = tool["input_schema"]
        for name, prop in (schema.get("properties") or {}).items():
            total_props += 1
            declared = prop.get("type")
            types = declared if isinstance(declared, list) else [declared]
            if "null" in types:
                null_expressible.append(f"{tool['name']}.{name}")

    assert total_props == 39, total_props
    assert null_expressible == [], f"gtsp schema 已能表达 null，分流理由需重估：{null_expressible}"


# --------------------------------------------------------------------------- #
# ④ 反面：豁免恢复不许把 H1/H2 一起放回来
# --------------------------------------------------------------------------- #


def test_external_schema_still_rejects_root_null(gtsp_schemas) -> None:
    """根节点 null（H1）：整份参数是 `null` ⇒ 外部路径**仍必须拒**。"""

    schema = gtsp_schemas["read_file"]
    result = toolArgsValidation.validate_tool_args("read_file", None, schema=schema)
    assert result is not None, "外部路径放过了根节点 null（H1 回潮）"
    assert result.get("error_code") == toolArgsValidation.INVALID_TOOL_ARGS


def test_external_schema_still_rejects_required_field_null(gtsp_schemas) -> None:
    """必填字段整体 null ⇒ 外部路径**仍必须拒**（豁免面不含必填）。"""

    schema = gtsp_schemas["read_file"]
    assert "file_path" in (schema.get("required") or [])
    result = toolArgsValidation.validate_tool_args("read_file", {"file_path": None}, schema=schema)
    assert result is not None, "外部路径放过了必填字段 null"
    assert result.get("error_code") == toolArgsValidation.INVALID_TOOL_ARGS


def test_external_schema_still_rejects_element_level_null(gtsp_schemas) -> None:
    """容器元素级 null（H2）：`len(path)>=2` ⇒ 外部路径**仍必须拒**。"""

    schema = gtsp_schemas["list_dir"]
    result = toolArgsValidation.validate_tool_args(
        "list_dir", {"dir_path": "x", "ignore_patterns": [None]}, schema=schema,
    )
    assert result is not None, "外部路径放过了元素级 null（H2 回潮）"
    assert result.get("error_code") == toolArgsValidation.INVALID_TOOL_ARGS


# --------------------------------------------------------------------------- #
# ⑤ 端到端：非法 null 仍不得越过进程边界
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_tsp_execute_rejects_illegal_null_before_crossing_process_boundary() -> None:
    """端到端：**必填**字段传 null ⇒ 不进 `call_tool`（非法参数不得越过进程边界）。

    ⚠️ 这条原先是「非必填字段传 null 必拒」，那是 B1 的错误期望（对端其实接受）。
    现在靶子换成**真正非法**的形态：必填字段整体 null。豁免面不含必填字段
    （`path[0] in required ⇒ return False`），所以这条在任何一版实现下都必须绿 ——
    它防的是「为了修 B1 把豁免放大成『null 一律放过』」。
    """

    from util import llmApiUtil

    schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "路径"},
            "depth": {"type": "integer", "description": "深度"},
        },
        "required": ["path"],
    }
    tool = llmApiUtil.OpenAITool(
        function=llmApiUtil.OpenAIFunction(
            name="remote_read",
            description="remote read",
            parameters=llmApiUtil.OpenAIFunctionParameter(type="object", properties={}, required=[]),
        )
    )
    tool.function.parameters = schema  # type: ignore[assignment]

    driver = _rebuild_driver()
    driver._tsp_tools = {"remote_read": tool}

    # 必填字段 null ⇒ 拒，且不越界
    result = await driver._execute_tsp_tool(json.dumps({"path": None}), _ctx("remote_read"))
    assert result.get("error_code") == toolArgsValidation.INVALID_TOOL_ARGS, result
    driver._client.call_tool.assert_not_called()

    # 对照：同一工具的非必填字段 null ⇒ 放行并真的调用（不是「一律拦」）
    await driver._execute_tsp_tool(json.dumps({"path": "x", "depth": None}), _ctx("remote_read"))
    driver._client.call_tool.assert_called_once()


# --------------------------------------------------------------------------- #
# ⑥ 快照防腐：checked-in 的 gtsp schema 必须与真实二进制一致
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(sys.platform != "darwin", reason="本机快照取自 darwin；其他平台由 CI 另行覆盖")
def test_gtsp_live_schema_matches_fixture(gtsp_tools) -> None:
    """现场跑 `gtsp-* schema -o` 与 checked-in 快照逐字节比对（防快照腐化）。

    如果 gtsp 升级导致 schema 变化，这条先红 —— 那时不是「改快照让它绿」，
    而是要重新评估 B1 名单（26 个字段可能变了）与「保留豁免」的理由是否还成立。
    """

    import appPaths

    binary = appPaths.get_gtsp_binary_path()
    assert "gtsp" in binary

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "schema.json"
        proc = subprocess.run(
            [binary, "schema", "-o", str(out)], capture_output=True, text=True, timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        live = json.loads(out.read_text(encoding="utf-8"))

    assert live == gtsp_tools, "gtsp schema 与 tests/fixtures/gtsp_tool_schema.json 不一致"

    # 本用例要起真实子进程；显式回收，避免把 GC 时点推到后续用例上
    # （未回收的 transport 在别人用例里触发 PytestUnraisableExceptionWarning）。
    gc.collect()

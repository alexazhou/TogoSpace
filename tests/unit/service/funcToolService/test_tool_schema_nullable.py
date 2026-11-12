"""#195：`Optional[T]` 不再剥成 `T` —— null 合法性由 schema 自己表达。

## 这两条判据防的是什么

改造前 `python_type_to_json_schema` 把 `Optional[T]` 剥成 `T`，「能不能传 null」在
schema 里丢失，于是校验层用 `_is_ignorable_null` 按**「是否必填」**放行 null。两条线
（默认值 / 注解是否含 None）不重合的 **19 个字段**（非必填、注解不含 None）因此接受
`null`，一路撞到 `NOT NULL constraint failed`，用户看到裸数据库错误。

## 为什么必须**一对**判据（缺一条都会做出错实现）

- `test_field_null_rejected_for_all_non_optional_params`：19 个字段传 null 必拒；
- `test_field_null_accepted_for_all_optional_params`：62 个 `Optional` 字段传 null
  必须**仍放行**。

（两个数字随 #196 更新：19→19 —— 表内 `save_agent.driver` 移出（见下）+ `delete_dept` /
`delete_room` 各新增一个 `dry_run: bool=False` 移入，净变化 0；41→62 见
`OPTIONAL_WITH_DEFAULT` 上方注释。）

只验第一条，会做出「一律拒 null」的实现（`get_time(timezone=null)`、`list_tasks(limit=null)`
这些现网合法用法全被误杀）；只验第二条，则本单的缺陷原封不动。

## 打底纪律（#189 踩过的坑）

判据必须「**合法打底 + 单点注入**」：打底要补齐**全部 required**，否则 `required` 错误
会前置遮住后置的 null 错误，判据假绿。
"""

from __future__ import annotations

import inspect
import json
from typing import Any, Union, get_args, get_origin, get_type_hints

import pytest
from types import UnionType

from service.funcToolService import core as funcCore
from service.funcToolService import toolArgsValidation
from service.funcToolService.funcToolType import get_function_metadata
from service.roomService import ToolCallContext

#: 任务单 §1 列出的 19 个字段，#196 后仍为 19 个（移出 `save_agent.driver`、移入两个
#: `dry_run`；**外部锚点**，故意写死，从代码里现算会跟着实现一起漂移）。
#: 口径：默认工具组（33 个工具 / 103 个参数）中「有默认值、但注解不含 None」的参数。
#: （#196 后参数数 95 → 103：+3 个 `clear_fields`、+2 个 `dry_run`、+2 个 `name`、
#: +1 个 `get_dept_info.name`，改名 2 处不计数。）
#:
#: #196 更新（19 → 19，成员有换但数量不变）：新增 `delete_dept.dry_run` / `delete_room.dry_run` 两个
#: `bool=False` 参数，同属「有默认值、注解不含 None」⇒ 必须进本表，否则它们传 null
#: 的行为无人钉住。
#:
#: ⚠️ `save_agent.driver` **在本单移出本表**（-1 出、+2 入 ⇒ 仍 19）。这是本单唯一一处
#: 「原以为要收严、实测必须放宽」的字段，详见报告 §「null 冲突消化 · driver」：
#: PATCH 语义要求「省略 = 保持现值」可表达，而 Python 只有 `None` 默认值能表达它；
#: `driver: str = "tsp"` 会让「只改 model 的 PATCH」把 native 悄悄改回 tsp（本单
#: 实测抓到，见 commit 说明）。改成 `Optional[str] = None` 后，schema 自己声明了
#: nullable ⇒ null ≡ 未传，符合 #195「合法性由 schema 表达」的哲学，不是绕它。
NON_OPTIONAL_BUT_DEFAULTED: list[tuple[str, str]] = [
    ("send_chat_msg", "insert_immediately"),
    ("finish_action", "confirm_no_need_talk"),
    ("save_agent", "overwrite_existing"),
    ("save_dept", "overwrite_existing"),
    ("delete_dept", "recursive"),
    ("delete_dept", "dry_run"),
    ("save_room", "overwrite_existing"),
    ("delete_room", "dry_run"),
    ("save_role_template", "overwrite_existing"),
    ("create_task", "description"),
    ("create_task", "priority"),
    ("create_task", "note"),
    ("update_task", "note"),
    ("get_task_detail", "event_limit"),
    ("list_task_events", "limit"),
    ("list_tasks", "open_only"),
    ("memory_list", "include_archived"),
    ("memory_search", "limit"),
    ("memory_create", "star_level"),
]

#: 任务单 §4.2 的 41 个 `Optional` 字段 + #196 新增的 21 个 = 62 个（同上：写死作外部锚点）。
#:
#: #196 更新（41 → 62，+21）：本单把 save 系列的定位/必填参数改成可选（PATCH 语义要求
#: 「未传」可达），并新增三个 `clear_fields: Optional[list[...]]`（详见报告 §「save 系列
#: required 放开」）。逐条对应：
#:   `save_agent`   + name / role_template_name / clear_fields / driver (4)
#:   `save_dept`    + name / responsibility / manager_name
#:                    / member_names / clear_fields                   (5)
#:   `save_room`    + name / member_names / initial_topic
#:                    / clear_fields                                  (4)
#:   `update_task`  + new_status / reviewer_id / clear_fields         (3)
#: （`save_room.initial_topic` 由 `str=''` 改 `Optional[str]=None` 是**必须**的：`''` 无法
#: 区分「未传」与「清空」，而 #195 之后 null 也不再是豁免通道。）
#:
#: 这张表是**外部锚点**，变化必须显式写在这里 —— 所以本单改动它是有意留痕，
#: 不视为锚点腐化。
OPTIONAL_WITH_DEFAULT: list[tuple[str, str]] = [
    ("create_container", "image"),
    ("create_container", "note"),
    ("create_task", "depends_on"),
    ("create_task", "parent_id"),
    ("create_task", "reviewer_id"),
    ("create_task", "room_id"),
    ("create_task", "status"),
    ("delete_container", "container_id"),
    ("delete_container", "record_id"),
    ("delete_dept", "dept_id"),
    ("delete_dept", "name"),
    ("delete_room", "name"),
    ("delete_room", "room_id"),
    ("get_agent_info", "agent_id"),
    ("get_agent_info", "name"),
    ("get_dept_info", "dept_id"),
    ("get_dept_info", "name"),
    ("get_room_info", "name"),
    ("get_room_info", "room_id"),
    ("get_time", "timezone"),
    ("list_role_templates", "keywords"),
    ("list_task_events", "before_id"),
    ("list_tasks", "assignee"),
    ("list_tasks", "limit"),
    ("list_tasks", "reviewer"),
    ("list_tasks", "scope"),
    ("list_tasks", "status"),
    ("list_tasks", "title_keyword"),
    ("memory_create", "tags"),
    ("memory_update", "content"),
    ("memory_update", "star_level"),
    ("memory_update", "status"),
    ("memory_update", "summary"),
    ("memory_update", "tags"),
    ("memory_update", "title"),
    ("save_agent", "agent_id"),
    ("save_agent", "allow_skills"),
    ("save_agent", "allow_tools"),
    ("save_agent", "clear_fields"),
    ("save_agent", "driver"),
    ("save_agent", "i18n"),
    ("save_agent", "model"),
    ("save_agent", "name"),
    ("save_agent", "role_template_name"),
    ("save_dept", "clear_fields"),
    ("save_dept", "dept_id"),
    ("save_dept", "i18n"),
    ("save_dept", "manager_name"),
    ("save_dept", "member_names"),
    ("save_dept", "name"),
    ("save_dept", "parent_name"),
    ("save_dept", "responsibility"),
    ("save_role_template", "i18n"),
    ("save_room", "clear_fields"),
    ("save_room", "initial_topic"),
    ("save_room", "max_rounds"),
    ("save_room", "member_names"),
    ("save_room", "name"),
    ("save_room", "room_id"),
    ("update_task", "clear_fields"),
    ("update_task", "new_status"),
    ("update_task", "reviewer_id"),
]


def _ctx(tool_name: str) -> ToolCallContext:
    return ToolCallContext(agent_id=1, team_id=1, tool_name=tool_name)


def _declared_types(prop: dict) -> set[str]:
    declared = prop.get("type")
    if isinstance(declared, str):
        return {declared}
    return {item for item in declared or [] if isinstance(item, str)}


def _sample(prop: dict) -> Any:
    """按 schema 造一个**合法**值（按非 null 分支造）。"""

    if "enum" in prop:
        return prop["enum"][0]
    any_of = prop.get("anyOf")
    if isinstance(any_of, list):
        inner = [branch for branch in any_of if branch != {"type": "null"}]
        return _sample(inner[0]) if inner else "x"
    types = _declared_types(prop) - {"null"}
    type_name = next(iter(types)) if types else None
    if type_name == "array":
        return [_sample(prop.get("items") or {})]
    if type_name == "object":
        return {}
    return {"string": "x", "integer": 1, "number": 1.5, "boolean": True}.get(type_name, "x")


def _parameters(tool_name: str) -> dict:
    func_tool = funcCore.get_func_tool(tool_name)
    assert func_tool is not None, tool_name
    return get_function_metadata(tool_name, func_tool.callable)["parameters"]


async def _inject_null(tool_name: str, param_name: str) -> dict:
    """合法打底（补齐全部 required）+ 单点注入 `null`，返回工具调用结果。"""

    parameters = _parameters(tool_name)
    required = list(parameters.get("required") or [])
    assert param_name not in required, f"{tool_name}.{param_name} 是必填参数，不属于本判据"

    args = {name: _sample(parameters["properties"][name]) for name in required}
    args[param_name] = None
    return await funcCore.run_tool_call(json.dumps(args), _ctx(tool_name))


@pytest.fixture(autouse=True)
def _loaded_tools():
    funcCore.load_func_tools()
    funcCore.load_container_func_tools()
    toolArgsValidation.clear_schema_cache()
    yield
    toolArgsValidation.clear_schema_cache()


def test_field_lists_match_signatures() -> None:
    """两张写死清单必须与真实签名一致（防清单腐化：改签名后判据还在验旧字段）。"""

    from service.funcToolService import tools

    for tool_name, param_name in NON_OPTIONAL_BUT_DEFAULTED + OPTIONAL_WITH_DEFAULT:
        func = getattr(tools, tool_name, None)
        assert func is not None, tool_name
        hints = get_type_hints(func)
        param = inspect.signature(func).parameters[param_name]
        assert param.default is not inspect.Parameter.empty, (tool_name, param_name)

        origin = get_origin(hints[param_name])
        has_none = origin in (Union, UnionType) and type(None) in get_args(hints[param_name])
        expected = (tool_name, param_name) in OPTIONAL_WITH_DEFAULT
        assert has_none is expected, (tool_name, param_name, hints[param_name])

    assert len(NON_OPTIONAL_BUT_DEFAULTED) == 19
    assert len(OPTIONAL_WITH_DEFAULT) == 62


@pytest.mark.asyncio
async def test_field_null_rejected_for_all_non_optional_params() -> None:
    """19 个「有默认值但注解不含 None」的字段传 `null` ⇒ 全部 `invalid_tool_args`。

    这是本单的正面判据。改造前这 19 个**全部放行**（`_is_ignorable_null` 按「非必填」
    豁免），`create_task.description=null` / `note=null` 会一路走到数据库
    `NOT NULL constraint failed`。
    """

    failures: list[str] = []
    for tool_name, param_name in NON_OPTIONAL_BUT_DEFAULTED:
        result = await _inject_null(tool_name, param_name)
        if result.get("error_code") != toolArgsValidation.INVALID_TOOL_ARGS:
            failures.append(f"{tool_name}.{param_name} 未被拒：{result}")

    assert not failures, "以下字段传 null 仍被放行：\n" + "\n".join(failures)


@pytest.mark.asyncio
async def test_field_null_accepted_for_all_optional_params() -> None:
    """62 个 `Optional` 字段传 `null` ⇒ 全部**不得**报 `invalid_tool_args`（防「修过头」）。

    这些是现网合法用法（`{"timezone": null}` 走默认时区等）。把 null 一律拒掉会同时
    打挂它们，且没有任何现有用例会红 —— 所以这条判据不能省。

    注意断言的是「**不是** `invalid_tool_args`」而不是「`success is True`」：参数合法
    不代表业务成功（例如 `list_tasks` 三个过滤条件都不给会报 missing_target），本判据
    只管**校验层**放不放行。
    """

    failures: list[str] = []
    for tool_name, param_name in OPTIONAL_WITH_DEFAULT:
        result = await _inject_null(tool_name, param_name)
        if result.get("error_code") == toolArgsValidation.INVALID_TOOL_ARGS:
            failures.append(f"{tool_name}.{param_name} 被误拒：{result.get('message')}")

    assert not failures, "以下 Optional 字段传 null 被误拒：\n" + "\n".join(failures)


@pytest.mark.asyncio
async def test_optional_enum_param_keeps_null_accepted() -> None:
    """`Optional[Literal[...]]` 的 null 必须放行（扁平化陷阱的钉子）。

    `create_task.status` / `list_tasks.scope` 的 schema 是
    `{"anyOf": [{"enum": [...], "type": "string"}, {"type": "null"}]}`。
    若把这种形态扁平化成 `{"enum": [...], "type": ["string", "null"]}`，jsonschema 会
    对 `null` 实例先查 `enum`（null 不在其中）而报错 ⇒ 这两个合法用法被误杀。
    这条用例同时证明「`anyOf` 分支里的 enum 仍能拦住野值」（不是用「放行一切 null」
    换来的）。
    """

    for tool_name, param_name in (("create_task", "status"), ("list_tasks", "scope")):
        accepted = await _inject_null(tool_name, param_name)
        assert accepted.get("error_code") != toolArgsValidation.INVALID_TOOL_ARGS, accepted

    # 同一字段喂野字符串仍必须被拦（enum 没丢）。
    illegal = await funcCore.run_tool_call(
        '{"title": "t", "assignee_id": 38, "status": "__not_a_status__"}', _ctx("create_task")
    )
    assert illegal.get("error_code") == toolArgsValidation.INVALID_TOOL_ARGS, illegal
    # 文案必须与 #195 之前**逐字同形**：「只能取 [...] 之一」，而不是
    # 「不满足 anyOf 约束 [{'enum': ..., 'type': 'string'}, {'type': 'null'}]」。
    # 后者是 #195 首版引入的可见退化（38 的 S1）：anyOf 把 enum 的文案盖住了。
    # `_describe` 里由 `_unwrap_nullable_any_of` 把 Optional 形态降回内层约束来保住文案。
    assert "只能取" in illegal["message"], illegal
    assert "anyOf" not in illegal["message"], illegal
    assert "'TODO'" in illegal["message"] and "'ON_HOLD'" in illegal["message"], illegal


# --------------------------------------------------------------------------- #
# 4.3 / 4.4：`description` 与 `required` 零漂移
# --------------------------------------------------------------------------- #

#: 基线 `e2d633036091ff7f157f5318ff14c05272ea235d` 上实测的
#: 「全部 38 个工具的 description + 全部参数（当时 100 个）的 description + 工具级 required」
#: 的 sha256 摘要（排序后 JSON 序列化）。**故意写死**：这是外部锚点，不是从当前代码
#: 现算的（现算会跟着实现一起漂移，等于没断言）。
#:
#: 为什么不只依赖 `tests/fixtures/tool_schema_snapshot.json`：那份快照要用
#: `scripts/tool_schema_snapshot.py --update` 更新，如果谁「改了 description + 顺手
#: update 快照」，快照用例会绿。这条摘要要求**任何** description / required 变化都必须
#: 显式改这个常量，改的时候才会看见「我在动模型的接口说明」。
#:
#: #196 更新（100 → 108 参数，digest 重算）——**这是本单唯一一次动 #195 的锚点**，
#: 理由与代价见报告 §「为何动 #195 的 BASELINE_TEXT_DIGEST 锚点」。要点：
#:   1. 参数数 100 → 108（+8）。实测差集（新树 vs `5d231bf0` 旧树，逐工具）：
#:      `save_agent`  +`clear_fields`
#:      `save_dept`   +`clear_fields`
#:      `save_room`   +`clear_fields`
#:      `delete_dept` +`dry_run`
#:      `delete_room` +`dry_run`
#:      `get_dept_info`  +`name`（原仅 `dept_id`）
#:      `get_agent_info` +`agent_id` `name`，-`agent_name`（净 +1）
#:      `get_room_info`  +`room_id` `name`，-`room_name`（净 +1）
#:      合计 +8；**两个改名**（`agent_name`→`name`、`room_name`→`name`）是唯一被删的参数。
#:   2. 工具级 required 只在 5 处变化（其余 33 个工具 required 一字未动）：
#:      `save_agent`  `["name","role_template_name"]` → `[]`
#:      `save_dept`   `["name","responsibility","manager_name","member_names"]` → `[]`
#:      `save_room`   `["name","member_names"]` → `[]`
#:      `delete_dept` `["name"]` → `[]`
#:      `delete_room` `["name"]` → `[]`
#:      前三个是 PATCH 语义要求「未传」可达（报告 §「save 系列 required 放开」）；
#:      后两个是「按 id 删 / 按名删二选一」，单给 name 才是必填是错的（dry_run 也一样）。
#:   3. 逐条 description 与其余 35 个工具的 required **仍逐字不变**——这条锚点的语义
#:      没有被稀释，只是随接口扩容重算了基线。
#: 复核方式（可复现）：`git show 5d231bf0:src/...` 旧树算得 100 参数 /
#: digest `10a72bd6…`，与旧常量逐字相同 ⇒ 本单没有在「顺手」改 description。
BASELINE_TEXT_DIGEST = "6365f3f7b61aafe96cdf76e1366d4f31f3401d0e7b798fab082d0ad257d11de9"


def _text_digest() -> tuple[str, int, int]:
    """(摘要, 工具数, 参数数)：只收 description 与 required，**不含** schema 形态。"""

    import hashlib

    snapshot: dict[str, Any] = {}
    for tool in funcCore.get_tools(include_container_tools=True):
        name = tool.function.name
        func_tool = funcCore.get_func_tool(name)
        assert func_tool is not None, name
        metadata = get_function_metadata(name, func_tool.callable)
        snapshot[name] = {
            "description": metadata["description"],
            "required": list(metadata["parameters"].get("required") or []),
            "params": {
                param: prop.get("description")
                for param, prop in metadata["parameters"]["properties"].items()
            },
        }
    payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest(), len(snapshot), sum(
        len(body["params"]) for body in snapshot.values()
    )


def test_descriptions_and_required_do_not_drift() -> None:
    """108 个参数的说明与 required 与基线**逐字相同**（本单最高风险点）。

    #195 把生成器换成 pydantic `TypeAdapter` —— 它**不产出 docstring 描述**。如果
    `_parse_param_descriptions` 的合并被漏掉，模型会失去全部参数说明，而「全量测试
    全绿」是可能发生的（schema 形态全对、用例都不看 description 是否存在）。
    这条与 `test_all_local_tools_expose_param_descriptions_and_meaningful_description`
    互补：后者查「有没有」，这条查「是不是和改前逐字一样」。
    """

    digest, tools, params = _text_digest()

    assert tools == 38, tools
    assert params == 108, params
    assert digest == BASELINE_TEXT_DIGEST, (
        "description 或 required 与基线不一致（摘要变化）。\n"
        "若确为有意变更，请先确认「模型看到的接口说明确实该变」，再更新本文件的 "
        "BASELINE_TEXT_DIGEST。"
    )

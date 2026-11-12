"""#195：`Optional[T]` 不再剥成 `T` —— null 合法性由 schema 自己表达。

## 这两条判据防的是什么

改造前 `python_type_to_json_schema` 把 `Optional[T]` 剥成 `T`，「能不能传 null」在
schema 里丢失，于是校验层用 `_is_ignorable_null` 按**「是否必填」**放行 null。两条线
（默认值 / 注解是否含 None）不重合的 **19 个字段**（非必填、注解不含 None）因此接受
`null`，一路撞到 `NOT NULL constraint failed`，用户看到裸数据库错误。

## 为什么必须**一对**判据（缺一条都会做出错实现）

- `test_field_null_rejected_for_all_non_optional_params`：19 个字段传 null 必拒；
- `test_field_null_accepted_for_all_optional_params`：65 个 `Optional` 字段传 null
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

import asyncio
import ast as _ast
import inspect
import json
import re
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
    ("save_agent", "overwrite"),
    ("save_dept", "overwrite"),
    ("delete_dept", "recursive"),
    ("delete_dept", "dry_run"),
    ("save_room", "overwrite"),
    ("delete_room", "dry_run"),
    ("save_role_template", "overwrite"),
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

#: 任务单 §4.2 的 41 个 `Optional` 字段 + #196 新增的 21 个 = 62 个，#198 再 +1
#: （`console.payload`）= 63 个，角色模板 PATCH 下沉再 +2（`save_role_template.type/soul`）
#: = **65** 个（同上：写死作外部锚点）。
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
#: ## #198 更新（62 → 63，+1：`console.payload`）
#:
#: 补的是**判据覆盖缺口**，不是功能缺陷：`console(command="help", payload=None)` 一直
#: 是 `success=True`（行为正确），但 `payload: Optional[dict] = None` 这一条从 #195
#: 起就没进本表。缺口的具体危害：**改动前**若有人把 `payload` 改成非 Optional，
#: 62 组不会红（它只遍历清单，清单里没有这个字段）；**改动后**会红，且有两处独立报出
#: —— `test_field_lists_cover_every_optional_param`（签名现算 vs 清单）与
#: `test_console_payload_null_is_accepted`（端到端传 null 被拒）。
#:
#: 为什么是**并入本表**而不是另立一张：
#: - `console` 与其他工具同源（都在 `core.get_func_tool` 注册表里，`payload` 的 schema
#:   由同一个 `get_function_metadata` 产出，形态 `{"type": ["object","null"]}`）；
#: - 另立一张表意味着两张表各自记账，长期必然分叉 —— 而「分叉」正是本单要堵的东西。
#: - 代价只有一个：取函数的方式必须换（`console` 不在 `tools` 模块里，见
#:   `_tool_callable`），这是一次性的、且换完之后对全部 65 条统一。
#:
#: ## 角色模板 PATCH 下沉（63 → 65，+2：`save_role_template.type/soul`）
#:
#: `save_role_template` 改成 PATCH 语义后，`type` / `soul` 的注解从必填 `str` 变为
#: `Optional[str] = None`，schema 随之声明 nullable。新建时仍要求 soul、type 默认
#: USER 的兜底移到工具运行时（与 `save_agent` 的「schema 放开、运行时必填」同口径）。
#:
#: 这张表是**外部锚点**，变化必须显式写在这里 —— 所以本单改动它是有意留痕，
#: 不视为锚点腐化。
OPTIONAL_WITH_DEFAULT: list[tuple[str, str]] = [
    ("console", "payload"),
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
    # #203：`container note` 的转发目标，3 个参数都是 Optional + None 默认。
    # `note=None` 与空串在 service 层是同一个意思（清空），所以 null 在这里必须
    # 被放行 —— 拦掉它会把「清空」这条合法用法一起打死。
    ("update_container_note", "container_id"),
    ("update_container_note", "note"),
    ("update_container_note", "record_id"),
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
    ("save_role_template", "soul"),
    ("save_role_template", "type"),
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


def _tool_callable(tool_name: str):
    """按名字取工具的真实可调用对象（**唯一**取函数入口）。

    为什么不用 `getattr(tools, tool_name)`（#198）：`console` **不在 `tools` 模块里**，
    它定义在 `service.funcToolService.consoleTool`（`hasattr(tools, "console")` 为 False）。
    `getattr` 写法对 62 条里的 23 个工具恰好成立，于是「换一种取法」这件事一直没被触发 ——
    直到要把 `console` 纳进来才炸。

    为什么用注册表而不是给 `console` 开特例：开特例意味着两张清单各自记不同的取法，
    长期必然分叉。注册表是**唯一事实源**，对全部 63 条统一。

    ⚠️ **必须先 load**：未 load 时 `get_func_tool` 返回 `None`（实测）。本文件有
    `_loaded_tools` autouse fixture 保证，这里的 `assert` 是兜底 —— 报「未加载」而不是
    让上层 `AttributeError` 指向一个和根因无关的地方。
    """
    func_tool = funcCore.get_func_tool(tool_name)
    assert func_tool is not None and func_tool.callable is not None, (
        f"取不到工具 {tool_name!r} 的可调用对象："
        f"「未注册」与「未 load」都会走到这里（本文件应先经 `_loaded_tools` fixture）"
    )
    return func_tool.callable


#: 两组建的**代表项**：用来判定「注册表加载完整」。
#:
#: 为什么不用「注册表条数 > 0」：注册表是两个 dict（`_func_tools` 33 个 + 容器组 5 个），
#: 只 load 前一组时条数仍是 33 > 0，而容器组整组缺失 —— 实测其中 4 个字段正在
#: `OPTIONAL_WITH_DEFAULT` 里（`create_container.image/note`、
#: `delete_container.container_id/record_id`）。条数会骗人，代表项不会。
_LOAD_PROBES: tuple[tuple[str, str], ...] = (
    ("console", "默认组（_func_tools）"),
    ("create_container", "容器组（_container_func_tools）"),
)


def _registered_tool_names() -> list[str]:
    """**注册面**的全部工具名（两组都算，不受 D2 暴露面开关影响）。

    ⚠️ 这里**不能用 `funcCore.get_tools()`** —— 那是**暴露面**：
    `get_tools()` 会按 D2 开关（`resolve_effective_visibility(...).tool_visible`）
    过滤掉被 `console` 收敛的 16 个原工具。实测 D2 隐藏模式（`TOGOSPACE_KEEP_LEGACY_ADMIN_TOOLS=0`）下
    `get_tools(include_container_tools=True)` 只剩 22 个，现算会少 36 条，
    于是 `stale` 报「清单过期 36 项」—— **完全是误报，且指令是错的**
    （会让人去删正确的清单项）。

    判据要的是「**注册了哪些工具**」（签名事实），与「**暴露给模型哪些**」（D2 策略）
    是两个维度。用后者算前者，等于让一个开关决定判据结论。

    ⚠️ 也**不能调 `load_func_tools()` / `load_container_func_tools()` 来取名字**：
    那两个函数会**重建注册表**（`_func_tools = {}` 后重新填），于是「未加载」这个
    被测状态会被自己的枚举动作抹掉 —— 自检永远绿，等于没写（本单实测：第一版
    就是这么错的，`default_only` / `container_only` 两种状态都算出 63 条）。

    所以只能**只读**私有 dict，不做任何加载。读私有在这里是必要的：判断「加载了吗」
    不能用「能加载」的接口。`getattr(..., {})` 兜底是为了让名字改动时报空列表
    （随后 `_LOAD_PROBES` 自检会报「未加载完整」），而不是 `AttributeError`。
    """
    names = list(getattr(funcCore, "_func_tools", {}))
    names.extend(getattr(funcCore, "_container_func_tools", {}))
    return names


def _declared_optional_pairs() -> set[tuple[str, str]]:
    """从**真实签名**现算「有默认值 且 注解含 None」的全部参数（同一个口径）。

    这是双向闭环的「现算」一侧。口径必须与清单注释里写的一致：
      - 跳过 `_context` 等私有参数；
      - 必须有默认值（`p.default is not empty`）；
      - 注解是 `Union[X, None]`（含 `Optional[X]` 与 `X | None` 两种写法）。

    ⚠️ **先自检加载完整**（#198 第二版修的就是这里）：两组各断一个代表项。
    不自检的后果实测过 —— 注册表没加载全时，现算集合偏小，`stale` 会报
    「清单有过期项」，把人引去删正确的清单项（其中 4 条是容器工具的唯一覆盖）。
    那比「判据不红」更糟：**判据给出错误指令**。
    """
    loaded = set(_registered_tool_names())
    missing_groups = [
        f"{name}（{label}）" for name, label in _LOAD_PROBES if name not in loaded
    ]
    assert not missing_groups, (
        "注册表未加载完整，现算结果不可信（会在下面报出假的「清单过期」）：\n  "
        + "\n  ".join(missing_groups)
        + "\n本文件应有 `_loaded_tools` autouse fixture 先 load 两组；"
        "若是手动跑探针，请先调 `load_func_tools()` + `load_container_func_tools()`。"
    )

    computed: set[tuple[str, str]] = set()
    for name in sorted(loaded):
        func = _tool_callable(name)
        hints = get_type_hints(func)
        for param_name, param in inspect.signature(func).parameters.items():
            if param_name.startswith("_"):
                continue
            if param.default is inspect.Parameter.empty:
                continue
            hint = hints[param_name]
            origin = get_origin(hint)
            if origin in (Union, UnionType) and type(None) in get_args(hint):
                computed.add((name, param_name))
    return computed



#: schema 层拒绝文案的固定前缀 —— **从源码 import，本文件不再抄一份**。
#:
#: ⚠️ **不能只看 `error_code`**：`save_agent` / `save_dept` / `save_room` / `delete_dept` /
#: `delete_room` 的 `required` 在 #196 后为空（PATCH 语义要求「未传」可达），必填校验
#: 下移到运行时，且运行时给出的码**与 schema 层同码**（小马哥 12:02 硬条件 1）。
#: 于是「两个定位参数都不给」在运行时被拒时也会带 `invalid_tool_args` ——
#: 只看码会把 27 个合法 null 注入判成「被 schema 误拒」（本单实测）。
#: 判据要问的是「**schema 层**放不放行 null」，所以必须同时匹配层文案。
#:
#: ⚠️ 单一来源（小马哥 23:08 第②件）：这里**不再硬编码**，改为从
#: `toolArgsValidation.SCHEMA_LAYER_MARK` 取。原来本文件与 `test_tool_args_validation.py`
#: 各硬编码一份同样的字符串 —— 文案一改，两份常量一起改，判据照样绿，等于没测。
SCHEMA_LAYER_MARK = toolArgsValidation.SCHEMA_LAYER_MARK


def test_schema_layer_mark_matches_the_message_actually_emitted() -> None:
    """meta 断言（小马哥 23:08 第①件）：常量与 schema 层**真实产出**的文案同源。

    为什么需要这条兜底：第②件把常量收敛成单一来源后，「常量本身写错」或「文案改了但
    常量没改」仍然不会被任何用例发现 —— 因为 63 组只是拿常量去 `in` 匹配，
    两边一起漂就一起绿。这里**构造一次必被 schema 层拒的调用**（`create_task` 的
    `description` 传 `null`，它非 `Optional` 且非 `str`），直接断言：

      1. `error_code == INVALID_TOOL_ARGS`（层码没丢）
      2. `SCHEMA_LAYER_MARK in message`（常量真的出现在真实文案里）

    这条**不依赖** 63 组、19 组或任何一组的语义方向，只问「文案长什么样」。
    """

    async def _run() -> dict:
        return await funcCore.run_tool_call(
            json.dumps({"title": "t", "assignee_id": 38, "description": None}),
            _ctx("create_task"),
        )

    result = asyncio.run(_run())

    assert result.get("error_code") == toolArgsValidation.INVALID_TOOL_ARGS, (
        f"`create_task(description=None)` 应被 schema 层以 invalid_tool_args 拒绝，实际：{result}"
    )
    assert SCHEMA_LAYER_MARK in result.get("message", ""), (
        f"SCHEMA_LAYER_MARK={SCHEMA_LAYER_MARK!r} 没有出现在真实拒绝文案里："
        f"{result.get('message')!r} —— 常量与实际文案不同步"
    )


def _declares_nullable(prop: dict) -> bool:
    """schema 是否声明了 `null` 是合法值（**读源头**，不看文案、不看层）。

    ⚠️ **不许写成 `{"type": "null"} in prop.get("anyOf", [])`**：那是字典精确相等，
    只要 schema 生成器给 null 支补一个 `description` / `title` 就漏判，而漏判的正好是
    风险最高的那两条（`create_task.status` / `list_tasks.scope`，`Optional[Literal[...]]`，
    `_flatten_nullable` 专门为它们留了不扁平化分支）。实测反例：

        {"anyOf": [..., {"type": "null", "description": "未指定"}]}   → 精确相等为 False

    所以按字段判：`branch.get("type") == "null"`。两种形态都要认：

    - `{"type": ["string", "null"]}`                       ← `initial_topic` / `driver`
    - `{"anyOf": [{"enum": [...], "type": "string"}, {"type": "null"}]}` ← 上面那两条
    """
    declared = prop.get("type")
    if isinstance(declared, list) and "null" in declared:
        return True
    any_of = prop.get("anyOf")
    return isinstance(any_of, list) and any(
        isinstance(branch, dict) and branch.get("type") == "null" for branch in any_of
    )


async def _inject_null_schema_layer(tool_name: str, param_name: str) -> None:
    """**只在 schema 层**注入 `null`，不走端到端（不经过 `run_tool_call`）。

    与 `_inject_null` 分开是结构上的隔离，不是风格偏好：

    63 组的结论是「schema 声明并放行 null」。若它和 19 组共用 `_inject_null`
    （那个 helper 走 `run_tool_call` 端到端），就共用「走端到端」这个前提 ——
    哪天有人给 `_inject_null` 加个参数或改内部，63 组会**静默回到端到端**，而 63 组
    自己一行代码都没动过。独立函数把这条耦合物理切断，守卫判据
    （`test_optional_group_does_not_go_through_end_to_end`）只是防止有人再合并回去。

    ⚠️ **必须合法打底**（#189 踩过的坑）：把全部 `required` 填上再单点注入 null。
    不打底时 required 错误会**前置遮住** null 的结论（`update_task` 只传
    `{"clear_fields": None}` 会先报「缺少必填参数 task_id」），判据假红/假绿。

    三条断言缺一不可：
      ① `schema is not None` —— 否则下面两条都在空转（`validate_tool_args` 取不到
         schema 时**也返回 None**，只断言「放行」会让 schema 整段删掉还全绿）；
      ② `_declares_nullable(prop)` —— **真正被测的事实**，读注解层，不依赖文案；
      ③ `validate_tool_args(... ) is None` —— 校验器确实放行。
    """
    func_tool = funcCore.get_func_tool(tool_name)
    assert func_tool is not None, f"{tool_name} 未注册"
    func = func_tool.callable

    schema = toolArgsValidation.get_tool_parameters_schema(tool_name, func)
    assert schema is not None, f"{tool_name}.{param_name} 取不到 schema，判据无法成立"  # 锚点①

    prop = schema["properties"][param_name]
    assert _declares_nullable(prop), (  # 锚点②
        f"{tool_name}.{param_name} 的 schema 未声明 null 合法：{prop!r}"
    )

    # 合法打底：补齐全部 required，再单点注入 null。
    required = list(schema.get("required") or [])
    assert param_name not in required, f"{tool_name}.{param_name} 是必填参数，不属于本判据"
    args: dict[str, Any] = {
        name: _sample(schema["properties"][name]) for name in required
    }
    args[param_name] = None

    assert toolArgsValidation.validate_tool_args(  # 放行
        tool_name, args, func=func
    ) is None, f"{tool_name}.{param_name} 传 null 被 schema 层拒了"


def _rejected_by_schema(result: dict) -> bool:
    """该返回体是否由 **schema 层**（而非运行时必填校验）拒绝。"""
    return (
        result.get("error_code") == toolArgsValidation.INVALID_TOOL_ARGS
        and SCHEMA_LAYER_MARK in result.get("message", "")
    )


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
    """两张写死清单的每一项都必须与真实签名一致（防清单腐化：改签名后判据还在验旧字段）。

    ⚠️ 这条是**单向**的：它只问「清单里的项，签名是否仍然合法」，**从不问**
    「签名里合法但清单没有的项，是不是漏了」。

    ⇒ 清单少一项、少十项，只要剩下的都合法，它永远绿。缺口由此而来（#198 的
    `console.payload` 就是这么漏掉的：`payload: Optional[dict] = None` 从 #195 起
    就合法，但没人把它加进清单）。

    反向由 `test_field_lists_cover_every_optional_param` 补 —— 两条必须都在：
    这条保证「清单不透支」，那条保证「清单不遗漏」。
    """

    for tool_name, param_name in NON_OPTIONAL_BUT_DEFAULTED + OPTIONAL_WITH_DEFAULT:
        func = _tool_callable(tool_name)
        hints = get_type_hints(func)
        param = inspect.signature(func).parameters[param_name]
        assert param.default is not inspect.Parameter.empty, (tool_name, param_name)

        origin = get_origin(hints[param_name])
        has_none = origin in (Union, UnionType) and type(None) in get_args(hints[param_name])
        expected = (tool_name, param_name) in OPTIONAL_WITH_DEFAULT
        assert has_none is expected, (tool_name, param_name, hints[param_name])

    assert len(NON_OPTIONAL_BUT_DEFAULTED) == 19
    assert len(OPTIONAL_WITH_DEFAULT) == 68


def test_field_lists_cover_every_optional_param() -> None:
    """**双向闭环**：从真实签名现算的集合，必须与两张清单的并集**逐项相等**。

    这条是真正堵住缺口的一条（#198）。原来的 `test_field_lists_match_signatures`
    只做「清单 → 签名」一个方向，清单漏项它抓不到：它遍历的是**清单**，清单里没有的
    字段根本不会被它看到。

    两个方向都要断，因为漏判的方向不同：
      - **现算有 / 清单无** = 漏收。新加了一个 `Optional` 参数、或者把某个参数从
        `X = <默认>` 改成 `Optional[X] = None`，而清单没跟上 ⇒ 该字段的 null 行为
        **无人钉住**。`console.payload` 就是这一种（#198 修复对象）。
      - **清单有 / 现算无** = 过期。某参数被改成非 Optional、或者被删掉，清单还留着
        ⇒ 判据在验一个不存在的字段（`test_field_lists_match_signatures` 能抓这一种，
        这里再抓一次是兜底：万一有人把那条删了，闭环仍在）。

    口径说明：现算侧 `_declared_optional_pairs()` 与清单注释里写的口径逐条对齐
    （跳过私有参数 / 必须有默认值 / `Union[X, None]` 两种写法都认）。
    """
    computed = _declared_optional_pairs()
    # 两侧必须分开比，不能拿「两表并集」跟现算集合比：
    #   - 漏收（missing）要拿**并集**比 —— 落在任何一张表里都算已登记；
    #   - 过期（stale）只能拿 `OPTIONAL_WITH_DEFAULT` 比 —— `NON_OPTIONAL_BUT_DEFAULTED`
    #     的定义就是「注解**不含** None」，它天然不在现算集合里，用并集比会把 19 条全判成过期。
    listed_both = set(NON_OPTIONAL_BUT_DEFAULTED) | set(OPTIONAL_WITH_DEFAULT)
    listed_optional = set(OPTIONAL_WITH_DEFAULT)

    missing = sorted(computed - listed_both)
    stale = sorted(listed_optional - computed)

    assert not missing, (
        "这些参数「有默认值 且 注解含 None」，但两张清单里都没有 ⇒ 它们的 null 行为无人钉住：\n"
        + "\n".join(f"  {tool}.{param}" for tool, param in missing)
        + "\n请加进 `OPTIONAL_WITH_DEFAULT`（这是外部锚点，改动要显式留痕）。"
    )
    assert not stale, (
        "这些清单项在真实签名里已不成立（参数被删 / 改成非 Optional / 已无默认值）：\n"
        + "\n".join(f"  {tool}.{param}" for tool, param in stale)
        + "\n\n⚠️ 先排除「注册表没加载全」再动手：`_declared_optional_pairs()` 已在开头断过两组"
        "\n   代表项（`console` / `create_container`），走到这里说明两组都在。若你是在"
        "\n   子集环境下（只 load 一组、或绕开了 `_loaded_tools` fixture）看到这条，"
        "\n   **不要删清单项** —— 那份清单是对的，缺的是加载。"
        "\n   确认签名真的变了，才从 `OPTIONAL_WITH_DEFAULT` 里删掉。"
    )



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
        if not _rejected_by_schema(result):
            failures.append(f"{tool_name}.{param_name} 未被 schema 拒：{result}")

    assert not failures, "以下字段传 null 仍被放行：\n" + "\n".join(failures)


@pytest.mark.asyncio
async def test_field_null_accepted_for_all_optional_params() -> None:
    """63 个 `Optional` 字段传 `null` ⇒ 全部**不得**报 `invalid_tool_args`（防「修过头」）。

    这些是现网合法用法（`{"timezone": null}` 走默认时区等）。把 null 一律拒掉会同时
    打挂它们，且没有任何现有用例会红 —— 所以这条判据不能省。

    注意断言的是「**不是** `invalid_tool_args`」而不是「`success is True`」：参数合法
    不代表业务成功（例如 `list_tasks` 三个过滤条件都不给会报 missing_target），本判据
    只管**校验层**放不放行。
    """

    failures: list[str] = []
    for tool_name, param_name in OPTIONAL_WITH_DEFAULT:
        try:
            await _inject_null_schema_layer(tool_name, param_name)
        except AssertionError as exc:
            failures.append(str(exc))

    assert not failures, "以下 Optional 字段的 null 合法性问题：\n" + "\n".join(failures)


@pytest.mark.asyncio
async def test_console_payload_null_is_accepted() -> None:
    """`console.payload` 传 `null` ⇒ 必须放行（#198 补的判据覆盖缺口）。

    为什么这条单独写、不并进 63 组的行为判据：63 组是**逐字段同口径**跑的
    （`_inject_null_schema_layer` 对每个 `(tool, param)` 做同样三件事），
    `console.payload` 并进去之后**也会被它覆盖**。这条补的是**另一层**：

    - 63 组走的是 schema 层（`get_tool_parameters_schema` + `validate_tool_args`）；
    - 这条走**端到端**（`run_tool_call`），验的是「模型真的这样调用时能不能过」。

    两层都要，因为它们的失败方式不同：schema 层对、运行时把 null 当非法值处理，
    63 组不会红而这条会。

    只断言「**不是** `invalid_tool_args`」，**不断言 `success is True`** —— 参数合法
    ≠ 业务成功（与 63 组现有口径一致）。具体到 `console(command="help", payload=None)`，
    实测 `success=True`，但这条判据不该依赖那个事实：`help` 的输出格式若变，
    这条会变成在测 help 而不是在测 null 通道。
    """
    result = await funcCore.run_tool_call(
        json.dumps({"command": "help", "payload": None}),
        _ctx("console"),
    )

    assert result.get("error_code") != toolArgsValidation.INVALID_TOOL_ARGS, (
        f"`console(command='help', payload=None)` 被拒了 —— payload 是 Optional，"
        f"null 必须等同于未传：{result}"
    )


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
#:
#: #196 P2 再次重算（`6365f3f7…` → `7ab24aee…`）：**只有一处 description 变化**，
#: `console.command` 末尾加「任意命令加 --help / -h 可看该命令的参数清单。」
#: 理由：P2 让 `--help` / `-h` 在任意位置可用（draft D12），这是模型必须知道的能力，
#: 不写进工具说明等于「实现了但没人知道」。工具数与参数数不变（38 / 108），
#: required 不变 —— 已实测快照 diff 里只有 `[console.command] 参数说明变化` 一条。
#:
#: 角色模板 PATCH 下沉再次重算（`7ab24aee…` → `06ef7b7c…`）：`save_role_template`
#: 的 `type` / `soul` 改成 Optional，工具级 required 从 `["name","type","soul"]`
#: 变为 `["name"]`；同时 `type` / `soul` 的参数说明改写为「新建必填 / 更新时省略
#: 保持现值」。`save_agent.name` 的说明补充「同名多行 ⇒ ambiguous_target」。
#: 工具数与参数数不变（38 / 108）。
BASELINE_TEXT_DIGEST = "eec676b35fcb0e77648f2e5ede1136a8432b9aefc4b4c6fe6a5139e67ccc622e"


def _text_digest() -> tuple[str, int, int]:
    """(摘要, 工具数, 参数数)：只收 description 与 required，**不含** schema 形态。"""

    import hashlib

    snapshot: dict[str, Any] = {}
    # 注册面枚举：description/required 是工具签名的事实，与 D2 暴露面无关。
    for tool in funcCore.get_all_tools(include_container_tools=True):
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

    assert tools == 39, tools
    assert params == 111, params
    assert digest == BASELINE_TEXT_DIGEST, (
        "description 或 required 与基线不一致（摘要变化）。\n"
        "若确为有意变更，请先确认「模型看到的接口说明确实该变」，再更新本文件的 "
        "BASELINE_TEXT_DIGEST。"
    )


# --------------------------------------------------------------------------- #
# 65 组的「不走端到端」守卫（小马哥 23:08 第③件；#198 起 62 → 63，角色模板 PATCH 后 → 65）
#
# 判据要钉的是**调用链**（有没有间接走端到端），不是「关键字出现与否」。
# 65 组的通道原本是 `_inject_null` → `run_tool_call` 两跳，只盯第二跳会漏掉第一跳。
#
# 为什么用 AST 取函数体，而不是行号 / sed / awk / 关键字 grep：
#   - 行号：插一行就腐化（本单实测过 `source.index(f"async def {plan_name}(")` 直接崩）；
#   - 相邻函数名：改名就失效；
#   - 不带 `\b` 的子串匹配：`_inject_null_schema_layer`.count("_inject_null") == 1 ⇒ 误报红。
# 这三样都是**与结论无关的表示细节**，判据依赖它们就会在正确实现上假红。
# --------------------------------------------------------------------------- #


def _body_of_test_function(func_name: str) -> str:
    """取某个测试函数的**语句体**源码（剥掉 docstring 与 decorator）。

    `ast.unparse` 会丢弃全部注释（注释在 `ast.parse` 阶段就不进树），所以函数体里
    写「本判据不走 `_inject_null`」这类注释**不会**让下面的守卫假红 —— 只有真实调用
    才会被 `re.findall` 抓到。
    """
    path = __file__
    tree = _ast.parse(open(path, encoding="utf-8").read())
    node = next(
        n for n in _ast.walk(tree)
        if isinstance(n, (_ast.AsyncFunctionDef, _ast.FunctionDef)) and n.name == func_name
    )
    stmts = [
        stmt for stmt in node.body
        if not (
            isinstance(stmt, _ast.Expr)
            and isinstance(stmt.value, _ast.Constant)
            and isinstance(stmt.value.value, str)
        )
    ]
    return "\n".join(_ast.unparse(stmt) for stmt in stmts)


def test_optional_group_does_not_go_through_end_to_end() -> None:
    """63 组必须**直连 schema 层**，不得经 `_inject_null` / `run_tool_call` 两跳。

    结构上的保证在 `_inject_null_schema_layer`（独立函数），这条判据只是防止有人
    把 63 组改回共用 `_inject_null` —— 那会让「走端到端」这个前提悄悄复活：
    63 组自己的代码一行没动，结论却已经变成了端到端结果。

    `\\b` 词边界是必须的：`_inject_null_schema_layer` 里含 `_inject_null` 子串。
    """
    body = _body_of_test_function("test_field_null_accepted_for_all_optional_params")
    hits = sorted(set(re.findall(r"\b_inject_null\b|\brun_tool_call\b", body)))
    assert not hits, (
        f"63 组必须直连 schema 层，函数体里命中了：{hits} —— "
        "请改调 `_inject_null_schema_layer`（不要共用 `_inject_null`）"
    )

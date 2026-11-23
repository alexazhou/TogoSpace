"""RC-4 校验矩阵（#334）：入口就把四类非法输入拒掉，并让 usage 行与详情段同源。

四类注入（ISSUES v2.1 §4 RC-4 判据原文）：

① `--from payload` 的**未知键**（C-03①，第 1 跳）
② `min_pos=0` 的位置槽写成 `--选项`（C-01；`dept rm --dept-id 2` 曾**真删**）
③ **双定位同时传入**（`dept rm 2 --name X`，C-16 家族：底层 `if dept_id is not None` 静默丢掉 name）
④ 不可用选项（既有行为，作防回归的阴性对照）

外加两条结构性判据：

- 第 2 跳 `additionalProperties: false`（C-03②）：未知键在 schema 校验层就变成结构化错误，
  不再穿透到 `func(**args)` 抛 `TypeError`（错误体 `error_code: null`）；
- `[--选项 ...]` 的出现与否**只由 `display_options` 决定**（#323 验收追加）：全量扫 28 条命令，
  正反两半都要成立（反面故意留了变异入口，见报告 §变异）。

本文件只碰入口（解析 / 校验），不落库、不用替身代替被测函数。
"""

from __future__ import annotations

import itertools

import pytest

from service.funcToolService import consoleParsing as cp
from service.funcToolService import consoleTool as ct
from service.funcToolService import toolArgsValidation as tav
from service.funcToolService.consoleRegistry import get_table

_TABLE = get_table()

#: 全部带位置槽的命令（#323 报告 §3.2 的 16 条；这里**现场派生**，不写死名单）。
#: 用 `cmd.key`（主命令路径）去重 —— `by_path` 里还有别名条目（如 `dept remove`），
#: 别名与主名是同一条命令，不能当成两条来参数化，否则提示里的命令名对不上。
_POSITIONAL_KEYS = sorted({cmd.key for cmd in _TABLE.by_path.values() if cmd.positional_param_names})


def _cmd(key: str):
    cmd = _TABLE.by_path[key]
    return cmd


def _build(key: str, argv: list[str], payload: dict | None = None):
    """走真流水线的同步部分（与 `_dispatch` / 自中断谓词同一个实现）。"""
    return ct._build_call_args(_cmd(key), list(argv), payload)


def _error(key: str, argv: list[str], payload: dict | None = None) -> cp.ConsoleError:
    with pytest.raises(cp.ConsoleError) as caught:
        _build(key, argv, payload)
    return caught.value


# --------------------------------------------------------------------------- #
# ① C-03①：payload 未知键 / 位置参数键
# --------------------------------------------------------------------------- #


def test_payload_unknown_key_is_rejected_with_allowed_list() -> None:
    """未知键 ⇒ `unknown_payload_key`，错误体里带该命令接受的键清单（可自查）。"""
    exc = _error("dept create", ["--from", "payload"], {"name": "x", "responsibility": "y", "bogus_key": 1})
    assert exc.code == "unknown_payload_key", exc.message
    assert "bogus_key" in exc.message, exc.message
    accepted = exc.extra.get("allowed") or []
    assert "name" in accepted and "bogus_key" not in accepted, accepted


def test_payload_unknown_key_does_not_reach_python_typeerror() -> None:
    """回归钉：改前 `dept update 1 --from payload {"bogus_key":1}` 一路漏到 `func(**args)`
    抛 `TypeError`（错误体 `error_code: null`）。现在必须在入口就有稳定码。"""
    exc = _error("dept update", ["1", "--from", "payload"], {"bogus_key": 1})
    assert exc.code in {"unknown_payload_key", "no_update_action"}, exc.message
    assert exc.code != "invalid_payload_field", exc.message


def test_payload_positional_key_is_rejected() -> None:
    """位置参数键塞进 payload ⇒ `positional_payload_key`（定位只能写位置形态）。"""
    exc = _error("dept update", ["1", "--from", "payload"], {"dept_id": 2, "name": "x"})
    assert exc.code == "positional_payload_key", exc.message
    assert "dept_id" in exc.message, exc.message


@pytest.mark.parametrize(
    "key,argv,payload,expected",
    [
        ("dept update", ["1", "--from", "payload"], {"name": "改名"}, {"name": "改名"}),
        # kebab / 旧下划线 / 别名写进 payload 也要归一到工具真名（与 --选项 同源）
        ("room update", ["1", "--from", "payload"], {"clear-fields": ["max_rounds"]}, {"clear_fields": ["max_rounds"]}),
        ("task update", ["1", "--from", "payload"], {"title-keyword": "x"}, None),
    ],
)
def test_payload_legit_keys_still_accepted(key: str, argv: list[str], payload: dict, expected: dict | None) -> None:
    """对照（防过度拦截）：合法键**照旧**可用，且 kebab/别名形态被归一到真名。

    `task update` 没有 `title_keyword` 参数（那是 `task ls` 的），所以第三条期望是
    `unknown_payload_key` —— 这正好说明白名单是**按命令**算的，不是全局一张表。
    """
    if expected is None:
        exc = _error(key, argv, payload)
        assert exc.code == "unknown_payload_key", exc.message
        return
    args = _build(key, argv, payload)
    for name, value in expected.items():
        assert args.get(name) == value, (name, args)


# --------------------------------------------------------------------------- #
# ② C-01：位置槽的 `--选项` 写法统一拦截（`min_pos` 0 与 1 一视同仁）
# --------------------------------------------------------------------------- #


def _first_positional(cmd):
    return cmd.positional_args[0]


def test_c01_min_pos_zero_used_to_execute_delete() -> None:
    """C-01 现场：`dept rm --dept-id 2` 改前被放行（`min_pos=0` 绕过位置计数）并**真删**。

    现在必须在选项解析层拒掉，且给出等价位置写法 —— 这是本单最重要的一条回归钉。
    """
    exc = _error("dept rm", ["--dept-id", "2"])
    assert exc.code == "unknown_option", exc.message
    assert "dept rm 2" in exc.message, exc.message
    # 反向：房间 rm 同理（`min_pos=0` 的第二条现场）
    exc_room = _error("room rm", ["--room-id", "2"])
    assert exc_room.code == "unknown_option", exc_room.message
    assert "room rm 2" in exc_room.message, exc_room.message


@pytest.mark.parametrize("key", _POSITIONAL_KEYS)
def test_c01_every_positional_command_rejects_option_form(key: str) -> None:
    """全部带位置槽的命令：把第一个位置槽写成 `--kebab` ⇒ 一律 `unknown_option` + 改写提示。

    参数化名单**现场派生**（`positional_param_names` 非空），新增命令自动纳入。
    """
    cmd = _cmd(key)
    arg = _first_positional(cmd)
    kebab = cp.cc.to_kebab(arg.target.name)
    exc = _error(key, [f"--{kebab}", "7"])
    assert exc.code == "unknown_option", f"{key}: {exc.message}"
    assert f"{key} 7" in exc.message, f"{key} 没给等价位置写法：{exc.message}"


@pytest.mark.parametrize("key", _POSITIONAL_KEYS)
def test_c01_legacy_underscore_and_inline_forms_also_rejected(key: str) -> None:
    """兼容期旧名（`--dept_id`）与 `--x=v` 内联值两种写法**同样**被拒，提示一致。"""
    cmd = _cmd(key)
    name = _first_positional(cmd).target.name
    for argv in ([f"--{name}", "7"], [f"--{name}=7"]):
        exc = _error(key, argv)
        assert exc.code == "unknown_option", f"{key} {argv}: {exc.message}"
        assert f"{key} 7" in exc.message, f"{key} {argv} 提示里没带值：{exc.message}"


def test_c01_positional_already_given_still_reports_duplicate() -> None:
    """位置参数**已给出**时，`--dept-id` 仍走「同一参数重复提供」⇒ `ambiguous_input_source`。

    既有契约（不是本单新增）：这里不能改成 `unknown_option`，否则用户分不清「写错形态」
    与「重复给了」。
    """
    exc = _error("dept rm", ["2", "--dept-id", "2"])
    assert exc.code == "ambiguous_input_source", exc.message
    assert "dept_id" in exc.message, exc.message


def test_c01_does_not_over_block_legit_options() -> None:
    """对照（防过度拦截）：真正的选项照旧放行 —— `--name` / `--recursive` / `--dry-run`。"""
    args = _build("dept rm", ["--name", "某部门", "--dry-run"])
    assert args["name"] == "某部门" and args["dry_run"] is True, args
    args = _build("dept rm", ["3", "--recursive"])
    assert args["dept_id"] == 3 and args["recursive"] is True, args


# --------------------------------------------------------------------------- #
# ③ 双定位同时传入（locators 声明）
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "key,argv",
    [
        ("dept rm", ["2", "--name", "研发部"]),
        ("room rm", ["2", "--name", "体验部"]),
    ],
)
def test_two_locators_at_once_is_rejected(key: str, argv: list[str]) -> None:
    """两个互斥定位同时给 ⇒ `ambiguous_input_source`，不再静默「以 ID 为准」。

    底层实现是 `if xxx_id is not None:` 优先，改前 `--name` 被静默丢弃（C-16 家族）：
    调用方以为按名字删了，实际删的是 ID 指定的对象。
    """
    exc = _error(key, argv)
    assert exc.code == "ambiguous_input_source", exc.message
    assert "--name" in exc.message or "name" in exc.message, exc.message


def test_single_locator_at_a_time_still_works() -> None:
    """对照：只给一个定位照旧能走（`dept rm 2` / `dept rm --name X`）。"""
    assert _build("dept rm", ["2"])["dept_id"] == 2
    assert _build("dept rm", ["--name", "某部门"])["name"] == "某部门"


def test_locators_declaration_is_single_source() -> None:
    """声明面：只有「真的二选一」的命令才声明 locators，且每条都 ≥2 个且都在参数面内。"""
    declared = {cmd.key: cmd.locators for cmd in _TABLE.by_path.values() if cmd.locators}
    assert set(declared) == {"dept rm", "room rm"}, declared
    for key, group in declared.items():
        cmd = _cmd(key)
        assert len(group) >= 2, (key, group)
        assert set(group) <= set(cmd.param_names), (key, group)
        assert not (set(group) & set(cmd.excluded_param_names)), (key, group)


# --------------------------------------------------------------------------- #
# ④ 不可用选项（阴性对照，既有行为）
# --------------------------------------------------------------------------- #


def test_unusable_option_stays_unknown() -> None:
    """不可用选项 ⇒ `unknown_option`（既有行为；本单不得把它改坏）。"""
    exc = _error("task ls", ["--bogus", "1"])
    assert exc.code == "unknown_option", exc.message


# --------------------------------------------------------------------------- #
# C-03②：第 2 跳 additionalProperties: false
# --------------------------------------------------------------------------- #


def test_local_tool_schema_forbids_extra_properties() -> None:
    """无 `**kwargs` 的本地工具：schema 必须封口，且与下发给模型的那一份**逐键相等**。"""
    from service.funcToolService import core as func_core

    func_core.load_func_tools()  # 注册面按需装配（模块顶层不抢跑，见 core.load_func_tools）
    for name in ("create_agent", "get_time", "list_tasks"):
        func_tool = func_core.get_func_tool(name)
        assert func_tool is not None, name
        validation = tav.get_tool_parameters_schema(name, func_tool.callable)
        model_side = func_tool.to_openai_tool().function.parameters.model_dump()
        assert validation is not None, name
        assert validation.get("additionalProperties") is False, (name, validation.get("additionalProperties"))
        assert validation == model_side, name


def test_variadic_tool_schema_allows_extra_properties() -> None:
    """有 `**kwargs` 的工具**不能**封口（语义上就接受任意键）。"""

    def variadic(**kwargs):
        return {"success": True}

    schema = tav.get_tool_parameters_schema("variadic_probe", variadic)
    assert schema is not None
    assert schema.get("additionalProperties") is True, schema


def test_unknown_key_at_second_hop_is_structured_not_typeerror() -> None:
    """未知键在 schema 校验层就被拒：`error_code` 稳定（改前是 `null` + Python `TypeError` 原文）。"""
    result = tav.validate_tool_args("get_time", {"timezone": "UTC", "bogus": 1}, func=_func("get_time"))
    assert result is not None, "未知键仍被放行"
    assert result.get("error_code") == "invalid_tool_args", result
    assert "additionalProperties" in result.get("message", "") or "bogus" in result.get("message", ""), result


def _func(name: str):
    from service.funcToolService import core as func_core

    func_core.load_func_tools()
    tool = func_core.get_func_tool(name)
    assert tool is not None, name
    return tool.callable


@pytest.mark.asyncio
async def test_unknown_key_at_executor_entry_is_structured_not_typeerror() -> None:
    """真执行收口 `core.run_tool_call`：未知键 ⇒ 结构化 `invalid_tool_args`。

    这条钉的正是 C-03 现场的症状：改前未知键穿透 schema 直达 `func(**args)`，抛
    Python `TypeError`，错误体是 `{"success": false, "error_code": null, "message":
    "函数执行失败: …got an unexpected keyword argument…"}`。现在必须在执行前就拒。
    """
    from types import SimpleNamespace

    from service.funcToolService import core as func_core

    func_core.load_func_tools()
    ctx = SimpleNamespace(tool_name="get_time", agent_id=1)
    result = await func_core.run_tool_call({"timezone": "UTC", "bogus": 1}, context=ctx)  # type: ignore[arg-type]
    assert result.get("success") is False, result
    assert result.get("error_code") == "invalid_tool_args", result
    assert result.get("error_code") is not None, "错误码退化回 null（C-03 现场）"
    # 阳性对照：同一入口、去掉未知键就能真跑通（否则上面可能是「怎么都失败」的假判据）
    ok = await func_core.run_tool_call({"timezone": "UTC"}, context=ctx)  # type: ignore[arg-type]
    assert ok.get("success") is True, ok


# --------------------------------------------------------------------------- #
# usage 行与详情段同源（#323 验收追加；#334 按 PM 02:24 裁定改成**文本对文本**）
# --------------------------------------------------------------------------- #
#
# 判据口径（重要，别改回去）：两侧都从**渲染后的 help 文本**抽，不读 `display_options`。
# 为什么：`display_options` 正是 `_usage_line` 的实现输入，判据读它等于「判据与实现同源」
# —— 只能防「改漏一处」，防不住两处一起改回 `cmd.options`（那恰恰是本单要防的回归：
# 位置槽的 kebab 形态又回到选项面，`dept get` 会同时印 `<id>` 位置行与 `--dept-id` 选项行）。
# `display_options` 只写进根因说明（§报告），不进判据。

_USAGE_PLACEHOLDER = "[--选项 ...]"
_SECTION = "选项："
#: #323/#334 定案：只有位置槽、没有可展示选项的 8 条（文本判据下逐条点名）
_NO_OPTION_COMMANDS = {
    "dept get",
    "agent get",
    "room get",
    "agent dm",
    "agent wake",
    "role get",
    "role rm",
    "container note",
}


def _usage_line(detail: str) -> str:
    return detail.splitlines()[0]


def _has_section(detail: str) -> bool:
    """详情里**是否真的有「选项：」段**（标题行本身，不是被描述文字蹭到的子串）。"""
    return any(line.strip() == _SECTION for line in detail.splitlines())


def _text_table() -> list[tuple[str, bool, bool]]:
    return [
        (key, _USAGE_PLACEHOLDER in _usage_line(detail), _has_section(detail))
        for key, detail in ct.HELP_DETAIL.items()
    ]


def test_usage_placeholder_matches_detail_section_textonly() -> None:
    """**主判据**（全量逐条）：`usage 行有 [--选项` ⇔ `详情有「选项：」段`。

    两侧都是渲染后的文本，覆盖全部命令（28 条），失败时打印全量对照表。
    """
    table = _text_table()
    bad = [(key, left, right) for key, left, right in table if left != right]
    assert not bad, (
        "usage 占位与详情选项段不同源（左=usage 有无 [--选项 / 右=详情有无「选项：」段）：\n  "
        + "\n  ".join(f"{key}: usage={left} detail_section={right}" for key, left, right in bad)
        + "\n全量对照：\n  "
        + "\n  ".join(f"{key}: usage={left} detail_section={right}" for key, left, right in table)
    )


def test_no_option_commands_have_neither_placeholder_nor_section() -> None:
    """正向（改前 8 条的第一种失效）：这些命令详情没有选项段，usage 就不许宣称有选项。"""
    for key in sorted(_NO_OPTION_COMMANDS):
        detail = ct.HELP_DETAIL[key]
        assert not _has_section(detail), f"{key} 详情里出现了「选项：」段：\n{detail}"
        assert _USAGE_PLACEHOLDER not in _usage_line(detail), (
            f"{key} 的 usage 仍宣称有选项：{_usage_line(detail)!r}"
        )


def test_commands_with_option_section_still_print_placeholder() -> None:
    """反向（防过度删除）：详情**有**选项段的命令，usage 必须**仍然**印占位。

    样本数 N 现场统计并要求 > 0（`N == 0` 说明判据没武装），失败时点名具体命令。
    """
    with_section = [key for key, detail in ct.HELP_DETAIL.items() if _has_section(detail)]
    missing = [key for key in with_section if _USAGE_PLACEHOLDER not in _usage_line(ct.HELP_DETAIL[key])]
    assert len(with_section) > 0, "样本 N=0：没有任何命令有选项段，这条判据没武装"
    assert not missing, (
        f"这 {len(missing)} 条有选项段却没印占位（N={len(with_section)}，越界名单={missing}）："
        + " / ".join(f"{k}: {_usage_line(ct.HELP_DETAIL[k])!r}" for k in missing)
    )
    assert len(with_section) == 16, f"样本数变了（{len(with_section)}，预期 16）：{sorted(with_section)}"


def test_positional_param_kebab_never_appears_in_option_section() -> None:
    """C-08 的**文本回潮判据**：位置槽参数的 `--kebab` 形态不得出现在「选项：」段里。

    为什么需要这条：主判据（usage ⇔ 详情段）两侧都是渲染文本，**同源误修**——把
    `_usage_line` 与 `display_options` 一起改回读 `cmd.options`——会让两侧保持一致，
    主判据照样绿，但同一个参数会被印两遍（位置行 `<id>` + 选项行 `--dept-id`）。

    右侧取的是**声明**里的位置槽名（`Arg.positional`，单一事实源），不是 `display_options`，
    所以这条不会被「渲染实现被一起改坏」骗过：只要渲染把位置槽又当选项印出来就红。
    """
    offenders: list[str] = []
    for key, detail in ct.HELP_DETAIL.items():
        lines = detail.splitlines()
        if _SECTION not in lines:
            continue
        option_block = lines[lines.index(_SECTION) + 1 :]
        for name in sorted(_cmd(key).positional_param_names):
            kebab = "--" + name.replace("_", "-")
            hit = [line.strip() for line in option_block if line.strip().startswith(kebab + " ")]
            if hit:
                offenders.append(f"{key}: 位置槽 {name} 又在选项段出现 -> {hit[0][:50]}")
    assert not offenders, (
        "位置槽参数被当成选项行又印了一次（C-08 回潮）：\n  " + "\n  ".join(offenders)
    )


# --------------------------------------------------------------------------- #
# 声明一致性：locators 的三条校验（build 时 fail-fast）
# --------------------------------------------------------------------------- #


def _build_with(console_commands, fake_tool):
    from service.funcToolService import consoleRegistry as cr
    from service.funcToolService.funcToolType import FuncTool
    from service.funcToolService.toolSpec import ToolSpec

    tool = FuncTool(
        name=fake_tool.__name__,
        callable=fake_tool,
        visibility=ct.ToolVisibility.CONSOLE_ONLY,
        spec=ToolSpec(visibility=ct.ToolVisibility.CONSOLE_ONLY, console=tuple(console_commands)),
    )
    try:
        cr.build([tool], strict=False)
    except ValueError as exc:
        return exc
    return None


def _fake_tool(dept_id: int | None = None, name: str | None = None) -> dict:  # pragma: no cover
    return {}


def test_locators_unknown_param_rejected() -> None:
    from service.funcToolService.toolSpec import ConsoleCommand

    exc = _build_with((ConsoleCommand("fake", "rm", locators=("dept_id", "nope")),), _fake_tool)
    assert exc is not None and "locators 引用了不存在的工具参数" in str(exc), exc


def test_locators_single_element_rejected() -> None:
    """只有一个参数谈不上「二选一」：单一必填定位不要声明（声明了也必须炸）。"""
    from service.funcToolService.toolSpec import ConsoleCommand

    exc = _build_with((ConsoleCommand("fake", "rm", locators=("dept_id",)),), _fake_tool)
    assert exc is not None and "至少要两个" in str(exc), exc


def test_locators_conflicting_with_exclude_rejected() -> None:
    from service.funcToolService.toolSpec import ConsoleCommand

    exc = _build_with(
        (ConsoleCommand("fake", "rm", exclude=("name",), locators=("dept_id", "name")),), _fake_tool
    )
    assert exc is not None and "exclude" in str(exc), exc


def test_legit_locators_declaration_builds() -> None:
    """对照：合法的 locators 声明能建表（否则上面三条可能是「怎么都炸」的假判据）。"""
    from service.funcToolService.toolSpec import ConsoleCommand

    assert _build_with((ConsoleCommand("fake", "rm", locators=("dept_id", "name")),), _fake_tool) is None

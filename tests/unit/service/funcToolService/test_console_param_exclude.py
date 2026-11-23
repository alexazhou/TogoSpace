"""命令级参数裁剪（`ConsoleCommand(exclude=...)`，D1=A / #323）。

一工具多命令时，「工具 schema 的全部参数」不等于「这条命令上可达的参数」。本文件钉三件事：

1. **不可达参数在 help 与解析白名单里同时消失**（C-21 / C-16 / C-12 的共同判据）；
2. **声明一致性**：位置槽与已声明的 `Arg` 不许被 exclude 抹掉（否则用法行与解析会打架）；
3. **选项必填来源是 `required_option_names`**（= 声明必填 ∪ schema required），
   不是 `schema_required` —— 后者与运行时 `_check_required()` 不同源。
"""

from __future__ import annotations

import pytest

from service.funcToolService import consoleRegistry as cr
from service.funcToolService import consoleTool as ct
from service.funcToolService.consoleParsing import ConsoleError, _parse_options


def _table():
    return cr.get_table()


def _spec(key: str):
    return _table().by_path[key]


# --------------------------------------------------------------------------- #
# ① 渲染面：被裁掉的参数不出现在 help 里
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "key, hidden_option",
    [
        ("container note", "--record-id"),  # C-21：与必填 <container_id> 撞车
        ("dept get", "--name"),  # C-16：<id> 必填 ⇒ 底层恒走 ID 分支
        ("room get", "--name"),
        ("agent get", "--name"),
        ("dept ls", "--dept-id"),  # C-12：ls 传单查定位项会跳进单查分支
        ("dept ls", "--name"),
        ("room ls", "--room-id"),
        ("room ls", "--name"),
        ("agent ls", "--agent-id"),
        ("agent ls", "--name"),
    ],
)
def test_excluded_option_not_rendered(key: str, hidden_option: str) -> None:
    detail = ct.HELP_DETAIL[key]
    assert hidden_option not in detail, f"{key} 的 help 里仍渲染了被裁掉的 {hidden_option}：\n{detail}"


def test_container_note_has_no_option_section() -> None:
    """`record_id` 被裁掉后，`container note` 一个选项都没有 ⇒ 不出现「选项：」标题。"""
    detail = ct.HELP_DETAIL["container note"]
    assert "选项：" not in detail, detail
    assert "--record-id" not in detail, detail


def test_ls_commands_render_no_option_section() -> None:
    """`dept|room|agent ls` 的单查定位参数被裁掉后没有剩余选项 ⇒ 也不出现「选项：」。"""
    for key in ("dept ls", "room ls", "agent ls"):
        detail = ct.HELP_DETAIL[key]
        assert "选项：" not in detail, f"{key}：\n{detail}"
        assert "[--选项 ...]" not in detail.splitlines()[0], f"{key} 用法行仍宣称有选项：{detail}"


# --------------------------------------------------------------------------- #
# ② 解析面：被裁掉的参数进不了白名单（unknown_option，而不是走到业务层）
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "key, argv",
    [
        ("container note", ["--record-id", "999999"]),
        ("dept get", ["--name", "hq"]),
        ("room get", ["--name", "r"]),
        ("agent get", ["--name", "a"]),
        ("dept ls", ["--dept-id", "1"]),
        ("dept ls", ["--name", "hq"]),
        ("room ls", ["--room-id", "1"]),
        ("agent ls", ["--name", "a"]),
    ],
)
def test_excluded_option_unknown_at_parse(key: str, argv: list[str]) -> None:
    with pytest.raises(ConsoleError) as excinfo:
        _parse_options(_spec(key), argv)
    assert excinfo.value.code == "unknown_option", excinfo.value


def test_unrelated_options_still_accepted() -> None:
    """对照组：裁剪只针对点名的那几个，同命令的其它合法选项不许被误伤。"""
    _positional, options, _source = _parse_options(_spec("dept update"), ["--name", "新名字"])
    assert options, "dept update 的 --name 被误裁"
    _positional, options, _source = _parse_options(_spec("dept rm"), ["--recursive"])
    assert options["recursive"] is True, "dept rm 的 --recursive 被误裁"


# --------------------------------------------------------------------------- #
# ③ 声明一致性：exclude 不许抹掉位置槽 / 已声明的 Arg / 不存在的参数
# --------------------------------------------------------------------------- #


def _build_with(console_commands, fake_tool):
    """用单条命令 + 单条工具声明跑一次 build，返回异常或 None。"""
    from service.funcToolService.funcToolType import FuncTool
    from service.funcToolService.toolSpec import ToolSpec

    tool = FuncTool(
        name=fake_tool.__name__,
        callable=fake_tool,
        visibility=ct.ToolVisibility.CONSOLE_ONLY,   # build 读的是这里，不是 spec.visibility
        spec=ToolSpec(visibility=ct.ToolVisibility.CONSOLE_ONLY, console=tuple(console_commands)),
    )
    try:
        # strict=False 只跳过「自举完整性」（要求收集列表含 console 自身与 6 个容器工具），
        # 单命令声明的一致性校验照样跑。
        cr.build([tool], strict=False)
    except ValueError as exc:
        return exc
    return None


def _fake_tool(dept_id: int | None = None, name: str | None = None) -> dict:  # pragma: no cover
    """声明校验用的最小工具：两个参数都取自 schema。"""
    return {}


def test_exclude_positional_rejected() -> None:
    from service.funcToolService.toolSpec import Arg, ConsoleCommand, Target

    cmd = ConsoleCommand(
        "fake", "get",
        args=(Arg(Target("dept_id", type=int), positional=True, required=True),),
        exclude=("dept_id",),
    )
    exc = _build_with((cmd,), _fake_tool)
    assert exc is not None and "位置参数" in str(exc), exc


def test_exclude_unknown_param_rejected() -> None:
    from service.funcToolService.toolSpec import ConsoleCommand

    exc = _build_with((ConsoleCommand("fake", "ls", exclude=("nope",)),), _fake_tool)
    assert exc is not None and "exclude 引用了不存在的工具参数" in str(exc), exc


def test_exclude_declared_arg_rejected() -> None:
    from service.funcToolService.toolSpec import Arg, ConsoleCommand, Target

    cmd = ConsoleCommand(
        "fake", "ls",
        args=(Arg(Target("name"), alias="nm"),),
        exclude=("name",),
    )
    exc = _build_with((cmd,), _fake_tool)
    assert exc is not None and "已在 args 里声明" in str(exc), exc


# --------------------------------------------------------------------------- #
# ④ 选项必填来源：`required_option_names`（声明必填 ∪ schema required）
# --------------------------------------------------------------------------- #


def test_declared_required_option_is_marked_required() -> None:
    """造一个「声明必填 / schema 可选」的选项：渲染必须标（必填）。

    这是 `required_option_names` 与 `schema_required` 的**鉴别点**——现有 28 条命令里
    还没有 `Arg(positional=False, required=True)` 的声明，两个来源今天恰好等值；不造这个
    现场，判据就没有鉴别力（文档 §9 禁令的实质）。
    """
    from service.funcToolService import consoleTool as ct_mod
    from service.funcToolService.funcToolType import FuncTool
    from service.funcToolService.toolSpec import Arg, ConsoleCommand, Target, ToolSpec

    def _fake_required_option(x: str | None = None, y: str | None = None) -> dict:  # pragma: no cover
        """假工具：两个都有默认值（⇒ schema_required 为空），但 x 在命令行上必填。"""
        return {"x": x, "y": y}

    cmd = ConsoleCommand("fake", "ls", args=(Arg(Target("x"), required=True),))
    tool = FuncTool(
        name="_fake_required_option",
        callable=_fake_required_option,
        visibility=ct.ToolVisibility.CONSOLE_ONLY,
        spec=ToolSpec(visibility=ct.ToolVisibility.CONSOLE_ONLY, console=(cmd,)),
    )
    table = cr.build([tool], strict=False)
    spec = table.by_path["fake ls"]

    assert spec.schema_required == (), "前提不成立：x 有默认值，schema required 应为空"
    assert spec.required_option_names == ("x",), spec.required_option_names

    positionals, options = ct_mod._param_sections(spec)
    assert not positionals
    x_row = next(row for row in options if row.startswith("--x "))
    y_row = next(row for row in options if row.startswith("--y "))
    assert "（必填）" in x_row, x_row
    assert "（可选）" in y_row, y_row


def test_option_required_marker_matches_required_option_names() -> None:
    """逐个选项核：help 里的（必填）/（可选）必须等于 `cmd.required_option_names`。

    **不拿 `schema_required` 比** —— 渲染与比对同源时天然全等、没有鉴别力（文档 §9 禁令）。
    """
    problems: list[str] = []
    checked = 0
    for spec in _table().specs:
        detail = ct.HELP_DETAIL[spec.key]
        if "选项：" not in detail:
            continue
        rows: list[str] = []
        for line in detail.split("选项：", 1)[1].splitlines():
            if not rows and not line.strip():
                continue  # 标题行在 split 后留下一个空串
            if line.startswith("  "):
                rows.append(line)
            else:
                break
        required = set(spec.required_option_names)
        for row in rows:
            name = row.strip().split()[0].lstrip("-")
            if " " not in name and not name:
                continue
            real = spec.normalize_option(name)
            checked += 1
            want = "（必填）" if real in required else "（可选）"
            other = "（可选）" if real in required else "（必填）"
            if want not in row or other in row:
                problems.append(f"{spec.key}.{real}: required={real in required}，行={row!r}")
    assert checked >= 40, f"只核到 {checked} 个选项，覆盖面不对"
    assert not problems, "选项必填标记与 required_option_names 不一致：\n" + "\n".join(problems)

"""`console` 的**元数据**级护栏（#154 CC6 + D2 开关）。

与 `test_console_tool.py`（集成）的分工：那边靠 DB 验证行为，这边**不碰 DB**，
只验证「模型看到的接口形态」和「暴露面开关」——它们是最容易被无意改坏、
又最难在集成测试里定位的两项。
"""

from __future__ import annotations

import json

import pytest

from service.funcToolService import consoleCommands as cc
from service.funcToolService import core as func_tool_core
from service.funcToolService.consoleTool import HELP_DETAIL, HELP_SUMMARY, console
from service.funcToolService.funcToolType import get_function_metadata
from service.funcToolService import tools as legacy


# ---------------------------------------------------------------------------
# CC6：help 体积实测（判据要求「报实测数字」，不允许把 draft 的估算当结论）
# ---------------------------------------------------------------------------

def _token_count(text: str) -> int:
    """用 litellm 计 token —— 与生产 `compact.estimate_tokens` 同一实现。

    不自己写 `len/4`：那样量出来的数和线上真实消耗不是一把尺子，报告里的百分比
    就成了「估算的估算」。
    """
    import litellm

    return litellm.token_counter(
        model="gpt-4o", messages=[{"role": "user", "content": text}]
    )


def test_cc6_help_is_far_smaller_than_the_16_legacy_schemas():
    """help 精简表 + console schema 的 token 必须显著小于 16 个原工具 schema 之和。

    `draft_v_console.md` §8 写的是「净省 50% 以上」——那是**估算**。本用例把
    「显著更省」这个方向钉死（阈值取 50%，即 draft 的口径），同时报告实测值；
    实测远高于 50%，但**结论以实测为准，不以 draft 为准**。
    """
    legacy_schemas = [
        get_function_metadata(name, getattr(legacy, name)) for name in cc.LEGACY_CONVERGED_TOOLS
    ]
    console_schema = get_function_metadata("console", console)

    legacy_tokens = _token_count(json.dumps(legacy_schemas, ensure_ascii=False))
    console_tokens = _token_count(json.dumps([console_schema], ensure_ascii=False))
    help_tokens = _token_count(HELP_SUMMARY)

    # 最保守口径：把 help 也算成常驻成本（实际上它只在模型主动敲 help 时才产生）。
    saved = legacy_tokens - console_tokens - help_tokens
    ratio = saved / legacy_tokens

    print(
        f"\n[CC6 实测] 16 工具 schema={legacy_tokens} tokens；"
        f"console schema={console_tokens}；help={help_tokens}；"
        f"净省={saved} tokens（{ratio:.1%}，最保守口径含 help）"
    )

    assert len(cc.LEGACY_CONVERGED_TOOLS) == 16, "被收敛工具清单必须仍是 16 项（draft §1）"
    assert ratio > 0.5, f"净省必须超过 draft 声称的 50%，实测 {ratio:.1%}"


def test_cc6_help_detail_is_on_demand_only():
    """`help` 默认只返回精简表；全量详情表**不随默认 help 输出**（否则省的 token 又回来了）。"""
    assert len(HELP_SUMMARY) < len(json.dumps(HELP_DETAIL, ensure_ascii=False)) / 2
    for resource in ("dept", "agent", "room", "role"):
        assert resource in HELP_SUMMARY
    # 详情文本里才有的东西不应出现在精简表里。
    assert "PATCH 语义" not in HELP_SUMMARY


def test_cc6_console_schema_shape():
    """console 的 schema 必须只有 command(必填) + payload(可选)，不多不少。

    #195 起 `payload` 的 `type` 是 **`["object", "null"]`**：注解是 `Optional[dict]`，
    生成器改用 pydantic `TypeAdapter` 后 nullability 直接写进 schema（改造前是剥成
    `{"type": "object"}`，靠校验层的 null 豁免兜住）。断言从「单值字符串」改成
    「恰好这两个类型的列表」——仍钉住「是 object」，同时钉住「可 null」。
    """
    meta = get_function_metadata("console", console)
    assert meta["name"] == "console"
    assert meta["parameters"]["required"] == ["command"]
    assert list(meta["parameters"]["properties"]) == ["command", "payload"]
    assert meta["parameters"]["properties"]["payload"]["type"] == ["object", "null"]
    assert meta["description"].strip(), "description 不能为空（模型据此决定何时用）"


# ---------------------------------------------------------------------------
# D2：原 16 个工具的单点开关
# ---------------------------------------------------------------------------

def test_d2_keep_legacy_by_default(monkeypatch):
    """默认（无环境变量）必须**保留**原 16 个工具——D2 明确要求默认值 = 保留。"""
    monkeypatch.delenv(cc.ENV_KEEP_LEGACY_TOOLS, raising=False)
    assert func_tool_core.keep_legacy_admin_tools() is True
    assert func_tool_core._hidden_tool_names() == set()


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "off", " off "])
def test_d2_switch_off_hides_exactly_the_16(monkeypatch, value):
    """开关置假 ⇒ 恰好隐藏那 16 个，console 仍在（收敛的前提）。"""
    monkeypatch.setenv(cc.ENV_KEEP_LEGACY_TOOLS, value)
    hidden = func_tool_core._hidden_tool_names()
    assert hidden == set(cc.LEGACY_CONVERGED_TOOLS)
    assert "console" not in hidden


@pytest.mark.parametrize("value", ["1", "true", "", "yes", "garbage"])
def test_d2_switch_is_fail_safe(monkeypatch, value):
    """无法识别的值按**保留**处理：少暴露工具的后果比多暴露严重得多。"""
    monkeypatch.setenv(cc.ENV_KEEP_LEGACY_TOOLS, value)
    assert func_tool_core._hidden_tool_names() == set()


def test_d2_get_tools_respects_switch_for_exposure_only(monkeypatch):
    """开关只改 `get_tools()` 的**暴露面**（schema 列表）。

    这里**只**断言暴露面，因为这是本开关唯一被验证过的效果。
    执行路径由下面 `test_d2_hidden_mode_console_forwarding_is_unavailable` 单独守。

    ⚠️ 历史教训（#166）：本用例的前身 `..._but_execution_does_not` 在暴露面之外
    还断言了 `get_func_tool()` 非 None，并据此声称「停用原工具后 console 仍要能
    路由到底层函数」。但 `get_func_tool()` **不是 console 的执行路径**——#160 起
    console 抛 `ForwardToolCall`，由注册表第 2 跳执行。所以那条断言恒真、基线也绿，
    却守着一个**假的事实**（实测隐藏模式下 console 23 条命令全部返回「未知工具」）。
    这正是「拿代理量当结论」：按名字和 docstring 在守"能执行"，按断言只在守
    "字典里有这个 key"。现已拆开：暴露面归本用例，执行路径归下一条。
    """
    # ⚠️ 必须**显式**清掉环境变量再测默认态：不能假定进程环境是干净的。
    # 隐藏模式全量回归是用 `TOGOSPACE_KEEP_LEGACY_ADMIN_TOOLS=0` 起的，
    # 这里若只 setenv 不 delenv，第一步的「默认态」断言就会红（#166 踩过）。
    monkeypatch.delenv(cc.ENV_KEEP_LEGACY_TOOLS, raising=False)
    func_tool_core.load_func_tools()
    exposed_default = {t.function.name for t in func_tool_core.get_tools()}
    assert "console" in exposed_default
    assert set(cc.LEGACY_CONVERGED_TOOLS) <= exposed_default

    monkeypatch.setenv(cc.ENV_KEEP_LEGACY_TOOLS, "0")
    exposed_off = {t.function.name for t in func_tool_core.get_tools()}
    assert "console" in exposed_off, "console 本身必须始终暴露（收敛的前提）"
    assert not (set(cc.LEGACY_CONVERGED_TOOLS) & exposed_off), "开关置假后 16 个原工具不得再暴露"



# ---------------------------------------------------------------------------
# 命令表的静态约束（加命令时最容易漏的两项）
# ---------------------------------------------------------------------------

def test_every_command_declares_legacy_tool():
    """每条命令都必须填 `legacy_tool`（#160 起它是**转发目标**）。

    漏填的后果不是「少个字段」，而是 fail-closed 把命令变成永久不可用（转发目标为空
    → 第 2 跳在注册表里查不到 → 按「未知工具」拒绝）；更糟的是如果实现改成
    「没填就按原样执行」，那就是给所有人开了 Admin 后门。
    """
    for spec in cc.COMMAND_SPECS:
        assert spec.legacy_tool, f"命令 `{spec.key}` 未声明等价原工具"


def test_every_legacy_tool_has_a_category():
    """`legacy_tool` 必须能在 `CATEGORY_CONFIG` 里查到——否则那个工具不在任何
    `Category:*` 里，任何身份的 allow_specs 都展开不到它，命令永久不可用。"""
    from service.agentService.toolRegistry import CATEGORY_CONFIG

    for spec in cc.COMMAND_SPECS:
        assert spec.legacy_tool in CATEGORY_CONFIG, (
            f"命令 `{spec.key}` 声明的 {spec.legacy_tool} 不在 CATEGORY_CONFIG 里，权限无从校验"
        )


def test_converged_list_covers_draft_scope():
    """收敛清单必须与 draft §1 的 16 项完全一致（不多不少）。"""
    expected = {
        "get_dept_info", "save_dept", "delete_dept",
        "get_agent_info", "save_agent", "wake_up_agent", "start_chat",
        "get_room_info", "save_room", "delete_room",
        "list_role_templates", "get_role_template", "save_role_template",
        "delete_role_template", "get_time", "reload_team",
    }
    assert set(cc.LEGACY_CONVERGED_TOOLS) == expected


def test_console_registered_as_basic():
    """`console` 入口必须注册为 BASIC（否则没 Admin 的人连 `dept ls` 都没有）。"""
    from constants import ToolCategory
    from service.agentService.toolRegistry import CATEGORY_CONFIG

    assert CATEGORY_CONFIG["console"] == ToolCategory.BASIC

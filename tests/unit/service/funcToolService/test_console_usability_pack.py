"""console 易用性包（#222）的**单元**判据：A3 / A4 / A5 / B3 的解析与文案面。

分工（别在本文件里重复集成层的事）：

- 本文件：命令表字段 → 解析/转发参数、错误码与文案。不起库、不走第 2 跳。
- `tests/integration/test_funcToolService/test_console_task.py`：同几件事的**真入口**
  版本（console → 注册表第 2 跳 → tools → taskService → DB），A3 的
  `auto_filled_filters` 与 A6 的「任务真的建成」都在那边验。

为什么两层都要：单元层挡不住「注册漏了 / 第 2 跳查不到目标」，集成层挡不住
「转发参数里到底补了什么」——`auto_filled_filters` 是 post_process 造的，
只跑成功路径看不出补的到底是 `scope=self` 还是别的值。

## 变异自证（都实测过，见 #222 报告）

- 删掉 `_handle_list` 里的 `fallback_options` 兜底 ⇒ A3 三条红；
- 删掉 `_check_arity` 的 `empty_target_hint` 分支 ⇒ A4 两条红；
- 删掉 `_usage_line` 末尾的 `--from payload` 推荐 ⇒ A5 红；
- 删掉 `_HELP_OVERLAY["mcp call"]` 的 base path 说明 ⇒ B3 红。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from exception import ForwardToolCall
from service.funcToolService import consoleParsing as cp
from service.funcToolService import consoleTool as ct
from service.funcToolService.consoleTool import console

# --------------------------------------------------------------------------- #
# A3：`task ls --status TODO` 单独使用时自动补 `--scope self`，且**不许静默**
# --------------------------------------------------------------------------- #


def _run(command: str) -> dict:
    """跑**真 console 入口**（`console` 是 async def，内部无 await）。

    不在这里复刻 console() 的「tokenize → help → resolve → parse → dispatch」步骤：
    复刻一份就是第二个行为源，测的就不是生产路径了。转发类命令用
    `_forward_args`（它同样走真入口，只是把 `ForwardToolCall` 拦下来）。
    """
    return asyncio.run(console(command))


def _forward_args(command: str) -> tuple[str, dict]:
    """跑一次 console，把 `ForwardToolCall` 拦下来，返回 (转发目标, 转发参数)。

    直接读 `tool_args` 是唯一能看清「命令层到底给原工具补了什么」的位置：
    真跑集成也只看得到结果集，分不清「补了 scope=self」与「原工具自己降级」。
    """
    with pytest.raises(ForwardToolCall) as caught:
        asyncio.run(console(command))
    signal = caught.value
    return signal.tool_name, signal.tool_args, signal.post_process


class TestTaskLsForwardsRaw:
    """A3 的**命令层**半侧：P0 之后补参不再由 console 做，命令层必须原样转发。

    责任归位（设计 §7-P0-①「默认值与提示归位」）：`--scope self` 的兜底从
    `consoleTool._handle_list` 的 `fallback_options` 挪进了 `list_tasks` 自己——
    工具层在过滤项全缺时补 `scope="self"`，并把 `auto_filled_filters` 与说明写进
    自己的返回体。于是命令层的正确行为变成「一个字都不许补、也不许挂后处理」，
    否则同一份兜底会出现两个来源（命令层补了、工具层又看到 scope 不为空）。

    兜底本身的行为判据在工具层与本文件的集成对应件里：
    `tests/unit/service/funcToolService/test_task_tools.py`（补参 + 提示 + 不覆盖显式值）
    与 `tests/integration/test_funcToolService/test_console_task.py`（真入口 + DB）。
    本类只钉「命令层不越权」这一半，避免两层判据互相顶牛。
    """

    def test_status_only_forwards_raw(self) -> None:
        """`task ls --status TODO`：转发参数只有 status，无 scope，无后处理。"""
        tool_name, args, post_process = _forward_args("task ls --status TODO")

        assert tool_name == "list_tasks"
        assert args == {"status": ["TODO"]}, f"命令层擅自补了我没给的参数：{args}"
        assert post_process is None, "补参已归工具层，命令层不许再挂后处理（会出现两个来源）"

    def test_no_filter_at_all_forwards_empty(self) -> None:
        """`task ls` 裸敲：转发空参数，兜底由 `list_tasks` 自己决定。"""
        _name, args, post_process = _forward_args("task ls")

        assert args == {}, f"命令层擅自补参数：{args}"
        assert post_process is None

    def test_explicit_filters_pass_through_untouched(self) -> None:
        """显式给的过滤项逐字透传（`--assignee` 不能被命令层改写成 scope=self）。"""
        for command, expected in (
            ("task ls --assignee 42", {"assignee": 42}),
            ("task ls --scope team --status TODO", {"scope": "team", "status": ["TODO"]}),
            ("task ls --kw 登录", {"title_keyword": "登录"}),
            ("task ls --reviewer 7", {"reviewer": 7}),
        ):
            _name, args, post_process = _forward_args(command)
            assert args == expected, f"{command} 转发参数被改动：{args}"
            assert post_process is None, f"{command} 不该带任何后处理"

    def test_help_states_the_same_owner(self) -> None:
        """`help task ls` 必须说清「补参由 `list_tasks` 做、结果里有 `auto_filled_filters`」。

        help 是模型唯一能读到的口径：若它还说「上游会自动补」，模型就无从解释
        `auto_filled_filters` 是谁写的，也想不到可以用 `--scope team` 查全团队。
        """
        help_text = _run("help task ls")["text"]

        assert "全缺时由 list_tasks 自身补 --scope self" in help_text, help_text
        assert "auto_filled_filters" in help_text, help_text
        assert "--scope team" in help_text, help_text

    def test_non_filter_command_is_untouched(self) -> None:
        """对照组：`dept ls` 没有过滤项概念，行为必须逐字不变（不许被顺手加上 scope）。"""
        _name, args, post_process = _forward_args("dept ls")
        assert post_process is None
        assert "scope" not in args


# --------------------------------------------------------------------------- #
# A4：`mcp ls` 空参不再只说「需要 1~1 个位置参数」
# --------------------------------------------------------------------------- #


class TestMcpEmptyTargetHint:
    """A4：「缺 target」要报固定错误码 + **一行可复制的正确调用**。

    #291 变更（原判据对象是 spec 的 `empty_target_hint = MCP_KIND_HINT`）：
    - 码：`missing_target` → `mcp_missing_target`（子树有了自己的错误码前缀，B 类）；
    - 文案：不再列「三种 kind / 三种 protocol」的枚举（那是 spec 时代的派生说明），
      改成新语法的定位格式 + 一条固定占位符示例（产品 §4.1 第 3 条）；
    - 判据本身不删：缺 target **必须**给可照抄的出路，且示例来自**唯一常量**。
    """

    @pytest.mark.parametrize("command", ["mcp call", "mcp config add", "mcp config remove"])
    def test_empty_target_gives_copyable_example(self, command: str) -> None:
        from service.funcToolService import consoleMcp as mcp_console

        result = _run(command)

        assert result["success"] is False
        assert result["error_code"] == "mcp_missing_target", result
        # 示例是一行可复制的占位符形态，且来自唯一常量（不在这里拼第二份）
        assert result.get("example"), result
        assert "正确调用" in result["message"], result["message"]
        if command == "mcp call":
            assert result["example"] == mcp_console.EX_CALL, result
            assert "<server-name>.<tool-name>" in result["message"], result["message"]

    def test_example_constant_is_single_source(self) -> None:
        """原「hint 与 MCP_KIND_HINT 同源」的意图保留：错误体 / help 摘要共用同一常量。

        判据：错误体里的 example 与 `mcpConsole.EX_CALL` **逐字节相同**，且该常量
        出现在 `MCP_HELP_SUMMARY` 里 —— 两处文案漂移会让任一条变红。
        """
        from service.funcToolService import consoleMcp as mcp_console

        body = _run("mcp call")
        assert body["example"] == mcp_console.EX_CALL
        assert mcp_console.EX_CALL in mcp_console.MCP_HELP_SUMMARY

    def test_other_arity_errors_keep_old_code(self) -> None:
        """对照组：**给多了**参数仍报 `invalid_argument_count`（A4 只改「缺」这一支）。"""
        result = _run("openapi list a b")
        assert result["error_code"] == "invalid_argument_count", result

    def test_session_rejects_name_or_server(self) -> None:
        """`mcp session` 三条命令都不接受名称/server（产品 §3.1）。

        多给一个位置参数 ⇒ `mcp_invalid_args`，并说明作用域恒为当前 Agent ——
        「具名 session」是产品明确排除的语法，不能静默忽略这个参数。
        """
        for command in ("mcp session start foo", "mcp session status foo", "mcp session stop foo"):
            result = _run(command)
            assert result["success"] is False, result
            assert result["error_code"] == "mcp_invalid_args", (command, result)
            assert "当前 Agent" in result["message"], result["message"]


# --------------------------------------------------------------------------- #
# A5：复杂参数的用法行给 `--from payload` 推荐
# --------------------------------------------------------------------------- #


class TestUsageLineRecommendsPayload:
    """A5：有 array/object 参数的命令，用法行末尾补推荐；没有的不补。"""

    def test_complex_param_command_recommends_payload(self) -> None:
        help_text = _run("help room create")["text"]
        usage = help_text.splitlines()[0]
        assert "--from payload" in usage, f"用法行没给推荐：{usage!r}"
        assert "复杂参数建议优先" in usage, usage

    def test_simple_command_has_no_recommendation(self) -> None:
        """对照组：`dept ls` 没有复杂参数，不该出现这句（否则成了噪音、也失去指示作用）。"""
        usage = _run("help dept ls")["text"].splitlines()[0]
        assert "复杂参数建议优先" not in usage, usage

    def test_update_command_also_covered(self) -> None:
        """`task update` 的 `--clear_fields` 是数组 ⇒ 同样要推荐（不是只给 create 加）。"""
        usage = _run("help task update")["text"].splitlines()[0]
        assert "复杂参数建议优先" in usage, usage


# --------------------------------------------------------------------------- #
# B3：`mcp` 的「spec 无 servers ⇒ base path 丢失」必须在 help 里说清
# --------------------------------------------------------------------------- #


class TestMcpBasePathNote:
    """B3：丢 base path 这件事没有任何运行时报错，只能在 help 里前置说明。

    #291：说明的落点从 `_HELP_OVERLAY["mcp ls"/"mcp call"]` 换到 `mcpConsole` 的
    `MCP_HELP_DETAIL`（overlay 随 spec 删除），判据的三要素不变：
    提到 `servers`、提到 `base path`、给一条可执行的出路（补 servers / 用完整 target）。
    """

    @pytest.mark.parametrize("command", ["help openapi", "help openapi call", "help openapi list"])
    def test_help_mentions_missing_servers_limitation(self, command: str) -> None:
        help_text = _run(command)["text"]
        assert "servers" in help_text, help_text
        assert "base path" in help_text, help_text
        # 得给一条可执行的出路，不能只说「有坑」
        assert "补 servers" in help_text or "完整 target" in help_text, help_text


# --------------------------------------------------------------------------- #
# A6（单元半侧）：`task create` 的转发参数与成功提示
# --------------------------------------------------------------------------- #


class TestTaskCreateForwarding:
    """A6 的单元半侧：转发目标、参数映射、以及「提示归工具层」这条边界。

    真跑建成任务在集成层（`test_console_task.py`），这里只钉「命令层交出去的是
    什么」。P0 之后 `hint` 由 `create_task` 自己的返回体携带（默认值/提示归位），
    命令层不再挂 `success_hint` 后处理 —— 两处都写会拼出两句重复提醒。
    """

    def test_forwards_to_create_task_with_options(self) -> None:
        tool_name, args, post_process = _forward_args(
            "task create --title 修一下 --assignee_id 42 --priority HIGH"
        )
        assert tool_name == "create_task"
        assert args["title"] == "修一下"
        assert args["assignee_id"] == 42
        assert args["priority"] == "HIGH"
        assert "hint" not in args, "提示不是入参：由 create_task 的返回体自带"
        assert post_process is None, "提示归工具层，命令层不许再挂后处理"

    def test_hint_lives_in_tool_docstring_contract(self) -> None:
        """命令层不再追加提示后，模型侧的唯一提示来源是工具说明与返回体。

        这里钉「用户能读到的那句话仍在 help 里」——help 由 `create_task` 的 docstring
        派生，docstring 掉了这句话，模型就再也不知道建完要另发 `send_chat_msg`。
        """
        help_text = _run("help task create")["text"]

        assert "不会唤醒执行人" in help_text, help_text
        assert "send_chat_msg" in help_text, help_text
        assert "hint" in help_text, help_text

    def test_other_create_commands_have_no_hint(self) -> None:
        """对照组：`dept create` 不带唤醒提示（提示是 task create 独有的事实）。"""
        # `manager_name` / `parent_name` 都是 `create_dept` 的签名级必填（P0 起必填项
        # 进签名），少给会在命令层先报 missing_required_option，根本到不了转发。
        _name, args, post_process = _forward_args(
            "dept create --name x --responsibility y --manager-name alice --parent-name 总部"
        )
        assert post_process is None
        # P1 删表后命令表不再有 `success_hint` 字段（那句提醒归 `create_task` 自己的
        # 返回体），对照组改成查：同一句提示不会从别的 create 命令漏出来。
        dept_help = _run("help dept create")["text"]
        assert "不会唤醒执行人" not in dept_help, dept_help


# --------------------------------------------------------------------------- #
# #233 记档 D1：`_handle_list` 两条路径**各转发恰一次**
# --------------------------------------------------------------------------- #

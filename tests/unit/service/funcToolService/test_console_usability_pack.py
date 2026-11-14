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
from service.funcToolService import consoleCommands as cc
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


class TestTaskLsAutoScope:
    """A3：过滤项全缺时补 `scope=self` 并显式提示。"""

    def test_status_only_auto_fills_scope_self(self) -> None:
        """`task ls --status TODO` 转发参数里必须出现 `scope=self`（且 status 原样保留）。"""
        tool_name, args, post_process = _forward_args("task ls --status TODO")

        assert tool_name == "list_tasks"
        assert args.get("scope") == "self", f"未自动补 scope=self：{args}"
        assert args.get("status") == ["TODO"], f"status 被改坏了：{args.get('status')!r}"
        assert post_process is not None, "补了参数却没有后处理 ⇒ auto_filled_filters 不会出现（属静默补参）"

    def test_auto_fill_is_announced_in_result(self) -> None:
        """后处理必须在返回体里署名：`auto_filled_filters` + message 说明。"""
        _name, args, post_process = _forward_args("task ls --status TODO")
        assert post_process is not None

        result = post_process({"success": True, "tasks": [], "count": 0, "total": 0})

        assert result["auto_filled_filters"] == {"scope": "self"}, result
        assert "自动补" in result["message"] and "--scope self" in result["message"], result
        assert result["tasks"] == [] and result["success"] is True, "不许改动原结果集与成败"
        # 原返回体没有 message 时也要能生成一句，不能把 None 拼进字符串
        assert post_process({"success": True, "tasks": []})["message"]

    def test_note_states_scope_fields_and_keeps_other_filters(self) -> None:
        """#238 判据 1：给了 `--status` 时 note 不得说「未给过滤条件」。

        note 必须与真实判据一致：补 `--scope self` 只因**过滤项
        （--assignee / --reviewer / --scope / --kw）全缺**，与 `--status` 无关；
        且要点明「其它过滤条件（如 --status）照旧生效」，不能让用户以为
        自己给的过滤项没生效。
        """
        _name, args, post_process = _forward_args("task ls --status TODO")
        assert post_process is not None

        result = post_process({"success": True, "tasks": [], "count": 0, "total": 0})
        message = result["message"]

        assert "未给过滤条件" not in message, message
        assert "--assignee / --reviewer / --scope / --kw" in message, message
        assert "全缺" in message, message
        assert "--scope self" in message, message
        assert "--status" in message and "照旧生效" in message, message
        assert "只列与我相关的任务" in message, message

    def test_no_filter_at_all_still_announced(self) -> None:
        """#238 判据 2：全缺时照旧补 `scope=self` 且显式提示（#222「不许静默」不退化）。"""
        _name, args, post_process = _forward_args("task ls")
        assert args.get("scope") == "self", args
        assert post_process is not None

        result = post_process({"success": True, "tasks": []})

        assert result["auto_filled_filters"] == {"scope": "self"}, result
        assert "已自动补" in result["message"] and "--scope self" in result["message"], result

    def test_explicit_scope_gets_no_note(self) -> None:
        """#238 判据 3：显式给 `--scope team` ⇒ 不补参，无 `auto_filled_filters`、无该文案。"""
        _name, args, post_process = _forward_args("task ls --scope team")
        assert post_process is None, "显式给过滤项不许挂补参后处理"
        assert "auto_filled" not in str(args), args

    def test_help_task_ls_states_same_condition(self) -> None:
        """#238 判据 4：`help task ls` 与 note 同口径（全缺才补 + 显式 --scope team 可查全团队）。"""
        result = _run("help task ls")
        help_text = result["help"]

        assert "过滤项 --assignee / --reviewer / --scope / --kw 全缺时自动补 --scope self" in help_text, help_text
        assert "要查全团队请显式给 --scope team" in help_text, help_text

    def test_explicit_filter_is_not_overridden(self) -> None:
        """显式给过滤项时**不许**再补（否则 `--assignee X` 会被 scope=self 收窄）。"""
        for command in (
            f"task ls --assignee 42",
            "task ls --scope team --status TODO",
            "task ls --kw 登录",
            "task ls --reviewer 7",
        ):
            _name, args, post_process = _forward_args(command)
            assert "scope" not in args or args["scope"] == "team", f"{command} 被补了 scope：{args}"
            assert post_process is None, f"{command} 不该带自动补参后处理"

    def test_non_filter_command_is_untouched(self) -> None:
        """对照组：`dept ls` 没有过滤项概念，行为必须逐字不变（不许被顺手加上 scope）。"""
        _name, args, post_process = _forward_args("dept ls")
        assert post_process is None
        assert "scope" not in args


# --------------------------------------------------------------------------- #
# A4：`mcp ls` 空参不再只说「需要 1~1 个位置参数」
# --------------------------------------------------------------------------- #


class TestMcpEmptyTargetHint:
    """A4：缺 target 时报 `missing_target` 并给出 kind 说明与示例。"""

    @pytest.mark.parametrize("command", ["mcp ls", "mcp call"])
    def test_empty_target_lists_kinds_and_examples(self, command: str) -> None:
        result = _run(command)

        assert result["success"] is False
        assert result["error_code"] == "missing_target", result
        usage = "\n".join(result["usage"])
        # 四种 kind 都得点到名，且各带一句 target 形态
        for kind in ("openapi", "mcp_stdio", "graphql"):
            assert f"--kind {kind}" in usage, f"usage 里缺 {kind}：{usage}"
        # 至少两个可抄的示例（一个是文件、一个是 URL/命令）
        assert "示例" in usage and "mcp ls ./openapi.json" in usage, usage
        assert "http" in usage.lower(), usage

    def test_hint_block_is_shared_not_copied(self) -> None:
        """两条 mcp 命令共用同一份文案（各抄一份必然漂移）。"""
        ls = cc.COMMAND_INDEX["mcp ls"]
        call = cc.COMMAND_INDEX["mcp call"]
        assert ls.empty_target_hint == call.empty_target_hint == cc.MCP_KIND_HINT

    def test_other_arity_errors_keep_old_code(self) -> None:
        """对照组：**给多了**参数仍报 `invalid_argument_count`（A4 只改「缺」这一支）。"""
        result = _run("mcp ls a b")
        assert result["error_code"] == "invalid_argument_count", result


# --------------------------------------------------------------------------- #
# A5：复杂参数的用法行给 `--from payload` 推荐
# --------------------------------------------------------------------------- #


class TestUsageLineRecommendsPayload:
    """A5：有 array/object 参数的命令，用法行末尾补推荐；没有的不补。"""

    def test_complex_param_command_recommends_payload(self) -> None:
        help_text = _run("help room create")["help"]
        usage = help_text.splitlines()[0]
        assert "--from payload" in usage, f"用法行没给推荐：{usage!r}"
        assert "复杂参数建议优先" in usage, usage

    def test_simple_command_has_no_recommendation(self) -> None:
        """对照组：`dept ls` 没有复杂参数，不该出现这句（否则成了噪音、也失去指示作用）。"""
        usage = _run("help dept ls")["help"].splitlines()[0]
        assert "复杂参数建议优先" not in usage, usage

    def test_update_command_also_covered(self) -> None:
        """`task update` 的 `--clear_fields` 是数组 ⇒ 同样要推荐（不是只给 create 加）。"""
        usage = _run("help task update")["help"].splitlines()[0]
        assert "复杂参数建议优先" in usage, usage


# --------------------------------------------------------------------------- #
# B3：`mcp` 的「spec 无 servers ⇒ base path 丢失」必须在 help 里说清
# --------------------------------------------------------------------------- #


class TestMcpBasePathNote:
    """B3：丢 base path 这件事没有任何运行时报错，只能在 help 里前置说明。"""

    @pytest.mark.parametrize("command", ["help mcp ls", "help mcp call"])
    def test_help_mentions_missing_servers_limitation(self, command: str) -> None:
        help_text = _run(command)["help"]
        assert "servers" in help_text, help_text
        assert "base path" in help_text, help_text
        # 得给一条可执行的出路，不能只说「有坑」
        assert "完整" in help_text or "补 servers" in help_text, help_text


# --------------------------------------------------------------------------- #
# A6（单元半侧）：`task create` 的转发参数与成功提示
# --------------------------------------------------------------------------- #


class TestTaskCreateForwarding:
    """A6 的单元半侧：转发目标、参数映射、`success_hint` 追加。

    真跑建成任务在集成层（`test_console_task.py`），这里只钉「命令层交出去的
    到底是什么」——两层各自失效面不同。
    """

    def test_forwards_to_create_task_with_options(self) -> None:
        tool_name, args, post_process = _forward_args(
            "task create --title 修一下 --assignee_id 42 --priority HIGH"
        )
        assert tool_name == "create_task"
        assert args["title"] == "修一下"
        assert args["assignee_id"] == 42
        assert args["priority"] == "HIGH"
        assert post_process is not None, "建单没有后处理 ⇒ 唤醒提示不会出现在返回体"

    def test_hint_is_appended_to_success_message(self) -> None:
        _name, _args, post_process = _forward_args(
            "task create --title x --assignee_id 42"
        )
        assert post_process is not None

        result = post_process({"success": True, "task_id": 77, "message": "任务已创建"})

        assert result["task_id"] == 77, "不许改动原返回体的关键字段"
        assert "不会自动唤醒" in result["hint"], result
        assert result["message"].startswith("任务已创建"), result["message"]
        assert "send_chat_msg" in result["message"], result["message"]

    def test_hint_not_injected_into_failures(self) -> None:
        """建单失败时不该附「记得通知对方」——会误导成「建成功了」。"""
        _name, _args, post_process = _forward_args("task create --title x --assignee_id 42")
        assert post_process is not None

        result = post_process({"success": False, "message": "无权将任务分配给他"})

        assert "hint" not in result
        assert result["message"] == "无权将任务分配给他"

    def test_other_create_commands_have_no_hint(self) -> None:
        """对照组：`dept create` 不带唤醒提示（提示是 task create 的命令级事实）。"""
        _name, args, post_process = _forward_args("dept create --name x --responsibility y")
        assert post_process is None
        assert cc.COMMAND_INDEX["dept create"].success_hint == ""


# --------------------------------------------------------------------------- #
# #233 记档 D1：`_handle_list` 两条路径**各转发恰一次**
# --------------------------------------------------------------------------- #


class TestHandleListForwardsExactlyOnce:
    """`_handle_list` 的转发必须结构化：有 fallback ⇒ 恰 1 次带 `post_process`；
    无 fallback ⇒ 恰 1 次不带。

    合并前的形态是 `if auto_filled:` 块尾**裸调用** `_forward(parsed, **args)`、
    没有显式 `else:`——不双转发完全靠「`_forward` 恒 raise」这个未受保护的
    前提（#222 记档 D1）。本判据把 `_forward` 换成**返回而不抛**的记录 stub，
    走真解析链直调 `_handle_list`，只认**精确计数 == 1**：

    - 「双转发」⇒ 计数 2 ⇒ 红（裸调用在「`_forward` 会返回」假设下的真实行为）；
    - 「零转发」⇒ 计数 0 ⇒ 红；
    - 「至少一次」类断言咬不住双转发，**禁止使用**——本类三条全部是精确计数。

    解析走真链（tokenizer → _parse_options → ParsedCommand，同
    `test_console_locator_hints` 的做法），不 mock ParsedCommand，避免与生产解析分叉。
    """

    @staticmethod
    def _parsed(command: str) -> cp.ParsedCommand:
        tokens = cp._tokenize(command)
        spec = cc.COMMAND_INDEX[" ".join(tokens[:2])]
        positional, options, input_source = cp._parse_options(spec, tokens[2:])
        return cp.ParsedCommand(
            spec,
            positional=positional,
            options=options,
            input_source=input_source,
            raw_tokens=tokens[2:],
        )

    @staticmethod
    def _record_and_run(
        monkeypatch: pytest.MonkeyPatch, command: str
    ) -> list[tuple[str, Any, dict[str, Any]]]:
        calls: list[tuple[str, Any, dict[str, Any]]] = []

        def _stub(parsed: cp.ParsedCommand, /, *, post_process: Any = None, **args: Any) -> None:
            """返回而不抛：制造「`_forward` 会返回」的假设态，让计数说话。"""
            calls.append((parsed.spec.legacy_tool, post_process, dict(args)))

        monkeypatch.setattr(ct, "_forward", _stub)
        ct._handle_list(TestHandleListForwardsExactlyOnce._parsed(command))
        return calls

    def test_auto_fallback_forwards_once_with_post_process(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`task ls`（过滤项全缺 ⇒ 自动补 scope=self）：恰 1 次，带 post_process。"""
        calls = self._record_and_run(monkeypatch, "task ls")
        assert len(calls) == 1, calls
        target, post_process, args = calls[0]
        assert target == "list_tasks", target
        assert post_process is not None, "自动补参必须带 post_process（auto_filled_filters 的唯一来源）"
        assert args.get("scope") == "self", args

    def test_explicit_filter_forwards_once_without_post_process(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`task ls --scope team`（有 fallback 表但过滤项已给）：恰 1 次，不带 post_process。

        注意不能拿 `--status TODO` 当「已给过滤」：`status` 不在
        `filter_options`（assignee/reviewer/scope/title_keyword）里，A3 的设计
        本来就会在只有 --status 时自动补 scope=self（见 TestTaskLsAutoScope）。
        """
        calls = self._record_and_run(monkeypatch, "task ls --scope team")
        assert len(calls) == 1, calls
        target, post_process, args = calls[0]
        assert target == "list_tasks", target
        assert post_process is None, "未补参不许挂后处理（否则 auto_filled_filters 会凭空出现）"
        assert args.get("scope") == "team", args

    def test_no_fallback_spec_forwards_once_without_post_process(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`dept ls`（命令表根本没有 fallback_options）：恰 1 次，不带 post_process。"""
        calls = self._record_and_run(monkeypatch, "dept ls")
        assert len(calls) == 1, calls
        target, post_process, args = calls[0]
        assert target == "get_dept_info", target
        assert post_process is None, "无补参路径不该有后处理"
        assert "scope" not in args, args

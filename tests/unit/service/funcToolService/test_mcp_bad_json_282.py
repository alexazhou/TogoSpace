"""#282 `mcp2cli_bad_json` 误报修复（方案 A）：非 JSON 输出按进程退出码判成败。

**背景**（Operator 2026-11-16 02:19 实测 + 选型）：`--extra` 透传 `--session-start` /
`--session-stop` / `--session-list` 时，mcp2cli 的 CLI 自己 print 控制台文本
（`Session 'ctx7' started (PID …)` / `No active sessions.`），不经上游 `output_result`
包装 ⇒ 旧代码把这些「操作成功」判成 `mcp2cli_bad_json`（success=False），使用者会重试。

**修法**：JSON 解析失败分支改为「能走到这里 = `_main_impl` 正常返回 = 退出码 0 ⇒ 成功」，
原文进 `result`、`result_type="string"`；退出码非 0 的失败由 `except SystemExit` 分支承担
（`mcp2cli_exit`、success=False）。**不按开关名白名单**（方案 B 已否）。

判据三组（双向）：
1. 正例：文本 + 正常返回 ⇒ success=True / result=原文 / result_type="string"；
2. 正例边界：超长文本 ⇒ truncated=True、不下发 result（与 JSON 成功同一条截断规则）；
3. **反例**：文本 + `sys.exit(1)` ⇒ 必须仍 `success=False`（`mcp2cli_exit`）。
"""

from __future__ import annotations

import sys
import types

import pytest

from service.funcToolService import mcpTool

_ARGV = ["--mcp", "https://example.invalid/mcp", "--json", "--list"]


def _install_stub(monkeypatch, *, text: str, exit_code: int | None = None) -> None:
    """stub mcp2cli 的 `_main_impl`：打印文本，可选再以非 0 退出。

    只替「外部包的执行分派」这一跳；`_run_argv_sync` 的捕获、JSON 判定、截断、退出码
    处理都是真的。
    """

    class _Stub(types.ModuleType):
        @staticmethod
        def _main_impl(argv: list[str]) -> None:
            print(text, end="")
            if exit_code is not None:
                sys.exit(exit_code)

    monkeypatch.setitem(sys.modules, "mcp2cli", _Stub("mcp2cli"))


def test_text_output_with_zero_exit_is_success(monkeypatch):
    """正例（不绑定开关名）：任何文本输出 + 正常返回 ⇒ 成功，原文进 result。

    这条**不依赖** `--session-*`：传入的 argv 与 session 无关，证明修的是「文本型输出」
    这一整类，而不是对某个开关打补丁（方案 B 被否的理由）。
    """
    text = "Session 'context7' started (PID 33097)\n"
    _install_stub(monkeypatch, text=text)
    result = mcpTool._run_argv_sync(list(_ARGV))
    assert result["success"] is True, result
    assert result["result"] == text
    assert result["result_type"] == "string"
    assert result["truncated"] is False
    assert "error_code" not in result, "成功返回体不该带 error_code"


def test_text_output_over_limit_is_truncated_success(monkeypatch):
    """正例边界：超长文本仍算成功，但按既有规则**不下发 result**、只给 raw_excerpt。"""
    text = "x" * (mcpTool.MAX_OUTPUT_CHARS + 50)
    _install_stub(monkeypatch, text=text)
    result = mcpTool._run_argv_sync(list(_ARGV))
    assert result["success"] is True, result
    assert result["truncated"] is True
    assert "result" not in result, "超限时不得把完整文本塞进 result（既有截断契约）"
    assert result["raw_excerpt"].startswith("x" * 100)
    assert result["original_chars"] >= len(text)


def test_text_output_with_nonzero_exit_still_fails(monkeypatch):
    """**反例**：文本 + `sys.exit(1)` ⇒ 必须仍是失败（`mcp2cli_exit`），不许被当成成功。

    这是「exit != 0 ⇒ success:false」的落点判据：mcp2cli 的失败路径是 `sys.exit(1)`，
    由 `except SystemExit` 接走；本用例保证它**不会**因为「有输出」而走到成功分支。
    """
    _install_stub(monkeypatch, text="fatal: something went wrong\n", exit_code=1)
    result = mcpTool._run_argv_sync(list(_ARGV))
    assert result["success"] is False, result
    assert result["error_code"] == "mcp2cli_exit"
    assert result["exit_code"] == 1


def test_text_output_with_zero_exit_zero_code_explicit(monkeypatch):
    """反例对照：显式 `sys.exit(0)` 也应算成功（退出码 0 与正常返回同义）。"""
    _install_stub(monkeypatch, text="done\n", exit_code=0)
    result = mcpTool._run_argv_sync(list(_ARGV))
    assert result["success"] is True, result
    assert result["result"] == "done\n"


def test_empty_output_still_reports_empty(monkeypatch):
    """既有口径不变：完全没有输出 ⇒ `mcp2cli_empty_output`（不被文本分支吞掉）。"""
    _install_stub(monkeypatch, text="")
    result = mcpTool._run_argv_sync(list(_ARGV))
    assert result["success"] is False
    assert result["error_code"] == "mcp2cli_empty_output"


@pytest.mark.asyncio
async def test_call_mcp_keeps_text_result_through_masking(monkeypatch):
    """经 `call_mcp` 全链：文本结果的 `result_type` / `success` 不被打码与 argv 附加破坏。"""
    text = "Session 'context7' stopped.\n"
    _install_stub(monkeypatch, text=text)
    result = await mcpTool.call_mcp(
        kind="mcp", target="https://example.invalid/mcp", action="list"
    )
    assert result["success"] is True, result
    assert result["result"] == text
    assert result["result_type"] == "string"
    assert result["argv"][0] == "--mcp"

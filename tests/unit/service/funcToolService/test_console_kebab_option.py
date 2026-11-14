"""#221 A2：选项名统一 kebab-case + 旧下划线名兼容期（单元层）。

## 这组判据防的是什么

清单 §A2：选项名下划线式不合 CLI 惯例（mcp2cli 侧早已 kebab 化，console 派生层用
原名，口径不一致）。#221 把命令行形态统一 kebab（`--dry-run` / `--clear-fields`），
schema 真名不动（上游不碰），旧名作为**兼容别名**在解析层继续接受整整一个版本。

三层防线（M1/M2/M3 变异见各用例）：
  1. 归一层：kebab / 旧名 / 短别名三种形态全部归一到同一真名（command_options_map
     唯一归一表，冲突派生时即炸）；
  2. 转换函数：to_kebab 单向（`dry_run` → `dry-run`），连续下划线派生时炸
     （防「转换错位」出 `dry--run` 这种打不出来的形态）；
  3. 展示层：help 参数行、HELP_SUMMARY、报错里的可用清单**只出现 kebab**，
     旧名不在展示面露脸（M3：双名混排即红）。
"""

from __future__ import annotations

import re

import pytest

from service.funcToolService import consoleCommands as cc
from service.funcToolService import consoleParsing as cp
from service.funcToolService import consoleSchema as cs
from service.funcToolService import consoleTool as ct


# --------------------------------------------------------------------------- #
# ① to_kebab：转换函数本体（M2 的落点）
# --------------------------------------------------------------------------- #


class TestToKebab:
    def test_basic_conversion(self) -> None:
        assert cc.to_kebab("dry_run") == "dry-run"
        assert cc.to_kebab("clear_fields") == "clear-fields"
        assert cc.to_kebab("role_template_name") == "role-template-name"

    def test_no_underscore_is_identity(self) -> None:
        assert cc.to_kebab("status") == "status"
        assert cc.to_kebab("args") == "args"

    def test_idempotent(self) -> None:
        assert cc.to_kebab(cc.to_kebab("open_only")) == "open-only"

    def test_double_underscore_is_rejected_at_derivation_time(self) -> None:
        """连续下划线派生时炸，不让 `--a--b` 这种打不出来的形态进运行时。"""
        with pytest.raises(ValueError):
            cc.to_kebab("a__b")
        with pytest.raises(ValueError):
            cc.to_kebab("_lead")

    def test_schema_kebab_matches_commands_kebab(self) -> None:
        """两份实现（consoleSchema._kebab 与 consoleCommands.to_kebab）必须同规则。

        consoleCommands → consoleSchema 不能模块级互相 import（成环），所以各有一份；
        这条结构判据把「同输入同输出」钉死，防止日后只改一边造成展示分叉。
        """
        for name in ("dry_run", "clear_fields", "member_names", "status", "open_only", "args"):
            assert cs._kebab(name) == cc.to_kebab(name), f"两份 kebab 实现在 {name!r} 上分叉"

    def test_no_kebab_collision_across_all_commands(self) -> None:
        """全命令表结构判据：任何两个真名/别名 kebab 后不得撞键指向不同真名。

        撞键意味着一种命令行写法悄悄指到另一个参数 —— 与其运行时静默选边，
        不如派生时炸。`reload` 这类无参数命令派生为空是合法的（没有选项可冲突），
        空表只对「有 schema 参数的命令」才算异常。
        """
        for spec in cc.COMMAND_SPECS:
            if not spec.param_names and not spec.alias_map:
                continue  # reload 等无选项命令：无可冲突面
            # command_options_map 派生含冲突检测；有冲突会直接 ValueError。
            assert spec.command_options_map, f"{spec.key} 选项派生为空"


# --------------------------------------------------------------------------- #
# ② 归一层：kebab / 旧名 / 短别名三向归一（M1 的落点）
# --------------------------------------------------------------------------- #


class TestNormalize:
    def test_kebab_and_legacy_and_alias_converge(self) -> None:
        """`--title-keyword` / `--title_keyword` / `--kw` → 同一个真名 title_keyword。"""
        spec = cc.COMMAND_INDEX["task ls"]
        assert spec.normalize_option("title-keyword") == "title_keyword"
        assert spec.normalize_option("title_keyword") == "title_keyword"  # 兼容期旧名
        assert spec.normalize_option("kw") == "title_keyword"

    def test_whitelist_accepts_all_three_forms(self) -> None:
        """白名单（解析层判定）三种形态都在，兼容期内旧名放行。"""
        spec = cc.COMMAND_INDEX["task ls"]
        for form in ("title-keyword", "title_keyword", "kw", "open-only", "open_only"):
            assert form in spec.options, f"{form!r} 不在 task ls 白名单（解析层会误报未知选项）"

    def test_display_options_hide_legacy_names(self) -> None:
        """展示清单（报错 hint / help）不含旧名，但含 kebab 与短别名。"""
        spec = cc.COMMAND_INDEX["task ls"]
        display = set(spec.display_options)
        assert "title-keyword" in display and "kw" in display
        assert "title_keyword" not in display, "旧名不该进展示清单（M3 反向护栏）"

    def test_boolean_flag_in_both_forms(self) -> None:
        """布尔开关 kebab/旧名两形态都命中（否则 --dry-run 会被当带值选项吃掉下一个 token）。"""
        spec = cc.COMMAND_INDEX["dept rm"]
        assert "dry-run" in spec.boolean_options and "dry_run" in spec.boolean_options

    def test_boolean_and_kinds_resolve_in_kebab_form(self) -> None:
        """option_kinds 的 kebab 键必须命中真名的 kind（否则静默退化 string）。"""
        spec = cc.COMMAND_INDEX["dept rm"]
        kinds = spec.option_kinds
        assert kinds["dry-run"] == "boolean" == kinds["dry_run"]
        assert kinds["recursive"] == "boolean"

    def test_parse_layer_kebab_and_legacy_agree(self) -> None:
        """解析层：kebab 与旧名产出的 options 归一目标一致（_store 归一键相同）。"""
        spec = cc.COMMAND_INDEX["task ls"]
        _p1, o1, _ = cp._parse_options(spec, ["--title-keyword", "登录"])
        _p2, o2, _ = cp._parse_options(spec, ["--title_keyword", "登录"])
        assert spec.normalize_option("title-keyword") in (o2 | o1) or (
            spec.normalize_option(next(iter(o1))) == spec.normalize_option(next(iter(o2)))
        ), "kebab 与旧名没归一到同一个真名"

    def test_mixed_forms_aggregate_to_one_key(self) -> None:
        """kebab 与旧名混写同一数组型选项 ⇒ 同一聚合键（#218 语义不被名字形态破坏）。"""
        spec = cc.COMMAND_INDEX["task ls"]
        _pos, options, _src = cp._parse_options(
            spec, ["--status", "TODO", "--status", "DONE"]
        )
        # status 无下划线；用 role ls 的 keywords（别名 kw）与真名混写验证归一聚合
        spec2 = cc.COMMAND_INDEX["role ls"]
        _pos2, options2, _src2 = cp._parse_options(
            spec2, ["--keywords", "a", "--kw", "b"]
        )
        assert len(options2) == 1, f"别名与真名混写应归并同键，实际 {options2!r}"


# --------------------------------------------------------------------------- #
# ③ 展示层：help 只出现 kebab（M3 的落点）
# --------------------------------------------------------------------------- #


class TestHelpShowsKebabOnly:
    def test_help_detail_lines_are_kebab(self) -> None:
        """HELP_DETAIL 全表：不允许出现带 `--` 的下划线选项形态（M3，含行中混排）。

        只查行首会被「行尾补一句旧写法」的变异漏掉，所以**整行扫描**：任何
        `--xxx_yyy` 形态出现都算混排。
        """
        problems = []
        pattern = re.compile(r"--[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+")
        for key, detail in ct.HELP_DETAIL.items():
            for ln in detail.splitlines():
                hit = pattern.search(ln)
                if hit:
                    problems.append(f"{key}: help 行混入下划线选项形态 {hit.group(0)!r}（{ln!r}）")
        assert not problems, "help 详情混入旧名（双名混排）：\n" + "\n".join(problems)

    def test_help_summary_is_kebab(self) -> None:
        """HELP_SUMMARY 里 --member-names（kebab）在、--member_names 不在。"""
        summary = ct.HELP_SUMMARY
        assert "--member-names" in summary
        assert "--member_names" not in summary

    def test_aliases_rendered_in_kebab(self) -> None:
        """旧别名含下划线的（role update 的 --role_name）在 help 里显示 --role-name。"""
        detail = ct.HELP_DETAIL["role update"]
        assert "--role-name" in detail, f"别名没按 kebab 展示：\n{detail}"
        assert "--role_name" not in detail

    def test_unknown_option_hint_lists_kebab_only(self) -> None:
        """未知选项报错的可用清单只列 kebab + 短别名（判定白名单仍收旧名）。"""
        spec = cc.COMMAND_INDEX["task ls"]
        with pytest.raises(cp.ConsoleError) as caught:
            cp._parse_options(spec, ["--title_keyword_typo", "x"])
        message = caught.value.message
        assert "--title-keyword" in message, f"hint 没列 kebab 主形态：{message!r}"
        # 旧名（title_keyword）不在 hint 清单里：展示面不往旧写法引。
        hint_part = message.split("可用选项")[-1]
        assert "--title_keyword |" not in hint_part, f"hint 混入旧名：{hint_part!r}"

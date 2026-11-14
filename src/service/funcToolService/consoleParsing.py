"""`console` 工具：把 16 个团队管理工具收敛成一条命令行。

设计依据是仓库根 `draft_v_console.md`（只读），落地口径见 #154 的 D1–D6。
本模块负责三件事，顺序也是执行顺序：

1. **解析**：`shlex` 切词（绝不 `eval`、绝不调 shell，D5），认出子命令；
2. **校验输入来源**：内联 JSON 与 `--from payload` 二选一且互斥（D1、CC3）。

3. **路由**在 `consoleTool`：它只负责「命令 → 目标工具 + 参数」。

选项白名单与「哪些选项是不带值的开关」**不在这里手写**（#196 P2）：两者都从
`CommandSpec` 派生，而 `CommandSpec` 又从被转发工具的 JSON Schema 派生
（`consoleSchema`）。`--dry-run` 这类新增布尔参数因此自动成为开关，不会被解析器
当成「需要值的选项」把下一个 token 吃掉。

选项名支持**双轨**（真名 + 别名，见 `consoleCommands._OPTION_ALIASES`）：白名单
同时认两者，但 `options` 字典里**存用户写下的原名**。原因是既有用例把
`options["remove-field"]` 当契约钉住了；归一（别名 → 工具参数名）只在
`consoleTool` 组装转发参数时做。

**权限校验不在这里**（#160 起）：`console` 自己不再重算一遍权限，而是把调用
转发给等价的原工具，由唯一执行入口 `AgentToolRegistry.execute_tool_call` 做鉴权。
这样「原工具调不了、console 却能调」的错位在结构上不可能出现——判定只有一份实现。
`consoleCommands` 里每条命令仍然声明 `legacy_tool`，它是**转发目标**（也是
`CATEGORY_CONFIG` 里那一行对应的工具），不是「权限查表的键」。
"""

from __future__ import annotations

import shlex
from typing import Any, Optional

from . import consoleCommands as cc

# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------

class ConsoleError(Exception):
    """console 层的结构化错误。`code` 直接作为返回体的 `error_code`。"""

    def __init__(self, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra


def _error_payload(exc: ConsoleError) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "success": False,
        "error_code": exc.code,
        "message": exc.message,
    }
    payload.update(exc.extra)
    return payload


def _tokenize(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=True)
    except ValueError as exc:  # 未闭合引号等
        raise ConsoleError(
            "invalid_command_syntax",
            f"命令引号/转义不合法：{exc}",
            example="dept ls",
        ) from exc


class ParsedCommand:
    """解析结果。`options` 是 `--key value` 或布尔开关。"""

    def __init__(
        self,
        spec: cc.CommandSpec,
        *,
        positional: list[str],
        options: dict[str, Any],
        input_source: str | None,
        raw_tokens: list[str],
    ) -> None:
        self.spec = spec
        self.key = spec.key
        self.resource = spec.resource
        self.action = spec.action
        self.positional = positional
        self.options = options
        self.input_source = input_source
        self.raw_tokens = raw_tokens

    @property
    def target(self) -> str | None:
        return self.positional[0] if self.positional else None


#: 全局「请求帮助」开关（#196 P2 D12）：`--help` / `-h` 可以出现在**任何位置**，
#: 命中即短路返回该命令的 help，且不占用选项白名单（否则每条命令都要在 schema 里
#: 造一个并不存在的 `help` 参数）。
#:
#: 为什么保留裸 `help` 命令：`help task update` 这种「先看命令再问细节」的写法既有
#: 用例与提示词都在用；`--help` 是给「已经把命令敲到一半」的场景。
HELP_FLAGS = ("--help", "-h")


_TRUE_VALUES = frozenset({"true", "1", "yes", "on"})
_FALSE_VALUES = frozenset({"false", "0", "no", "off"})


def parse_boolean_value(raw: str, *, name: str, command: str) -> bool:
    """`--flag=<文本>` 的布尔解析。

    裸 `--flag` 是 True；显式 `=false` 必须真的关闭（`role update` 的
    `overwrite` 依赖这一点，否则写了 false 反而被当成 true）。
    """
    normalized = raw.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ConsoleError(
        "invalid_option_value",
        f"--{name} 只接受 true/false（也接受 1/0、yes/no、on/off），收到 {raw!r}。",
        example=f"{command} --{name} false",
    )


def _parse_options(spec: cc.CommandSpec, tokens: list[str]) -> tuple[list[str], dict[str, Any], str | None]:
    """把 token 拆成 (位置参数, 选项, 输入来源)。

    `--from` 是输入来源选择器，单独提取，不进 `options`（它不在 `spec.options` 里，
    否则会被「未知选项」误报）。

    白名单与布尔开关都按 `spec` 派生（#196 P2）：`spec.options` 是真名 + 别名，
    `spec.boolean_options` 是 schema 里 `type: boolean` 的那批（含其别名形态）。
    `options` 里存的是**用户写下的原名**（别名不归一），归一由路由层做。

    ## 重复选项（#218，翻 #187 裁定④）

    同一个参数重复出现时按**归一后的目标名**计数（`--remove-field` 与
    `--clear_fields` 算同一个），再按类型分流：

    - **数组型**（schema `type: array`：`--status` / `--member_names` /
      `--remove-field` 等）：按出现顺序**聚合**。值存成「每次出现的原始字符串」
      列表，CSV 拆分与去重保序统一由 `consoleTool._split_csv` 做（那里已支持
      list 入参），解析层不再自己拆逗号 —— 否则同一件事有两个实现。
    - **标量型**（string / integer / object）：报 `duplicate_option`。覆盖式赋值
      会**静默丢掉前一个值**，那正是 #187-④ 后来被 Operator 翻案的行为。
    - **布尔开关**：幂等，重复出现不报错（末次显式值生效）。

    聚合落在**首次写下的原名**这个键上：别名与真名混写（`--remove-field a
    --clear_fields b`）必须合并成同一个键，否则路由层归一后会在 `args` 里看到
    两个同义键、后一个覆盖前一个 —— 聚合就白做了。
    """
    positional: list[str] = []
    options: dict[str, Any] = {}
    input_source: str | None = None

    boolean_options = spec.boolean_options
    allowed = spec.options
    kinds = spec.option_kinds
    #: 归一后的目标名 → `options` 里实际使用的键（首次写下的原名）
    first_name: dict[str, str] = {}

    def _store(name: str, value: Any) -> None:
        """把一次出现的选项值落到 `options`（重复语义见本函数 docstring）。"""
        target = spec.normalize_option(name)
        kind = kinds.get(target) or kinds.get(name) or "string"
        existing = first_name.get(target)
        if existing is None:
            first_name[target] = name
            options[name] = value
            return
        if kind == "array":
            current = options[existing]
            options[existing] = [current, value] if not isinstance(current, list) else current + [value]
            return
        if kind == "boolean":
            options[existing] = value
            return
        # #219（D2，小马哥 11:48 拍板「建议必须与 kind 匹配」）：这里 kind 一定是
        # 标量（string/integer/object —— 数组型上面已聚合、布尔型上面已幂等），
        # 任何「逗号写进一个 --x」的建议对 integer/object 都**不可执行**
        # （实测：integer ⇒ invalid_target，object ⇒ invalid_option_value），
        # 不可执行的建议比不给更坏。所以标量重复**只提示原因与改法**、不给
        # `suggestion` 字段；「逗号 / 重复写法」的引导只放在数组型参数的
        # help 标注里（consoleSchema.describe_param），那里才匹配 kind。
        raise ConsoleError(
            "duplicate_option",
            # 展示 kebab 归一名（#221）：用户写 `--open_only a --open_only b`（旧名）
            # 也按主形态 --open-only 提示 —— 兼容期两者都通，报错只认主形态。
            f"--{cc.to_kebab(target)} 重复出现：它是 {kind} 型**标量**参数，只能给一次；"
            f"后一次会覆盖前一次、静默丢掉数据，因此直接拒绝。"
            f"请检查是否多写（只有数组型参数支持逗号分隔或重复出现并按顺序聚合）。",
            option=target,
            kind=kind,
        )

    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        if token.startswith("--"):
            name, sep, inline_value = token[2:].partition("=")
            name = name.strip()
            if name == "from":
                value = inline_value or (tokens[idx + 1] if idx + 1 < len(tokens) else "")
                if not value:
                    raise ConsoleError(
                        "missing_argument",
                        "--from 需要一个来源值，正确写法：--from payload",
                        example=f"{spec.key} --from payload",
                    )
                if not sep:
                    idx += 1
                input_source = value.strip()
                idx += 1
                continue
            if name not in allowed:
                # 可用清单只展示 kebab + 短别名（#221 需求 3：旧名是兼容期别名，
                # 不在展示面露脸把用户往旧写法引）；**判定**仍用含旧名的白名单。
                hint = (
                    f"可用选项：{' | '.join('--' + o for o in spec.display_options)}"
                    if spec.display_options else "这条命令不接受选项"
                )
                raise ConsoleError(
                    "unknown_option",
                    f"未知选项：--{name}。{hint}",
                    example=spec.key,
                )
            if name in boolean_options:
                _store(
                    name,
                    parse_boolean_value(inline_value, name=name, command=spec.key)
                    if sep
                    else True,
                )
                idx += 1
                continue
            if sep:
                _store(name, inline_value)
                idx += 1
                continue
            if idx + 1 >= len(tokens):
                raise ConsoleError(
                    "missing_argument",
                    f"--{cc.to_kebab(name)} 需要一个值",
                    example=f"{spec.key} ... --{cc.to_kebab(name)} <值>",
                )
            _store(name, tokens[idx + 1])
            idx += 2
            continue
        positional.append(token)
        idx += 1

    return positional, options, input_source


# ---------------------------------------------------------------------------
# 配置输入（#154 D1：v1 只有内联 JSON + --from payload）
# ---------------------------------------------------------------------------

def _resolve_input_payload(
    config_tokens: list[str],
    payload: dict[str, Any] | None,
    *,
    input_source: str | None,
    command_key: str,
) -> dict[str, Any]:
    """按 D1 与 CC3 解析配置输入，返回最终的 JSON object。

    `config_tokens` 是**已经剥掉 target** 的位置参数（create 的全部、update 的第 1 位
    之后）；调用方负责切分，本函数不再需要构造一个临时的 `ParsedCommand` 来「跳过
    target」——那层克隆只是把同一件事又描述了一遍。

    四种情形**必须严格区分**（含糊报「解析失败」等于让模型没法自查）：
    - `--from payload` + 无 payload → `missing_payload`
    - `--from payload` + 内联 JSON 同时出现 → `ambiguous_input_source`
    - 未用 `--from` 却传了 payload → `ambiguous_input_source`
    - `--from local:...` / heredoc → `unsupported_input_source`（v1 明确不支持）
    """
    if input_source is not None:
        normalized = input_source.strip()
        if normalized.startswith("local:"):
            raise ConsoleError(
                "unsupported_input_source",
                "v1 暂不支持 local:<path> 文件来源；请改用内联 JSON 或 --from payload。",
                supported=list(cc.SUPPORTED_FROM_SOURCES),
            )
        if normalized.startswith("<<"):
            raise ConsoleError(
                "unsupported_input_source",
                "v1 暂不支持 heredoc 多行输入；请改用内联 JSON 或 --from payload。",
                supported=list(cc.SUPPORTED_FROM_SOURCES),
            )
        if normalized != "payload":
            raise ConsoleError(
                "unsupported_input_source",
                f"未知的输入来源：{normalized!r}；v1 只支持 payload。",
                supported=list(cc.SUPPORTED_FROM_SOURCES),
            )
        # --from payload
        if config_tokens:
            raise ConsoleError(
                "ambiguous_input_source",
                "--from payload 与内联 JSON 不能同时使用。",
                example=f"{command_key} --from payload   （配置放 payload 字段）",
            )
        if payload is None:
            raise ConsoleError(
                "missing_payload",
                "使用 --from payload 时必须在 payload 字段提供配置对象。",
                example=f"{command_key} --from payload",
            )
        return _require_json_object(payload)

    # 未使用 --from
    if payload is not None:
        raise ConsoleError(
            "ambiguous_input_source",
            "提供了 payload 但命令没有用 --from payload；请二选一。",
            example=f"{command_key} --from payload   或   {command_key} '{{...}}'",
        )

    if not config_tokens:
        # 没有配置输入在解析层是合法的（#198）：create/update 都可以只用 `--选项`。
        # 「create 连选项也没有」= `missing_input_source`、「update 没有动作」=
        # `no_update_action`，都属于**命令的业务语义**，由路由层判定 —— 解析层只回答
        # 「输入来源是否合法」，不认识某条命令需要哪些字段。
        return {}
    if len(config_tokens) > 1:
        raise ConsoleError(
            "ambiguous_input_source",
            f"只能提供一个配置输入，收到 {len(config_tokens)} 段：{config_tokens!r}",
        )
    raw = config_tokens[0]
    if raw.lstrip().startswith("<<"):
        raise ConsoleError(
            "unsupported_input_source",
            "v1 暂不支持 heredoc 多行输入；请改用内联 JSON 或 --from payload。",
            supported=list(cc.SUPPORTED_FROM_SOURCES),
        )
    return _parse_inline_json(raw)


def _parse_inline_json(raw: str) -> dict[str, Any]:
    import json as _json

    try:
        data = _json.loads(raw)
    except _json.JSONDecodeError as exc:
        raise ConsoleError(
            "invalid_source_json",
            f"内联 JSON 解析失败：{exc.msg}（第 {exc.lineno} 行第 {exc.colno} 列）",
            example="dept create '{\"name\":\"x\",\"responsibility\":\"y\"}'",
        ) from exc
    return _require_json_object(data)


def _require_json_object(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ConsoleError(
            "invalid_payload_field",
            f"配置必须是 JSON object，收到 {type(data).__name__}。",
        )
    return data


def _input_payload_for(parsed: ParsedCommand, payload: dict[str, Any] | None) -> dict[str, Any]:
    """create/update 用：位置参数里「配置」占第几位。

    - `create <json>`：第 0 位是配置。
    - `update <target> <json>`：第 0 位是 target，第 1 位是配置。
    """
    if not parsed.spec.accepts_input:
        return {}
    config_tokens = parsed.positional[1:] if parsed.spec.needs_target else parsed.positional
    return _resolve_input_payload(
        config_tokens,
        payload,
        input_source=parsed.input_source,
        command_key=parsed.key,
    )


# 目标定位全部由 `CommandSpec.locator` 声明、`consoleTool._locator_args` 一处翻译成
# 工具参数；同名消歧已下沉到 `tools.save_agent`（工具层对直接调用同样生效）。
# `console*` 四个模块自此不再 import `dal`。

"""console 工具的静态部分：命令表、转发目标、help 文本。

拆成独立模块的原因有两个（都是硬理由，不是「分层好看」）：
1. **「命令 → 转发目标」必须是一张纯数据表**（#160）。权限判定现在完全由唯一
   执行入口 `AgentToolRegistry.execute_tool_call` 在第 2 跳做，命令表只负责声明
   这条命令等价于哪个原工具（`legacy_tool`）。加命令时**必须同时填等价工具名**，
   漏填会让转发目标为空、命令直接不可用，而不是「没填就放行」。
2. **help 文本要能单独量体积**（#154 CC6）。它是唯一额外注入 context 的文本，
   跟解析逻辑混在一个文件里会让「help 有多大」这件事变得难以独立测。

只依赖标准库，不 import 任何 service 模块 —— 这样单元测试可以脱离 DB 直接跑。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# ---------------------------------------------------------------------------
# 单点开关（#154 D2）：原 16 个工具是否保留。
#
# 为什么用环境变量而不是模块级常量：测 token 收益时要**在不改代码**的前提下对比
# 两种暴露面，环境变量能让同一次构建跑两个配置。默认值 = 保留，切换主干暴露面
# 由 Operator 决定，不在 #154 范围。
#
# 注意它只影响 `get_tools()` 的**暴露列表**，不影响 `console` 子命令的路由能力
# —— 停用原工具后 console 仍要能调到底层函数（这是收敛的前提，不是漏洞）。
# ---------------------------------------------------------------------------
ENV_KEEP_LEGACY_TOOLS = "TOGOSPACE_KEEP_LEGACY_ADMIN_TOOLS"

_CONSOLE_NAME = "console"

#: 被 `console` 收敛的 16 个原工具名（draft §1 的清单，逐条对齐）。
#:
#: ⚠️ **本清单目前未包含任务三工具**（`list_tasks` / `get_task_detail` / `update_task`），
#: 而 `task ls|get|update` 三条命令已经把这三者作为转发目标接进来了（#187）。
#: 也就是说：D2 开关置假（隐藏原工具）时，task 三条命令**仍可达**——因为它们的转发
#: 目标不在本清单里，不会被隐藏。
#:
#: 这不是「不自洽」，性质是**未完成的收敛**：本清单是 D2 开关的语义载体，一旦改动
#: 就等于改变所有启用 console 的 Agent 的工具暴露面，属 draft §1 已定死的口径，
#: 须 Operator 决策，不能由实现单方面扩表（#187 裁定②）。故此处**保持 16 项不动**，
#: 只留注释与报告里的一条「只报不改」记录，供后续收敛时一并处理。
LEGACY_CONVERGED_TOOLS: tuple[str, ...] = (
    "get_dept_info",
    "save_dept",
    "delete_dept",
    "get_agent_info",
    "save_agent",
    "wake_up_agent",
    "start_chat",
    "get_room_info",
    "save_room",
    "delete_room",
    "list_role_templates",
    "get_role_template",
    "save_role_template",
    "delete_role_template",
    "get_time",
    "reload_team",
)


InputSource = Literal["inline", "payload", "local", "heredoc"]


@dataclass(frozen=True)
class CommandSpec:
    """一条 console 子命令的静态定义。

    Attributes:
        resource: 资源词（dept / agent / room / role / time / help / 空串表示顶层命令）。
        action: 操作词（ls / get / create / update / rm / wake / dm / reload）。
        legacy_tool: 这条命令转发到的原工具名（#160）。它是**转发目标**，权限判定
            由统一入口在第 2 跳做，所以它必须非空；只有 `help` 这种纯本地命令可以留空。
        accepts_input: 是否接受配置输入（内联 JSON / `--from`）。
        needs_target: 是否需要位置参数 target。
        input_optional: `accepts_input` 为真时，「没有配置输入」是否合法（#187）。
            默认 False ⇒ 沿用 create/update 的老语义：没输入就报 `missing_input_source`。
            只有 `task update` 置 True：它的配置输入和 `--remove-field` 都是可选的，
            两者都没有时才由路由层显式报 `no_update_action`（见 `consoleTool._route`）。
            —— 为什么不用「先判断 options 再决定要不要输入」的做法：那样把「这条命令
            能不能没有输入」这件事藏进路由的 if 分支里，命令表就不再是唯一事实源了。
        self_interrupt: 执行时会不会自中断当前 turn（#154 D6）。目前只有 `reload`。
        min_args / max_args: 位置参数个数约束。
    """

    resource: str
    action: str
    legacy_tool: str
    accepts_input: bool = False
    needs_target: bool = False
    input_optional: bool = False
    self_interrupt: bool = False
    min_args: int = 0
    max_args: int = 0
    #: 可用选项名（不带前导 `--`），用于「未知选项」报错时给出正确候选。
    options: tuple[str, ...] = field(default_factory=tuple)

    @property
    def key(self) -> str:
        return f"{self.resource} {self.action}".strip()


def _spec(
    resource: str,
    action: str,
    legacy_tool: str,
    *,
    accepts_input: bool = False,
    needs_target: bool = False,
    input_optional: bool = False,
    self_interrupt: bool = False,
    min_args: int = 0,
    max_args: int = 0,
    options: tuple[str, ...] = (),
) -> CommandSpec:
    return CommandSpec(
        resource=resource,
        action=action,
        legacy_tool=legacy_tool,
        accepts_input=accepts_input,
        needs_target=needs_target,
        input_optional=input_optional,
        self_interrupt=self_interrupt,
        min_args=min_args,
        max_args=max_args,
        options=options,
    )


#: 全部 console 子命令。**这张表是权限校验与命令解析的唯一事实源。**
COMMAND_SPECS: tuple[CommandSpec, ...] = (
    # 部门
    _spec("dept", "ls", "get_dept_info"),
    _spec("dept", "get", "get_dept_info", needs_target=True, min_args=1, max_args=1),
    _spec("dept", "create", "save_dept", accepts_input=True, max_args=0),
    _spec(
        "dept", "update", "save_dept",
        accepts_input=True, needs_target=True, min_args=1, max_args=1,
    ),
    _spec(
        "dept", "rm", "delete_dept",
        needs_target=True, min_args=1, max_args=1,
        options=("recursive",),
    ),
    # 成员
    _spec("agent", "ls", "get_agent_info"),
    _spec("agent", "get", "get_agent_info", needs_target=True, min_args=1, max_args=1),
    _spec("agent", "create", "save_agent", accepts_input=True),
    _spec(
        "agent", "update", "save_agent",
        accepts_input=True, needs_target=True, min_args=1, max_args=1,
    ),
    _spec("agent", "wake", "wake_up_agent", needs_target=True, min_args=1, max_args=1),
    _spec("agent", "dm", "start_chat", needs_target=True, min_args=1, max_args=1),
    # 房间
    _spec("room", "ls", "get_room_info"),
    _spec("room", "get", "get_room_info", needs_target=True, min_args=1, max_args=1),
    _spec("room", "create", "save_room", accepts_input=True),
    _spec(
        "room", "update", "save_room",
        accepts_input=True, needs_target=True, min_args=1, max_args=1,
    ),
    _spec("room", "rm", "delete_room", needs_target=True, min_args=1, max_args=1),
    # 角色模板
    _spec("role", "ls", "list_role_templates", options=("kw",)),
    _spec("role", "get", "get_role_template", needs_target=True, min_args=1, max_args=1),
    _spec("role", "create", "save_role_template", accepts_input=True),
    _spec(
        "role", "update", "save_role_template",
        accepts_input=True, needs_target=True, min_args=1, max_args=1,
    ),
    _spec("role", "rm", "delete_role_template", needs_target=True, min_args=1, max_args=1),
    # 任务（#187）
    #
    # `legacy_tool` 照旧填转发目标，第 2 跳做权限校验与执行。task 三工具在主干上
    # 都已是 `ToolCategory.BASIC`（见 `toolRegistry.CATEGORY_CONFIG`），所以这三条命令
    # 的可达性由第 2 跳的原工具权限决定，console 侧不另算。
    _spec(
        "task", "ls", "list_tasks",
        # `--status` 接受逗号分隔多值（`TODO,DONE`），`--kw` 映射 `title_keyword`。
        # 选项名不带前导 `--`（见 `CommandSpec.options` 注释）。
        # 刻意**不**暴露 `open-only`：工具层默认就是 True（只看未完结），而给布尔开关
        # 只会得到一个「开了等于没开」的骗人选项；要含已完结任务请用 `--status` 显式列出。
        options=("status", "assignee", "reviewer", "scope", "kw", "limit"),
    ),
    _spec("task", "get", "get_task_detail", needs_target=True, min_args=1, max_args=1),
    _spec(
        "task", "update", "update_task",
        accepts_input=True, needs_target=True, min_args=1, max_args=1,
        # 任务 id 是必填位置参数；配置输入与 `--remove-field` 都可省略 —— 单独给
        # `task update 187 --remove-field reviewer_id` 是合法调用（撤销验收人），
        # 它没有配置输入。详见 `CommandSpec.input_optional`。
        input_optional=True,
        options=("remove-field",),
    ),
    # 其他
    _spec("time", "get", "get_time", options=("tz",)),
    _spec("reload", "", "reload_team", self_interrupt=True),
)

#: `help` 是纯本地命令，不映射任何原工具，也不做权限校验（它不碰数据）。
HELP_SPEC = _spec("help", "", "")

COMMAND_INDEX: dict[str, CommandSpec] = {spec.key: spec for spec in COMMAND_SPECS}

#: 同一资源下可用的操作，用于报错时给候选。
ACTIONS_BY_RESOURCE: dict[str, tuple[str, ...]] = {}
for _spec_item in COMMAND_SPECS:
    _existing = ACTIONS_BY_RESOURCE.get(_spec_item.resource, ())
    ACTIONS_BY_RESOURCE[_spec_item.resource] = _existing + (_spec_item.action,) if _spec_item.action else _existing

RESOURCE_NAMES: tuple[str, ...] = tuple(ACTIONS_BY_RESOURCE.keys())

#: 顶层命令（没有资源词）。
TOP_LEVEL_COMMANDS: tuple[str, ...] = ("help", "reload")

#: `--from` 支持的来源前缀。v1 只放开 `payload`（#154 D1），其余**显式拒绝**。
SUPPORTED_FROM_SOURCES: tuple[str, ...] = ("payload",)
UNSUPPORTED_FROM_SOURCES: dict[str, str] = {
    "local": "v1 暂不支持 local:<path> 文件来源（D1）",
    "heredoc": "v1 暂不支持 heredoc 多行输入（D1）",
}


def resolve_command_spec(tokens: list[str]) -> tuple[CommandSpec | None, str | None]:
    """从 token 列表里认出命令，返回 (spec, 错误信息)。

    `help` 单独处理：`help`、`help dept`、`help dept create` 都合法，
    且不参与 `COMMAND_INDEX` 的两段式查找。
    """
    if not tokens:
        return None, "命令为空"

    head = tokens[0].lower()

    if head in ("help", "?"):
        return HELP_SPEC, None

    if head == "reload":
        if len(tokens) == 1:
            return COMMAND_INDEX["reload"], None
        # `reload` 不接受任何位置参数（也不接受选项，见 #165 删 confirm 闸门）
        return COMMAND_INDEX["reload"], None

    spec = COMMAND_INDEX.get(f"{head} {tokens[1].lower()}" if len(tokens) > 1 else "")
    if spec is not None:
        return spec, None

    if head not in ACTIONS_BY_RESOURCE:
        return None, (
            f"未知资源：'{head}'。可用资源：{' | '.join(RESOURCE_NAMES)}"
        )
    return None, (
        f"未知命令：'{head} {tokens[1] if len(tokens) > 1 else ''}'。"
        f"可用操作：{' | '.join(ACTIONS_BY_RESOURCE[head])}"
    )

# Console 工具设计与实现评估报告（含 Review 校核版）

> 评估范围：`src/service/funcToolService/consoleCommands.py`、`consoleSchema.py`、`consoleParsing.py`、`consoleTool.py` 及其与 `core.py`、`tools.py`、`toolRegistry.py`、各 Driver（`nativeDriver` / `tspDriver` / `claudeSdkDriver`）的协作边界。

## 一、现状概览

经过 #154（初版落地）、#160（`ForwardToolCall` 统一权限收口）、#165（去 confirm 闸门）、#187（接入 task 三工具）、#196 P1/P2（PATCH 下沉 + 命令表与 Schema 派生）多轮迭代，`console` 的主干设计已经成型：

1. **命令表单一事实源**（`consoleCommands.COMMAND_SPECS`）：声明 `<resource> <action>` 到 `legacy_tool` 的映射、动作动词（`LIST` / `GET` / `SAVE` / `DELETE` / `DIRECT`）及位置参数定位方式（`Locator`）。
2. **Schema 自动派生**（`consoleSchema.py`）：选项白名单、布尔开关、参数类型转换、`HELP_DETAIL` 参数列表均从 `tools.py` 的函数签名与 docstring 单向派生。
3. **两跳转发鉴权**（`ForwardToolCall` -> `AgentToolRegistry.execute_tool_call`）：`console` 本身不做权限判定，统一抛 `ForwardToolCall` 交由注册表第 2 跳按原工具的 `enabled` 状态鉴权与执行。

在此基础上，当前实现仍残留若干**多轮迭代留下的跨层特判、契约摩擦与冗余代码**，可按「设计与架构」与「代码实现」两个层面进一步简化。

---

## 二、设计与架构层面的改进空间

### 1. 用 4 态可见性枚举 `ToolVisibility` 统一控制 Tool 与 Console 暴露面（取代 `LEGACY_CONVERGED_TOOLS` 并解决 D2 死锁）

#### 现状问题
- **D2 开关关闭原工具时，`console` 转发同步失效（#166-A）**：`core.get_tools()` 在 `TOGOSPACE_KEEP_LEGACY_ADMIN_TOOLS=0` 时直接过滤掉 16 个原工具；三个 Driver（`nativeDriver.py:22`、`tspDriver.py:480`、`claudeSdkDriver.py:94`）仅将 `get_tools()` 返回的工具注册到 `AgentToolRegistry`。当 `console` 抛出 `ForwardToolCall` 进入第 2 跳时，`toolRegistry.execute_tool_call` 在 `_tools_by_name` 中查不到目标工具，导致隐藏模式下 23 条命令全部失败并返回 `未知工具: <legacy_tool>`（详见 `core.py:73-125` 与 `test_console_d2_execution.py`；注意 `consoleCommands.py:34-35` 旧注释仍声称「不影响路由能力」，与现状相反，需修正）。
- **「是否收敛」靠双名单拼凑，语义割裂**：`consoleCommands.LEGACY_CONVERGED_TOOLS` 维护了一份 16 项硬编码元组，而 #187 接入的 `task ls|get|update` 转发到 `list_tasks` / `get_task_detail` / `update_task`，却不在该元组里，测试里又被迫再造一个 `NON_CONVERGED_FORWARD_TARGETS` 来描述「可转发但不隐藏」的中间态。
- **多余的 JSON 序列化往返**：第 1 跳 `console` 已将参数组装为 Python `dict` 放入 `ForwardToolCall.tool_args`，`toolRegistry.py:338` 将其 `json.dumps` 成字符串，紧接着 `core.run_tool_call`（`core.py:284`）又立即 `json.loads` 反序列化回 `dict`。

#### 改进方案：每个工具方法绑定一个 4 态可见性枚举 `ToolVisibility`

将每个工具在 **独立 Tool（LLM Schema 直调）** 与 **Console（命令行转发）** 两个维度上的可见性正交组合为 4 个枚举值（内部语义枚举，继承 `EnhanceEnum` 并使用 `auto()`），且与权限面 `enabled` 保持正交：

```python
class ToolVisibility(EnhanceEnum):
    """工具在「独立 Function Tool」与「Console 子命令」两个入口上的可见性（2×2=4）。"""
    NONE         = auto()  # tool=False, console=False：两边均不可见/不可用
    TOOL_ONLY    = auto()  # tool=True,  console=False：仅作为独立 Tool 暴露（如 send_chat_msg、create_task、console 自身）
    CONSOLE_ONLY = auto()  # tool=False, console=True ：仅通过 console 子命令暴露（已收敛完成的管理工具目标态）
    BOTH         = auto()  # tool=True,  console=True ：独立 Tool 与 console 子命令同时可用（双轨过渡态，或 task 查询/更新）

    @property
    def tool_visible(self) -> bool:
        return self in (ToolVisibility.TOOL_ONLY, ToolVisibility.BOTH)

    @property
    def console_visible(self) -> bool:
        return self in (ToolVisibility.CONSOLE_ONLY, ToolVisibility.BOTH)
```

| 枚举值 | 独立 Tool 暴露 & 直调 (`hop == 0`) | Console `help` 展示 & 第 2 跳转发 (`hop == 1`) | 典型适用工具 |
| --- | :---: | :---: | --- |
| `NONE` | 否 | 否 | 临时下线或当前环境禁用的工具 |
| `TOOL_ONLY` | 是 | 否 | `send_chat_msg`、`finish_action`、`create_task`、`web_search`、`memory_*`、`console` 自身 |
| `CONSOLE_ONLY` | 否 | 是 | 收敛后的 16 个管理工具（`save_dept`、`save_agent`、`reload_team` 等） |
| `BOTH` | 是 | 是 | 双轨过渡期的管理工具，或同时保留独立工具与命令行入口的 `list_tasks` / `get_task_detail` / `update_task` |

##### 实施关键口径（Review 补充约束）
1. **注册面与暴露面分离，且同时覆盖 OpenAI 与 Claude SDK 两个暴露出口**：
   - Driver 注册阶段使用全量注册入口（如 `get_all_tools()` 或让 `get_tools()` 负责注册面），将所有 `visibility != ToolVisibility.NONE` 的工具全部注册进 `AgentToolRegistry._tools_by_name`，统一通过 `apply_tool_allow_specs` 计算 `registered.enabled`。
   - **模型可见工具过滤必须收口在同一处**：不仅 `export_openai_tools()`（用于 `nativeDriver` / `tspDriver`）要过滤 `item.enabled and item.effective_visibility.tool_visible`，**`list_enabled_tool_names()`（`claudeSdkDriver.py:113` 用它构建 MCP 工具列表）也必须同步过滤 `tool_visible`**（或拆分为 `list_exposed_tool_names()` 供 Driver 暴露使用），否则 `CONSOLE_ONLY` 工具会出现在 Claude SDK 的工具列表里，但模型一调就被 `hop == 0` 拒绝。
2. **动态读取环境变量 `TOGOSPACE_KEEP_LEGACY_ADMIN_TOOLS`**：
   - 现有测试（`test_console_meta.py`、`test_console_d2_execution.py`）依赖在 `load_func_tools()` 之后动态 `monkeypatch.setenv` 切换暴露面。
   - 因此不能只在启动构造 `FuncTool` 时静态定死 `visibility`：收敛工具声明基准可见性（或标记受 D2 开关控制），在读取 `effective_visibility`（或导出层）时按 `keep_legacy_admin_tools()` 动态在 `BOTH` 与 `CONSOLE_ONLY` 之间解析。
3. **执行入口（`execute_tool_call`）按跳数校验可见性**：
   - `hop == 0`（模型直接发起 tool_call）：要求 `registered.enabled and registered.effective_visibility.tool_visible`（拒绝模型盲猜名字直调 `CONSOLE_ONLY` 工具）。
   - `hop == 1`（`console` 抛出 `ForwardToolCall` 转发）：要求 `registered.enabled and registered.effective_visibility.console_visible`。
4. **Console 侧的 `help` 与路由按需（运行时）过滤，不在 import 期固化**：
   - `HELP_SUMMARY` 与 `_help_detail` 不能在模块 import 期或无参 `@lru_cache` 中静态烘焙 `visibility` 过滤结果（既有循环 import 风险，也会缓存过期的动态状态），应在 `_help_text()` 和 `resolve_command_spec()` / `_dispatch()` 运行时过滤掉 `not console_visible` 的命令。
5. **消除第 2 跳多余的 JSON 序列化往返（可选优化）**：
   - 若希望一并消除 `toolRegistry.py:338` 的 `json.dumps(forward.tool_args)` 与 `core.py:284` 的 `json.loads`，可让 `run_tool_call` 支持接收 `str | dict[str, Any]`（当入参已是 `dict` 时跳过 `json.loads`，直接走 `validate_tool_args` 与函数调用）。

---

### 2. 将最后两个业务特判下沉到 `tools.py`，使 `console` 成为 100% 纯内存语法层

#### 现状问题
#196 P1/P2 将 `dept`、`agent`、`room` 的 PATCH 语义下沉到了 `tools.py`，唯独留下两个例外，迫使原本只做语法解析与转发的 `consoleTool` / `consoleParsing` 依然在函数内 `import dal.db` 查库，且整条 `_dispatch` 链路不得不保持 `async`：

1. **`role update` 的本地读库补齐（`CommandSpec.needs_padding`）**
   - `tools.save_role_template(name, type, soul, ...)` 的三个参数全部必填且无 PATCH 语义。
   - 因此 `consoleTool._pad_missing_required`（`consoleTool.py:558-579`）只能在转发前查 `gtRoleTemplateManager.get_role_template_by_name`，手动补齐 `type`、`soul`、`i18n` 并强设 `args["overwrite_existing"] = True`，还在 `CommandSpec` 上专门定义了一个只用一次的 `needs_padding: bool` 字段。
2. **`agent update` 的本地同名消歧（`Locator.ambiguous_by_name`）**
   - `GtAgent` 允许同团队内存在同名记录（离职成员名字被复用），而 `tools.save_agent` 按 `name` 定位时调用 `gtAgentManager.get_agent()` 只取第一条，可能静默改错人。
   - 因此 `consoleParsing._resolve_agent_by_target`（`consoleParsing.py:319-356`）和 `consoleTool._save_locator_args`（`consoleTool.py:533-555`）在转发前先查库做同名检查并把 `name` 换成 `agent_id`。
   - 这不仅让解析层耦合了数据库，还带来两处不一致：
     - 直接调用 `save_agent(name=...)` 依然存在同名静默改错人的风险；
     - `console` 里 `agent update <name>` 会报 `ambiguous_target`，而 `agent get <name>` 却不报，静默返回第一条。

#### 改进方案与必补细节
1. **下沉 `save_role_template` 的 PATCH 语义（须同时处理 4 个细节）**：
   - **签名放开**：将 `save_role_template` 改为 `name: str, type: Optional[str] = None, soul: Optional[str] = None, i18n: dict | None = None, overwrite_existing: bool = False`。
   - **新建 vs 更新补值**：
     - 新建（`existing is None`）：`type` 省略时默认 `"USER"`；`soul` 省略时返回 `invalid_tool_args`（新建必须提供 `soul`）；`i18n` 省略时默认 `{}`。
     - 更新（`existing is not None`）：`type` / `soul` / **`i18n`** 省略（`is None`）时**必须保持 `existing` 的现值**（注意：当前 `tools.py:722` 写的是 `i18n=i18n or {}`，直接用会导致更新省略 `i18n` 时把已有国际化名称清空，必须改为 `existing.i18n if i18n is None else i18n`）。
   - **补齐 `USER -> SYSTEM` 拦截**：当前 `tools.py:707-710` 只拦了「新建 SYSTEM」和「修改已是 SYSTEM 的模板」，漏拦了「把已有的 USER 模板通过 `type="SYSTEM"` 改成 SYSTEM」；应在入口处统一拒绝任何 `role_type == RoleTemplateType.SYSTEM` 的写入。
   - **给 `role update` 补上 `patch_by_name=True`**：此前 `overwrite_existing=True` 是由 `_pad_missing_required`（`consoleTool.py:578`）顺手塞进去的；删掉 `_pad_missing_required` 后，**必须在 `COMMAND_SPECS` 中给 `role update` 声明 `patch_by_name=True`**（与 `dept update` / `agent update` / `room update` 一致），否则 `role update tester '{...}'` 会被 `save_role_template` 的 `overwrite_existing is False` 覆盖保护拒绝。
   - 完成上述改动后，`console` 侧可安全删除 `_pad_missing_required`、`CommandSpec.needs_padding` 以及 `role create` 的 `input_defaults=(("type", "USER"),)`。
2. **下沉 `save_agent` 的同名消歧**：
   - 在 `tools.save_agent` 按 `name` 定位（`agent_id is None`）时，用 `get_team_all_agents(team_id)` 检查同名记录数；若匹配多条，直接返回 `ambiguous_target`（含 `candidates` 列表），提示改用 `agent_id`。
   - `console` 侧可直接删除 `consoleParsing._resolve_agent_by_target`、`consoleTool._save_locator_args` 和 `Locator.ambiguous_by_name`。
   - 至此，**`console*` 四个文件彻底脱离 `dal.db` 依赖**，`_dispatch` 与 `_handle_save` 均可简化为纯同步函数。

---

### 3. 理清 `SAVE` 命令中「命令行 `--选项`」与「配置输入（JSON / payload）」的交互契约

#### 现状问题
1. **Help 展示了 `--选项`，但单用 `--选项` 会报错**：
   - `CommandSpec.options` 将底层工具的所有 schema 参数都暴露为 `--选项`（例如 `help dept create` 列出 `--name`、`--responsibility`、`--member_names`、`--parent_name`）。
   - 然而 `consoleParsing._resolve_input_payload`（`consoleParsing.py:235-250`）对除 `task update`（`input_optional=True`）以外的所有 `SAVE` 命令，强制要求提供内联 JSON 或 `--from payload`。
   - 结果是：模型按照 `help dept create` 提示的参数敲出 `dept create --name 研发部 --responsibility 研发 --parent_name root`，会被直接拒绝并报 `missing_input_source`。
2. **`--选项` 与 JSON 配置同名字段静默覆盖**：
   - `consoleTool._handle_save`（`consoleTool.py:498-499`）执行 `config = _option_args(...)` 后直接 `config.update(_input_args(...))`。若调用方同时传了 `--name A` 与 `'{"name": "B"}'`，`--name A` 会被静默覆盖。
3. **`create` 命令暴露了仅 `update` 有意义的选项**：
   - 因为 `create` 与 `update` 共用同一个 `save_*` 工具，`dept create` / `agent create` / `room create` 的选项白名单和 help 里都出现了 `--dept_id` / `--agent_id` / `--room_id` / `--clear_fields` / `--overwrite_existing`，只能靠 `_HELP_OVERLAY` 手写多段补充说明来解释（`consoleTool.py:103-122`）。

#### 改进方案（放开纯 `--选项` 传参 + 钉死错误码契约）
- **统一允许 `SAVE` 命令省略 JSON 输入（当提供了有效 `--选项` 时）**：
  - 移除 `CommandSpec.input_optional` 特判字段，解析层允许 `accepts_input=True` 的命令不带位置 JSON / `--from payload`。
  - **错误码必须按 `create` 与 `update` 区分（保持既有测试契约）**：
    - 当命令行既无有效 `--选项`、也无配置输入时：
      - **`create` 命令**（`not spec.needs_target`）必须报 **`missing_input_source`**（`test_console_tool.py:842` 对此有显式断言）；
      - **`update` 命令**（`spec.needs_target`）报 **`no_update_action`**。
  - **同名字段冲突按「别名归一后的真名」检测**：
    - `_option_args` 已将选项名通过 `spec.normalize_option(name)` 归一为工具真名（例如 `role update --role_name foo` 归一为 `name`）。
    - 当 `option_args.keys() & input_args.keys()` 非空时，显式报 `ambiguous_input_source`（指出哪个参数在命令行选项与 JSON 配置中重复提供），禁止静默覆盖。

---

## 三、代码实现层面的简化与清理清单

| 序号 | 位置 | 问题描述 | 建议改法与测试联动注意 |
| --- | --- | --- | --- |
| 1 | `consoleTool.py:747-785`<br>`consoleTool.py:675-680` | **自中断判定器漏判 `payload` 合法性（真 Bug）**：`console_triggers_self_interrupt` 复刻了 `console()` 的解析步骤，但漏掉了 `if payload is not None and not spec.accepts_input:` 检查。若模型调用 `console(command="reload", payload={"a": 1})`，判定器会返回 `True`（落盘 `SELF_INTERRUPT` 标记），但实际执行 `console()` 时会在第 676 行报 `ambiguous_input_source` 退出而不触发重载，留下假中断标记。 | 将 `payload is not None and not spec.accepts_input` 校验收进 `_parse_command_tokens(spec, tokens, payload)`，使 `console()` 与 `console_triggers_self_interrupt()` 完全共线，并补一条单测。 |
| 2 | `consoleParsing.py:289-307`<br>`consoleTool.py:696-740` | **临时对象克隆与解析函数归属**：<br>1. `consoleParsing._input_payload_for` 为了跳过 `update` 的第 0 个位置参数 `target`，克隆构造了一个临时 `ParsedCommand` 再调 `_resolve_input_payload`；<br>2. `_parse_command_tokens` 与 `_check_arity` 属于语法解析，目前放在 `consoleTool.py`。 | 1. `_resolve_input_payload` 直接接收 `config_tokens: list[str]`，去掉临时 `ParsedCommand` 构造；<br>2. **注意**：`test_console_tool.py:1354-1366` 有源码结构断言（在 `consoleTool.py` 源码字符串中查找 `def _parse_command_tokens(` 与 `cp._parse_options(`），若将 `_parse_command_tokens` 移至 `consoleParsing.py`，需同步更新该结构测试的检查目标模块。 |
| 3 | `exception.py:28-45`<br>`consoleTool.py:436`<br>`toolRegistry.py:283,334,358-375` | **`ForwardToolCall.post_process` 生产无调用**：#196 P2 将 `impact` 下沉到原工具后，生产代码中已无任何地方传入非空 `post_process`。 | **取舍项**：它是 #164 加固过的通用扩展点，且有 6 条专门单测（`test_tool_registry.py` 5 条 + `test_agent_turn_runner.py` 1 条）。建议保留机制但更新 docstring 注释（去掉已过时的「用于 `rm` impact」描述）；若决定移除则需同步清理对应测试。 |
| 4 | `consoleCommands.py:34-35`<br>`consoleCommands.py:473-476` | **过时注释与未使用常量 `UNSUPPORTED_FROM_SOURCES`**：<br>1. `:34-35` 注释仍写着「D2 不影响 console 路由能力」，与 `core.py` 实测现状相反；<br>2. `:473-476` 定义了 `UNSUPPORTED_FROM_SOURCES` 字典但全仓无引用。 | 1. Step 3 落地前先修正 `:34-35` 注释；<br>2. 删除 `UNSUPPORTED_FROM_SOURCES` 或在 `consoleParsing.py` 中实际引用。 |
| 5 | `consoleCommands.py:493-497` | **`resolve_command_spec` 中的冗余分支**：<br>`if head == "reload":`<br>`    if len(tokens) == 1: return COMMAND_INDEX["reload"], None`<br>`    return COMMAND_INDEX["reload"], None`<br>内层 if 与外层 return 完全一致。 | 直接合并为 `if head == "reload": return COMMAND_INDEX["reload"], None`。 |
| 6 | `consoleTool.py:600-627` | **`_VERB_HANDLERS` 字符串反射调用**：用字典存函数名字符串 `"_handle_list"` 再通过 `globals()[handler_name](parsed)` 调用，而 4 个处理函数就在正上方定义。 | 改为直接存放函数引用 `dict[cc.Verb, Callable[[ParsedCommand], NoReturn]]`。<br>**注意**：`test_console_tool.py:1404-1408` 读取 `_VERB_HANDLERS["DELETE"]` 并与 AST 函数名 `n.name` 比较，改为函数引用后需将该测试同步改为 `handler.__name__`。 |
| 7 | `consoleSchema.py:44-64, 123-144` | **`get_function_metadata` 重复获取**：`tool_parameters(tool_name)` 与 `tool_description(tool_name)` 各自查找 `legacy_tools` 并调用 `get_function_metadata`。 | 提取 `@functools.lru_cache(maxsize=None) def _tool_metadata(tool_name: str) -> Optional[dict]`，`tool_parameters` 与 `tool_description` 共用该缓存。 |

---

## 四、建议演进步骤（分步提交）

1. **Step 1（Bug 修复与内部代码清理）**：
   - 修复 `console_triggers_self_interrupt` 漏验 `payload` 的 Bug；
   - 清理 `resolve_command_spec` 冗余分支、`UNSUPPORTED_FROM_SOURCES` 死常量、合并 `consoleSchema._tool_metadata` 缓存、简化 `_input_payload_for`、将 `_VERB_HANDLERS` 改为函数引用并同步更新 `test_console_tool.py:1404` 断言。
2. **Step 2（下沉两处 DB 特判，净空 `console` 的数据库依赖）**：
   - 在 `tools.save_role_template` 补齐 PATCH 语义（含 `i18n` 省略保现值 + 拦截 `USER -> SYSTEM` 变更），并在 `COMMAND_SPECS` 中给 `role update` 加上 `patch_by_name=True`；
   - 在 `tools.save_agent` 下沉按名更新时的同名消歧（报 `ambiguous_target`）；
   - 删除 `console` 中的 `_pad_missing_required`、`_resolve_agent_by_target`、`_save_locator_args` 及 `CommandSpec` 上的 `needs_padding` / `ambiguous_by_name`。
3. **Step 3（独立 PR：引入 4 态枚举 `ToolVisibility`，打通 Tool / Console 可见性与执行面）**：
   - 在 `constants.py` 新增 `ToolVisibility(NONE / TOOL_ONLY / CONSOLE_ONLY / BOTH)` 枚举；
   - Driver 全量注册工具以保证 `enabled` 权限单点计算；
   - 同时在 `export_openai_tools()` 与 `list_enabled_tool_names()`（供 `claudeSdkDriver` 使用）按 `tool_visible` 过滤模型暴露面（支持动态响应 `TOGOSPACE_KEEP_LEGACY_ADMIN_TOOLS` 环境变量）；
   - 在 `execute_tool_call` 中按 `hop == 0`（要求 `tool_visible`）和 `hop == 1`（要求 `console_visible`）校验可见性，在 `console` 的 `help` 与路由中按需过滤 `console_visible`，并更新 `test_console_meta.py` 与 `test_console_d2_execution.py`。

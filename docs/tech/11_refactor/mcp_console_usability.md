# MCP / console 可用性问题清单（边讨论边改）

> 来源：Operator 2026-11-16 02:03 要求「把问题整理成一个文档放到仓库里，边讨论边修改」。
> 记录人：小马哥（PM）。**本文档只记录实际跑出来的现象，不含推测。**

## 0. 本文档怎么用

- 每条问题格式固定：**复现命令 / 现状输出（原文）/ 我怎么理解的 / 期望 / 建议改法 / 代价 / 状态**。
- 状态取值：`待讨论` / `同意改` / `不改` / `已改`。
- 讨论请直接在该条的「讨论」小节往下加，不动正文结论；结论变化时在正文标注「已被 X 取代」。

## 0.1 总原则（Operator 2026-11-16 02:07 / 02:12 两次定调）

> **① 报错的 message 应该是「一行可以直接复制去执行的正确调用」，而不是「可用选项清单」。**
> **② 这行示例用固定文案（占位符 `<名称>` / `<工具名>` / `<键>`），不做动态拼装。**

理由 ①：给词汇表（`--target | --action | --tool | --args | …`）只是把可选项摆出来，使用者（人或模型）还得自己重新拼一遍，**拼错一次就多一轮往返**；给一行拼好的命令，复制即可。

理由 ②（Operator 02:12）：
- 动态拼装要处理「值是否存在、是否 inline、引号怎么写」等一堆形态，**复杂且脆**；
- 更实际的是**列表里不一定有配置** —— 没有任何 server 时，就拼不出真实名称，动态方案必须再加一层分支；
- 固定文案**零状态依赖、可测、可一次写完**，用户自己把 `<名称>` 换成手边的值，比机器猜更准。

例外：**只有「值零成本已在手、且不引入任何分支」时**才允许拼实值（例：`mcp add` 的报错可以用用户刚写的 `--name` 值）。有分支就退回固定文案。

## 1. 环境与复现前提

| 项 | 值 |
|---|---|
| 仓库 | `0.5_dev` @ `3e37adb2`（含 #273/#277/#278/#279） |
| 后端 | `src/backend_main.py` 运行中，监听 **8180** |
| 注册表 | 1 条：`name=context7`、`kind=mcp`、`target=https://mcp.context7.com/mcp`、`enable=1`、`config={}`（空，无需鉴权） |
| 执行入口 | **console 工具**（`mcp ls` / `mcp call`），非直连 HTTP |

**已验证可用的最终形态**（对照基准）：

```
mcp ls                                    # 注册表条目
mcp ls context7                           # 该 server 的工具清单（2 个）
mcp call context7 --tool resolve-library-id --args '{"library-name":"React","query":"react hooks"}'
```

---

## 2. 问题清单

### P1【高】`unknown_option` 的「可用选项」列表里混入位置参数 `--target`，主动把人带错

**复现**：
```
mcp call context7 resolve-library-id --library-name React --query "react hooks state management"
```
**现状输出（原文）**：
```
{"error_code": "unknown_option",
 "message": "未知选项：--library-name。可用选项：--target | --action | --tool | --args | --kind | --extra"}
```
**我怎么理解的**：① 以为 MCP 工具自己的参数（`library-name`）可以直接当选项写；② 看到「可用选项」里**有 `--target`**，判断 target 应该写成 `--target context7` —— 下一条我就是这么写的（直接导致 P2）。

**期望（固定文案，按 §0.1）**：
```
未知选项：--library-name。MCP 工具的参数请放进 --args。正确调用：
  mcp call <名称> --tool <工具名> --args '{"键":"值"}'
```

> **这段示例与 P2 共用同一个常量**（Operator 2026-11-16 02:13 决定）：只在前面「哪一句说明」上按情况替换（未知选项名 / `--target`），示例行只有一处实现 —— 避免两份文案各自漂移。

**建议改法（只做两件，都不解析用户输入）**：
1. `unknown_option` 的 message 换成上面这段固定文案（去掉「可用选项」清单）；
2. 生成该清单时**排除位置参数**（`display_options` 取的是命令定义里所有参数名，未排除 `target`）—— 即使清单不再展示，其他命令的同类提示也受益。

**不做（Operator 02:10 结论）**：不实现「把用户已写的 `--library-name React` 自动搬进 `--args` JSON」的通用拼装 —— 收益小、形态多、易脆。

**代价**：文案 ~5 行 + 清单过滤 1 行 + 2 条测试；**状态：待讨论**

**讨论**：

---

### P2【高】写了 `--target` 时报 `missing_target`，把「位置错」说成「缺少」

**复现**：
```
mcp call --target context7 --tool resolve-library-id --args '{"library-name":"React","query":"react hooks state management"}'
```
**现状输出（原文）**：
```
{"error_code": "missing_target",
 "message": "`mcp call` 缺少位置参数 target。",
 "usage": ["用法：mcp <ls|call> <target> [--kind openapi|mcp|mcp_stdio|graphql]，..."]}
```
**我怎么理解的**：直到这句 usage，我才第一次看到正确形态是 `mcp call <名称>`。它**与 P1 的「可用选项：--target」正面矛盾** —— 我是照 P1 的指引写的，却被告知「缺少」。

**期望（**Operator 2026-11-16 02:13 决定：直接复用 P1 的提示**）**：P1 与 P2 **共用同一段 message 与同一示例常量**，不再单独维护第二份文案：

```
未知选项：--target。MCP 工具的参数请放进 --args。正确调用：
  mcp call <名称> --tool <工具名> --args '{"键":"值"}'
```

**为什么可以复用**：对 `mcp call` 而言，`--target` 确实属于「不被接受的选项」，性质与 P1 完全相同；而示例里 `mcp call <名称>` 本身已经示范了「target 要写在命令名后面」，不需要第二句话再解释一遍。

仍然保留的错误区分（码值见 P6）：
- 完全没写 target ⇒ `missing_target`（「缺少」）；
- 写了 `--target` ⇒ **复用上面这段**（不再报「缺少」，也不再说「位置参数」——示例已经把正确形态摆出来了）。

**建议改法**：解析阶段识别「`--target` 被当作选项传入」这一形态（一条特例判断），给上面这段固定文案。

**代价**：一条 if + 文案 + 2 条测试；**状态：待讨论**

**讨论**：

---

### P3【中】`mcp ls` 无参时不给「下一步」

**复现**：`mcp ls`
**现状输出**：只有注册表条目（`name/kind/target/enable/has_config/config_masked`），**无任何提示**。
**我怎么理解的**：以为 `mcp ls` 就是「列出工具」，没看到工具才去猜要加名字。

**期望（固定文案，一段覆盖「有/无配置」两种情况 —— 这正是 §0.1 理由②要解决的）**：
```
mcp ls 列出已配置的 MCP server。看某个 server 的工具清单：mcp ls <名称>。
还没有 server 时先登记，例如：
  mcp add --name ctx7 --kind mcp --target https://mcp.context7.com/mcp
```

**建议改法**：无参分支返回体加 `hint` 字段（已有 `empty_target_hint` 先例，可复用命名族），内容为上面这段**固定文案**。

**代价**：一段文案 + 1 条测试；**状态：待讨论**

**讨论**：

---

### P4【中】`mcp add` 与 `mcp ls` / `mcp call` 的默认 `--kind` 不同（`mcp` vs `openapi`）

> **2026-11-16 02:45 订正**：本节原文写的是「`mcp ls` 默认 `mcp`、`mcp call` 默认 `openapi`」，**这是一处事实错误**——我把 `mcp add` 的默认当成了 `mcp ls` 的默认。
> 代码事实（`0.5_dev` @ `f16f0bf9`）：`save_mcp_server`（`mcp add`）`kind="mcp"`（`tools.py:252`）、`mcp_call` `kind="openapi"`（`tools.py:125`，另有 `_MCP_CALL_DEFAULT_KIND` 常量）、`list_mcp_servers`（`mcp ls`）`kind="openapi"`（`tools.py:346`），且 `mcp ls` 有参分支原样透传给 `mcp_call`，console 层没给 `mcp ls` 另设默认。
> 实测（二部开发，基线 `f16f0bf9`）：`mcp ls https://mcp.context7.com/mcp` ⇒ `mcp2cli_error`（405 Method Not Allowed），`mcp call https://mcp.context7.com/mcp --tool x` ⇒ 同样 405。**两条都默认 openapi**。
> 下面保留原始描述仅作留痕；以订正后的事实为准。

**现状（订正后）**：按**注册表名**调用时 kind 取自注册表（`resolved_from_registry: true`），不会踩；**直接传 URL 时** `mcp ls <URL>` 与 `mcp call <URL>` **都按 openapi 解析**并失败（405），必须显式 `--kind mcp`。`help` 里「kind 默认 mcp」那句是 `mcp add` 的口径（`consoleTool.py:180`），不是 `mcp ls` 的。

**我怎么理解的**：`help` 里「两者有意不同」的说明埋在参数说明深处，且容易被读成 `mcp ls` 默认 mcp；报错时也不会提示「你可能缺 `--kind mcp`」。

**期望（固定文案，按 §0.1）**：直接传 URL 且解析失败时：
```
若目标是 MCP server，请加 --kind mcp：
  mcp ls <URL> --kind mcp
  mcp call <URL> --kind mcp --tool <工具名> --args '{"键":"值"}'
```
并且 `--help` 的「快速上手」把 kind 默认口径**写成事实句**提到显眼位置：

```
mcp：kind 默认 openapi（mcp ls / mcp call 相同）；目标是 MCP server 时请加 --kind mcp。
```

（原期望写的是「`mcp ls` 默认 `mcp`、`mcp call` 默认 `openapi`」，随上文订正一并改；`mcp add` 的 `kind` 默认仍是 `mcp`，属另一条命令。）

**代价**：一句错误提示 + 文档；**状态：待讨论**

**讨论**：

---

### P5【低】`mcp ls <name>` 之后，怎么拼 `mcp call` 没有说明

**现状**：`mcp ls context7` 每项有 `name`、`toolName`（值相同）和完整 `parameters`，但**没有任何一行告诉你下一步怎么调**；`--tool` 取哪个字段、`--args` 键名从哪抄，都要靠猜。

**期望（固定文案，按 §0.1）**：`mcp ls <名称>` 的提示里给一行固定模板：
```
调用工具：mcp call <名称> --tool <工具名> --args '{"参数名":"值"}'   （参数名见各工具的 parameters）
```

**建议改法**：固定文案；`--tool` 明确取 `name` 字段。**不做**「按 parameters 逐工具生成 args 骨架」（那是动态拼装，按 §0.1 放弃）。

**代价**：一段文案；**状态：待讨论**

**讨论**：

---

### P6【待定】错误码是否改名：`missing_target` → `missing_path_arg`

**Operator 2026-11-16 01:59 提议**：`missing_target` 改成 `missing_path_arg` 是否更好。

**我的结论：不建议改。** 依据（可指认）：

1. **它不是 mcp 专用码，是 console 通用码** —— 至少三处在用：`mcp call` 缺 target、`list_tasks` 三个过滤条件都不给（`taskService.py`）、console 通用位置参数校验（`consoleTool.py` 两处）。
2. **测试里有 20+ 处断言 `error_code == "missing_target"`**。改名 = 跨 3 个模块 + 20 多个断言，而目标只是 mcp 那一处。
3. 前端不按具体码分支（`client.ts` 只取字符串展示）⇒ 无下游收益。
4. **`path_arg` 有歧义**：`path` 在 git/文件系统语境是「路径」，这里指**位置参数**；真要改应叫 `missing_positional_arg`。
5. **真正卡住人的是 P1 的误导文案，不是码名。**

**代价**：改名 ~1 小时（含测试），收益≈0；**状态：待讨论**

**讨论**：

---

### P7（新，根因级）`target` 在 `mcp add` 是**选项**，在 `mcp call` / `mcp ls` 是**位置参数**

**事实（两条 help 原文对照）**：
- `mcp add --help`：`--target <string>（可选） 完整连接描述…` —— **带 `--` 的选项**
- `mcp call --help`：`--target <string>（位置参数：值必须写在命令名后面）` —— **同一名字，位置参数**
- 我在 `mcp ls` 上用的是位置参数（`mcp ls context7`），在 `mcp add` 上必须用 `--target`（`mcp add --name x --target ...`）

**为什么这是根因级**：同一个词 `target` 在同一族命令里有两种写法，而 P1 的「可用选项」清单又没有区分 ⇒ 使用者必然把 `mcp add` 的肌肉记忆带到 `mcp call` 上，然后吃到 `missing_target`。

**期望（任选其一，需 Operator 定）**：
- **A（最小）**：不改语法，只在 `mcp call/ls` 的 help 与报错里**显式写一句**「注意：`mcp add` 用 `--target`，本命令用位置参数」；
- **B（统一）**：让 `mcp call/ls` 也接受 `--target`（作为位置参数的别名），报错只在两者都没给时才出现；
- **C（彻底）**：全族统一成一种形态（改 `mcp add` 或改 `mcp call/ls`），有一次破坏性变更。

**我的建议：A 先做**（零破坏），B 可选（对 Agent 友好，但要防「两个都给」的歧义），C 不建议（收益不足以抵一次破坏性变更）。

**状态：待讨论（需 Operator 选 A/B/C）**

**讨论**：

---

### P8（新，实测发现）`--extra` 透传 `--session-*` 控制类开关时，**操作成功但被判失败**

**背景**：`mcp2cli` 支持持久会话（`--session-start NAME` / `--session NAME` / `--session-list` / `--session-stop NAME`，daemon 走 Unix domain socket），我们的 console 通过 `--extra` 能透传这些开关（返回体 `argv` 可见），**功能本身是通的**。

**实测（原文）**：

| 操作 | `--extra` | 实际发生了什么（`raw_excerpt`） | 返回体判定 |
|---|---|---|---|
| 起会话 | `{"session-start":"ctx7"}` | `Session 'ctx7' started (PID 27570)` | **`success:false`** `mcp2cli_bad_json` |
| 列传列 | `{"session-list":true}` | `ctx7  http  alive  PID=27570` | **`success:false`** `mcp2cli_bad_json` |
| 复用会话 | `{"session":"ctx7"}` | 正常返回库列表（结果有效） | `success:true` ✔ |
| 停会话 | `{"session-stop":"ctx7"}` | `Session 'ctx7' stopped.` | **`success:false`** `mcp2cli_bad_json` |
| 再列 | `{"session-list":true}` | `No active sessions.` | **`success:false`** `mcp2cli_bad_json` |

**根因**：封装固定加 `--json` 并强制解析 JSON；而 `--session-start/stop/list` 输出的是**人类可读文本**，解析失败 ⇒ 报 `mcp2cli_bad_json`（文案自己都说「此分支应为防御性」）。**操作已经成功，却告诉调用方失败。**

**危害**：Agent 看到 `success:false` 会重试 ⇒ 第二次 `session-start` 撞「already running」（mcp2cli exit 1）⇒ 状态更乱、且留下无人管理的 daemon。

**期望**：`--extra` 里出现 `session-start` / `session-stop` / `session-list` 这类**控制类**开关时，走「raw 文本 + `success:true`」分支，不做 JSON 解析（或按「进程退出码」而非「输出可解析」判成败）。

**状态：待讨论**

**讨论**：

---

## 3. 待 Operator 决策

- [x] **D1** P1：`unknown_option` 改固定示例 + 清单剔除位置参数 —— 已批准，进 #281
- [x] **D2** P2：`--target` 形态错给专属文案（固定示例） —— 已批准，进 #281
- [x] **D3** P3：`mcp ls` 无参 hint（一段固定文案，覆盖有/无配置） —— 已批准，进 #281
- [x] **D4** P4：`mcp ls` / `mcp call` 直接传 URL 失败时提示 `--kind mcp`；help 按实测写事实句（两条都默认 openapi） —— 已批准，进 #281
- [x] **D5** P5：`mcp ls <名称>` 给一行固定 `mcp call` 模板 —— 已批准，进 #281
- [ ] **D6** P6：错误码维持 `missing_target` 不变
- [ ] **D7** P7：`target` 双形态选 A（只加说明）/ B（接受别名）/ C（统一改造）
- [ ] **D8** P8：`--session-*` 控制类子命令改走「raw 文本 + success」，不判 JSON

## 4. 附：原始执行记录（按发生顺序，原文）

| # | 命令 | 结果 |
|---|---|---|
| 1 | `mcp ls` | 成功：1 条注册表记录 |
| 2 | `mcp ls context7` | 成功：`resolved_from_registry=true`，工具 `resolve-library-id` / `query-docs` |
| 3 | `mcp call context7 resolve-library-id --library-name React --query "..."` | **失败** `unknown_option`（见 P1） |
| 4 | `mcp call --target context7 --tool resolve-library-id --args '{...}'` | **失败** `missing_target`（见 P2） |
| 5 | `mcp call context7 --tool resolve-library-id --args '{"library-name":"React","query":"react hooks state management"}'` | **成功**：返回 `/reactjs/react.dev`（Code Snippets 5029）、`/react/react`（5514）等 5 条真实库结果，`isError=false` |

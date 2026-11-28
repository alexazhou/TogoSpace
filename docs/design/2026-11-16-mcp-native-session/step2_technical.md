# mcporter 风格 console CLI 与 Agent session — 技术文档

> 日期：2026-11-16  
> 状态：技术方案已定稿；待实施。  
> 产品文档：[step1_product.md](./step1_product.md)  
> 开发任务：[step3_tasks.md](./step3_tasks.md)  
> 历史资料：[MCP 设计与实现核对](../2026-11-08-agent-mcp-support/step2_technical.md)、[console 可用性记录](../../tech/11_refactor/mcp_console_usability.md)

本文维护实现架构、模块边界、连接生命周期与错误处理方案。CLI 语法、功能范围、session 行为及产品验收以 [产品文档](./step1_product.md) 为准。

---

## 1. 运行环境与依赖选型

1. **MCP 协议客户端**：直接使用后端 `.venv` 已集成的 **Python `mcp` SDK**（`mcp.ClientSession` + `stdio_client` / `sse_client` / `streamablehttp_client`），在后端现有 `asyncio` 事件循环中运行，不增加独立 daemon 进程。
2. **CLI 解析选型**：**不引入 TypeScript/Node mcporter runtime**（避免桌面打包体积膨胀与跨语言进程管理），采用纯 Python 解析器实现 [产品文档 §2](./step1_product.md#2-cli-命令与示例) 与 [§4.1](./step1_product.md#41-产品契约细则选项参数转换与配置联动) 定义的命令语法。
3. **OpenAPI / GraphQL 兼容**：已有 OpenAPI / GraphQL 外部调用保留现有 `mcpTool.py`（`mcp2cli`）路径，不纳入本次 `mcpSessionService` 范围。
4. **打包产物可用性**：本服务只用 Python `mcp` SDK（`mcp==1.27.1`，`requirements.txt:21`），**不拉 daemon、不读 stdin、不依赖 `sys.executable` 子进程**，因此 PyInstaller onedir 产物（没有 Python 解释器）里同样可用 —— 这正是 `mcpTool._SESSION_EXTRA_REASON` 里「打包产物没有 Python 解释器」那条限制的替代路径。

---

## 2. 模块设计与四层架构

严格遵守 `controller -> service -> dal -> model -> util` 依赖方向：

| 层 / 模块 | 职责与改动 | 复用现有能力 |
|---|---|---|
| `src/service/mcpRegistryService.py` | 全局 server 注册表读写、校验（`VALID_PROTOCOLS = ("stdio", "sse", "streamable_http")`）与配置变更广播/查询 | 复用 `resolve_name`、`list_servers`、`save_server`、`delete_server`、`set_enabled` |
| `src/service/mcpSessionService.py`（新增） | 有状态会话管理服务：维护 `(team_id, agent_id) -> AgentMcpSession`，管理长连接、并发锁、配置版本校验、初始化/关闭清理 | 复用 `mcpRegistryService` 配置数据与 `mcp` SDK |
| `src/service/funcToolService/consoleCommands.py` & `consoleTool.py` | `console` 中 `mcp` 子命令路由、参数解析（`--args` + `key=value` 按工具 `inputSchema` 转换）、结果格式化与错误文案生成 | 保留 `ToolCallContext` 权限校验与可信身份提取 |
| `src/service/agentService/` & `src/service/teamService/` & `src/backend_main.py` | 生命周期清理钩子：Agent 卸载/重载、Team 停止/重载、后端退出时按归属关闭 session | 复用现有启停与卸载流程 |
| `src/util/`（或共享脱敏 helper） | 凭证出口脱敏与 20,000 字符输出截断 | 复用/抽离 `mcpTool._mask_secret_values` 与 `mask_config_for_display` |

---

## 3. 身份归属、连接标识与配置快照

### 3.1 会话与连接归属

- **会话键**：`SessionKey = tuple[int, int]`，即 `(team_id, agent_id)`，严格从可信 `ToolCallContext` 获取。CLI 入参无法覆盖调用者身份；缺少有效身份时直接拒绝，不回退为全局 session。
- **连接键**：单个 `AgentMcpSession` 内以规范化 `ServerKey` 索引连接：
  - 已注册服务：`("registered", server_name)`
  - 临时 HTTP/SSE URL：`("adhoc_url", normalized_url)`
  - 临时 stdio 命令：`("adhoc_stdio", command_str)`
- **隔离保证**：不同 Agent 即使访问同一 `<server-name>` 或同一 URL，各自持有独立的 `ManagedMcpConnection` 与底层 transport（stdio 子进程 / HTTP 客户端），绝不跨 Agent 复用连接。

### 3.2 配置快照与失效联动

每个已注册连接的 `ManagedMcpConnection` 在建立时记录其配置快照（`target`、`protocol`、`config`、`enabled` 等关键字段签名）：
- **懒校验**：每次从活动 session 获取已有连接前，先通过 `mcpRegistryService.resolve_name(server_name)` 检查当前注册状态：
  - 若服务已不存在或 `enable == False`：立即关闭并移除该连接，按“未找到”或“已禁用”报错；
  - 若配置签名与快照不一致（地址、协议或凭证已更新）：先关闭旧连接，再按最新配置重建新连接。
- **主动失效（可选增强）**：`mcpRegistryService.save_server` / `delete_server` / `set_enabled` 变更配置后，通知 `mcpSessionService.invalidate_server(server_name)` 主动清理所有活动 session 中对应连接，确保已禁用/删除的 stdio 子进程及时退出。

---

## 4. SDK 上下文持有、并发与取消

### 4.1 `AsyncExitStack` 与专用连接任务（避免 anyio 跨 Task 报错）

Python `mcp` SDK 的 `stdio_client` / `sse_client` / `streamablehttp_client` 与 `ClientSession` 均为基于 `anyio` task group 的 async context manager，**要求 `__aenter__` 与 `__aexit__` 必须在同一个 asyncio Task 中执行**。

因此每个 `ManagedMcpConnection` 采用**独立生命周期 Task（owner task）**持有 `contextlib.AsyncExitStack`：
1. 建立连接时启动一个 `owner_task`：在任务内 `async with AsyncExitStack() as stack:` 依次进入 transport 与 `ClientSession`，完成 `await session.initialize()` 后触发 `ready_event`；
2. 随后 `owner_task` 等待 `close_event.wait()` 挂起保活；
3. 关闭连接时设置 `close_event`，等待 `owner_task` 自然退出 `AsyncExitStack`（保证在同一个 task 内完成 transport 清理与 stdio 子进程回收）；若超时则取消 `owner_task` 并等待收敛。

### 4.2 并发控制与初始化中取消

- **连接级锁**：每个 `ServerKey` 配一把 `asyncio.Lock`（或 session 内按 key 映射的建连锁），防止同一 Agent 并发两次 `mcp call` 对同一 server 重复建连。
- **状态机**：`ManagedMcpConnection` 维护状态 `CONNECTING -> READY -> CLOSING -> CLOSED`：
  - 无活动 session 时：每次 `list / call / resource` 创建临时连接，操作完成即在 `finally` 中触发关闭并等待回收；
  - 有活动 session 时：`session start` 仅将 `active = True`（不预建连接）；后续首次访问某 server 时建连并加入 `session.connections`；
  - `session stop` 或归属清理时：对处于 `CONNECTING` 或 `READY` 的全部连接并发触发关闭（取消尚未就绪的初始化任务并等待 `AsyncExitStack` 退出），确保无残留子进程。

---

## 5. 生命周期接入点

| 触发事件 | 处理动作 | 不触发的边界 |
|---|---|---|
| `mcp session start` | 创建或保持当前 Agent 的活动 session（幂等，不重置已有连接） | — |
| `mcp session status` | 返回当前 Agent session 是否开启、已有连接列表及状态（脱敏后） | — |
| `mcp session stop` | 关闭当前 Agent session 内全部连接（含初始化中连接）并置为未开启（幂等） | — |
| Agent 卸载 / 重载 / 离职 | 调用 `mcpSessionService.close_agent_session(team_id, agent_id)` | Turn 结束、跨房间、正常休眠**不关闭** |
| Team 停止 / 重载 | 调用 `mcpSessionService.close_team_sessions(team_id)` | — |
| 后端进程退出 | 应用 shutdown 钩子调用 `mcpSessionService.shutdown()` 关闭全部 session | 后端重启后不自动恢复旧 session |

---

## 6. 宿主接口、输出与授权适配

1. **进程安全**：所有 CLI 解析与协议执行均在 `console` 工具调用内完成，不读取后端 `stdin`，不执行 shell 管道/重定向，捕获任何可能触发进程退出的异常并转为结构化工具结果。
2. **输出规范**：
   - 沿用统一 20,000 字符输出上限与截断标识；
   - 返回体、错误消息与日志均在出口执行凭证脱敏（替换 headers/env 中的敏感值为 `***`）；
   - 参数解析与选项错误遵循固定占位符示例格式（[产品文档 §4.1](./step1_product.md#41-产品契约细则选项参数转换与配置联动)）。
3. **超时与人工授权**：
   - 工具调用超时或被取消时，不自动重放结果未知的业务调用；
   - 需要人工交互授权的远端服务不阻塞等待终端输入，返回明确错误提示引导用户在配置中补充凭证或授权信息。

---

## 7. 验证与迁移说明

详细开发步骤、历史代码清理与分层测试要求见 [开发任务表](./step3_tasks.md)。

---

## 8. console 子树实现备注（#359 搬迁）

> 本节是 `src/service/funcToolService/consoleMcp.py` 模块 docstring 的原文搬迁（2026-11-25，#359 压缩注释：35 行压到 5 行）。`consoleSchema.py` 的说明已并入 [console_tool_review.md](../../tech/11_refactor/console_tool_review.md)（该文已覆盖派生层，按「尽量并入既有文档」不新建）。
> 命令的写法与产品上的定义仍以 [step1_product.md](./step1_product.md) §2 / §4.1 为准，本节只记录「为什么这么实现」。

### 8.1 为什么是「局部专用解析器」而不扩通用 console 框架

通用 `consoleCommands` 的 spec 机制假定「一条命令 = 资源 + 动词 + 至多一个位置参数，选项面从某个既有工具签名派生」。本子树需要三层子命令（`mcp config list`）、双位置参数（`config add <name> <target>`、`resource <server> <uri>`）、`name=value` 具名参数与 `--output` 这类纯展示选项 —— 这些语义**只属于 mcp**。把它们塞进通用框架会让 34 条既有命令的解析路径一起变（风险与收益不成比例），所以这里自带一个窄解析器，`consoleTool.console()` 只在 `tokens[0] == "mcp"` 时短路进来。

### 8.2 两条入口的分工（Q1=A，产品 §5「OpenAPI / GraphQL 能力保留，CLI 另行明确」）

- `mcp list` / `mcp call <server>.<tool>` / `mcp resource` / `mcp session` ⇒ **MCP 原生路径**（Python `mcp` SDK，`mcpSessionService`）。
- `mcp ls <path/URL>` / `mcp call --kind openapi|graphql …` ⇒ **既有 mcp2cli 路径**，原样委托 `tools.list_mcp_servers` / `tools.mcp_call`，行为与文案不变（它是 A 类不变量）。mcp2cli 侧的取舍见 [2026-11-08-agent-mcp-support/step2_technical.md §9](../2026-11-08-agent-mcp-support/step2_technical.md)。

两条靠**命令词不同**（`list` vs `ls`）区分，不靠猜参数，所以无二义；`help` 里各给一条可复制示例，避免「能力保住了但用户不知道怎么用」。

### 8.3 报错信息怎么写（产品 §4.1 第 3 条）

语法错误 / 未知选项 / 缺目标 / 缺参数一律：固定错误码 + 一句话原因 + **一行可直接复制的固定占位符示例**，不动态拼装注册表实值。

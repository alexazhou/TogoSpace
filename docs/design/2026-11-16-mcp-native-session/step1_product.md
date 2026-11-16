# mcporter 风格 console CLI 与 Agent session — 产品文档

> 日期：2026-11-16  
> 状态：产品契约已补齐；待实施。  
> 技术文档：[step2_technical.md](./step2_technical.md)  
> 开发任务：[step3_tasks.md](./step3_tasks.md)

本文维护产品目标、功能范围、CLI 语法和用户可见行为。实现方式由技术文档说明，开发步骤和验证用例由任务表记录。

## 1. 目标与范围

console 的 MCP 命令参考 mcporter 的 `list / call / config / resource` 语法，入口词使用 `mcp`。服务通过配置中的名称选择，工具通过 `<server-name>.<tool-name>` 定位。已有 server 注册表、设置页和配置管理能力继续使用。

增加无名称的 `session start / status / stop`，让 Agent 在一段持续工作中访问多个 MCP server，并将连接保留到统一关闭。每个 Agent 同一时刻只有一个活动 session，开启时不指定 server，也不提前建立连接；之后访问的 server 自动加入当前 session。

session 开启前后使用相同的调用语法，用户无需为每个 server 分别创建会话或修改 keepalive 配置。session 与连接按 Agent 隔离，全局服务配置可以共享。文档示例是目标语法，功能尚未实施。

本次范围为常规 MCP CLI 和 session 使用行为。范围与兼容约定集中见第 4 节。

## 2. CLI 命令与示例

### 2.1 占位符

| 占位符 | 含义 |
|---|---|
| `<server-name>` | 已配置的 MCP 服务名称，来自项目 server 注册表 |
| `<another-server-name>` | 另一个已配置的服务名称，用于展示同一 session 访问多个服务 |
| `<server-url>` | HTTP / SSE MCP 服务的实际 URL |
| `<server-command>` | stdio MCP server 的完整启动命令，整体加引号 |
| `<tool-name>` | 对应服务的工具名称，来自 `mcp list <server-name>` |
| `<args-json>` | 工具的 JSON 参数对象，键名使用工具 schema 中的参数名 |
| `<parameter-name>` / `<parameter-value>` | 工具参数的名称及值 |
| `<resource-uri>` | 对应服务的 MCP resource URI |

替换占位符后再执行，尖括号本身不是参数值。常规 MCP 命令对应 mcporter 语法，只把入口词替换为 console 的 `mcp`；session 子命令是本项目扩展。

### 2.2 服务和工具发现

```text
# 查看已配置的服务及其状态
mcp list

# 查看一个服务的工具或单个工具的 schema
mcp list <server-name>
mcp list <server-name>.<tool-name> --schema

# 查看简要工具签名或全部参数
mcp list <server-name> --brief
mcp list <server-name> --all-parameters

# 临时查看未注册的 HTTP 或 stdio 服务
mcp list <server-url> --brief
mcp list --stdio '<server-command>'
```

服务名称选择已配置的服务，URL 选择临时来源。工具签名、参数说明和调用示例参考 mcporter，参数详细程度由其原生选项控制。开启 session 后，`mcp list` 实际检查到的每个 server 也遵循第 3.3 节的 keepalive 规则；只查看已保存配置时使用 `mcp config list`。

### 2.3 工具调用

```text
# 使用 JSON 参数对象
mcp call <server-name>.<tool-name> --args '<args-json>'

# 使用具名参数
mcp call <server-name>.<tool-name> <parameter-name>=<parameter-value>

# 选择输出格式
mcp call <server-name>.<tool-name> --args '<args-json>' --output json
```

`--args`、`key=value`、工具参数选项和函数调用表达式的规则参考 mcporter；主示例使用 `--args` 和 `key=value`。参数合并、类型转换及错误行为遵循第 4.1 节产品契约，实现和帮助应遵循该契约。

### 2.4 配置管理

```text
mcp config list
mcp config get <server-name>
mcp config add <server-name> <server-url>
mcp config remove <server-name>
```

`list` 用于发现服务和工具，`config` 用于管理保存的服务定义。CLI 与设置页管理同一份已保存配置，配置项对所有团队可用。配置管理不创建当前 Agent 的 session。

### 2.5 Resources 与帮助

Resources 是 MCP server 发布的可读取资料，例如说明文档、文件、配置或日志。第一条命令列出资源及其 URI，第二条按返回的 URI 读取内容；工具清单使用 `list`，执行工具使用 `call`。该能力以 server 提供 Resources 为前提。

```text
# 列出资源或读取指定资源
mcp resource <server-name>
mcp resource <server-name> <resource-uri>

# 查看帮助
mcp --help
mcp call --help
mcp session --help
```

## 3. 无名称、跨 server 的 session

### 3.1 三条命令

```text
mcp session start
mcp session status
mcp session stop
```

三条命令均不接受 session 名称或 server 选择参数，作用范围始终是当前 Agent。

| 操作 | 行为 |
|---|---|
| `session start` | 开启当前 Agent 的 session；已经开启则保持原 session 和已有连接，不重置状态；未访问 server 时连接数为零 |
| `session status` | 返回是否开启，以及当前 session 中保留的 server 连接和状态；未开启时正常返回未开启状态 |
| `session stop` | 关闭 session 中的全部连接并结束 session；尚未开启则直接成功；后续操作恢复一次性连接 |

没有 session 名称、会话选择开关或多会话列表。`status` 就是当前 Agent 的 session 状态入口，不返回其他 Agent 的连接。

### 3.2 完整使用过程

```text
# 开始工作：只开启 session，不指定 server
mcp session start

# 查看第一个服务的工具，建立并保留连接
mcp list <server-name>

# 调用第一个服务，复用连接
mcp call <server-name>.<tool-name> --args '<args-json>'

# 访问另一个服务，连接也加入当前 session
mcp call <another-server-name>.<tool-name> --args '<args-json>'

# 再次访问第一个服务，继续复用已有连接
mcp call <server-name>.<tool-name> --args '<args-json>'

# 查看本次工作保留了哪些连接
mcp session status

# 结束工作：统一关闭全部连接
mcp session stop
```

session 表示当前 Agent 的一段持续工作，可以同时访问多个 server。每次命令仍显式选择 server，工作结束时通过 stop 统一结束这些连接。

### 3.3 Keepalive 与隔离规则

| 场景 | 行为 |
|---|---|
| 没有活动 session | MCP 操作使用临时连接，操作完成后关闭 |
| session 内首次访问某个 server | 按需建立连接并加入当前 session |
| session 内再次访问同一 server | 复用当前 session 内已有连接 |
| `list / call / resource` 访问 server | 均遵循当前 session 的 keepalive 规则，临时来源同样适用 |
| `config` 管理服务定义 | 管理配置，不因该操作建立 MCP 连接 |
| turn 结束、跨房间或正常休眠 | session 和已有连接保留，不因闲置自动关闭 |
| 当前 Agent 执行 `session stop` | 关闭当前 session 中全部连接，结束本次工作对这些服务的持续使用 |
| 其他 Agent 开启、调用或关闭 session | 操作属于各自 Agent，互不影响 |

不同 Agent 即使访问同一 server，也各自拥有独立的 session 和连接。Agent 只能查看和关闭自己的 session。远端服务本身的账号级共享状态不由客户端 session 改变。

正常使用时 keepalive 持续到显式关闭。Agent 卸载 / 离职 / 重载后，其 session 结束；Team 停止 / 重载后，所属 Agent 的 session 结束；应用退出时全部 session 结束。应用重启后 session 为未开启状态。

## 4. 功能范围与兼容约定

| 项目 | 产品约定 |
|---|---|
| mcporter 语法参考 | 常规命令参考 list / call / config / resource，console 入口使用 `mcp`；完整选项细则在本文补齐 |
| session 扩展 | 无名称的 session start / status / stop 是本项目扩展，不宣称是 mcporter 原生命令 |
| 服务配置 | 继续使用已有服务配置和设置页，不引入另一套 bake 配置概念 |
| 旧 console 语法 | 迁移为本文定义的命令，旧 mcp ls 和旧 target + --tool / --args 包装不作为并行入口保留 |
| 旧 session 语法 | 不保留 mcp2cli 的 session 开关、具名会话及调用时选择会话的用法 |
| 上游其他功能 | daemon 控制、录制回放、生成独立 CLI 和编辑器配置自动导入不自动纳入本次范围 |
| 已有 OpenAPI / GraphQL 能力 | 能力保留，CLI 另行明确，不进入本设计的 MCP session |
| prompts / completion | 命令尚未确认，不从旧方案直接沿用 |

mcporter 的 record / replay 中的名称标识流量录制，与本设计的活动 session 不同。迁移命令后，已保存的服务配置继续可用。

### 4.1 产品契约细则（选项、参数转换与配置联动）

1. **常规命令选项与输出格式**：
   - 通用输出选项：`--output json|text`（默认 `text` 或按工具约定输出结构化文本；`--output json` 输出完整 JSON）。
   - `mcp list` 支持：`--brief`（简要工具签名）、`--all-parameters`（展示全部参数）、`--schema`（与 `<server-name>.<tool-name>` 联用输出完整 JSON Schema）、`--stdio '<server-command>'`（临时 stdio 来源）。
   - `mcp config add` 支持：`--protocol stdio|sse|streamable_http`（可选，HTTP/SSE 默认按 URL 探测或 `streamable_http`，stdio 命令则对应 `stdio`）。
2. **参数合并与类型转换（`mcp call`）**：
   - `--args '<args-json>'` 必须解析为 JSON 对象（非对象直接报错）。
   - 具名参数 `key=value` 可单独使用，也可与 `--args` 混用；同一参数同时出现在 `--args` 与 `key=value` 时，以显式 `key=value` 覆盖（或当 `--args` 非法时直接报错）。
   - 类型转换依据目标工具的 `inputSchema`：`integer` / `number` / `boolean` / `array` / `object` 优先按 JSON 值解析，失败时给出明确参数类型错误；`string` 类型保留原始字符串文本，不做隐式数字/布尔转换。
3. **错误反馈规范**：
   - 语法错误、未知选项或缺少目标时，返回明确的错误码与**一行可直接复制执行的固定占位符示例**（如 `mcp call <server-name>.<tool-name> --args '{"key":"value"}'`），不动态拼装注册表实值。
   - 业务调用超时或结果未知时不自动重放。
4. **配置变更对活动 session 的影响**：
   - 通过 `config` 或设置页禁用（disable）或删除（remove）某个已配置 server 后，活动 session 中该 server 的连接立即失效并关闭；再次访问时按“已禁用”或“未找到”报错。
   - 更新（overwrite）某个 server 的目标地址、协议或凭证后，活动 session 中旧连接失效关闭，下次访问按最新配置重新建立连接。
   - 仅修改其他 server 的配置，不影响当前 session 中无关 server 的已有连接。

参考资料（本次核对的 mcporter main 中 package 版本为 0.17.1，用于语法参考）：

- [CLI 参考](https://github.com/openclaw/mcporter/blob/main/docs/cli-reference.md)
- [调用语法](https://github.com/openclaw/mcporter/blob/main/docs/call-syntax.md)
- [配置管理](https://github.com/openclaw/mcporter/blob/main/docs/config.md)
- [持久连接与 daemon](https://github.com/openclaw/mcporter/blob/main/docs/daemon.md)

## 5. 产品验收标准

- 常规命令参考 mcporter 语法，服务名称、工具名称及参数含义明确；示例统一使用有解释的占位符。
- 开启 session 不需要名字或 server，刚开启时没有连接；调用时无需 session 选择参数。
- 同一 session 可访问多个 server，首次访问建立并保留连接，再次访问复用。
- `list / call / resource` 与临时来源访问遵循相同的 keepalive 规则，`config` 管理服务定义。
- 重复 start 保持已有连接，未开启时 status 正常返回状态，重复 stop 成功。
- stop 关闭全部所属连接；之后的操作恢复一次性连接。
- session 状态和连接只对所属 Agent 可见，其他 Agent 的操作不影响它。
- turn 结束、跨房间、正常休眠和闲置保持 session；卸载、重载或应用退出按范围清理。
- Resources 列表和读取含义在帮助中说明，命令支持范围与实际功能一致。

详细验收用例与执行记录见 [开发任务表](./step3_tasks.md)。

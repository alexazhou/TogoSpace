# Agent 自定义 MCP 服务接入（Model Context Protocol）- 技术设计

> **代码基线**：`/Volumes/PDATA/GitDB/TeamAgent`  
> **关联文档**：[产品文档 (step1_product.md)](./step1_product.md)  
> **设计原则**：严格遵循四层架构（Controller → Service → DAL → Model），进程/运行时按 Team 严格隔离，工具动态绑定至 Agent ToolRegistry。

---

## 1. 架构目标与关键修正

### 1.1 核心设计定位

构建以 **Team 为隔离边界**、**宿主进程直接管控 stdio 子进程生命周期**、**直接向 Agent 实例的 `tool_registry` 动态注入** 的 MCP 运行时接入体系。

### 1.2 纠正此前设计草案（`draft_mcp.md`）中的架构误区

在初始评估中，方案草案曾存在以下两项严重的设计偏差，在本次技术设计中予以彻底修正：

1. **纠正“gtsp 内部运行模型并回传工具”的错误假设**：
   * 实际代码实现中，`gtsp` 仅为外部系统工具提供者（提供 `execute_bash`/`read_file` 等）。真正的 LLM 推理循环位于宿主侧的 [`agentTurnRunner.py`](file:///Volumes/PDATA/GitDB/TeamAgent/src/service/agentService/agentTurnRunner.py#L752)（调用 `llmService.infer_stream`）。
   * 工具调用直接由宿主侧的 `self.tool_registry.execute_tool_call` 分发执行。因此，**MCP 工具与 gtsp 无关，无需经过 gtsp**，由宿主直接向 MCP 进程发起调用。
2. **纠正“将 MCP 工具挂入全局 `funcToolService`”的设计**：
   * [`funcToolService`](file:///Volumes/PDATA/GitDB/TeamAgent/src/service/funcToolService/core.py) 是整个 Python 进程共享的全局单例，且无 `team_id` 参数。系统同时并发运行多个 Team（见 [`backend_main.py:211`](file:///Volumes/PDATA/GitDB/TeamAgent/src/backend_main.py#L211)）。若挂入全局注册表，会导致跨 Team 污染和同名冲突。
   * [`funcToolType.py`](file:///Volumes/PDATA/GitDB/TeamAgent/src/service/funcToolService/funcToolType.py#L223) 通过 Python 函数反射生成 Schema，而 MCP 工具本身已是标准 JSON Schema，无需强行打包为 `FuncTool`。
   * **正确做法**：对齐 TSP 工具模式，MCP 工具由 Team 级 `mcpService` 管理，在 Agent 初始化与环境刷新时，直接将带有 `mcp__{server}__{tool}` 命名的 `OpenAITool` 注册进该 Agent 专属的 [`AgentToolRegistry`](file:///Volumes/PDATA/GitDB/TeamAgent/src/service/agentService/toolRegistry.py#L121)。

---

## 2. 总体架构与模块分层

```mermaid
flowchart TD
    subgraph Controller["Controller 层 (HTTP API)"]
        McpCtrl["mcpController<br>(CRUD / Test / Reload)"]
    end

    subgraph Service["Service 业务层"]
        TeamSvc["teamService<br>(restore_team / stop_team)"]
        McpSvc["mcpService<br>(TeamMcpManager 管理池)"]
        AgentSvc["agentService / Driver<br>(tspDriver / nativeDriver)"]
        TurnRunner["AgentTurnRunner<br>(Host Loop & llmService)"]
        ToolReg["AgentToolRegistry<br>(Agent 级工具注册表)"]
    end

    subgraph DAL_Model["DAL & Model 层"]
        McpDAL["gtMcpServerManager"]
        McpModel["GtMcpServer (SQLite 表 gt_mcp_server)"]
    end

    subgraph Runtime["外部子进程 (Runtime)"]
        MCPProcess["stdio MCP Server 进程组<br>(npx / python3 / binary)"]
    end

    McpCtrl --> McpSvc
    McpCtrl --> McpDAL
    TeamSvc -->|restore / stop 生命周期| McpSvc
    McpSvc --> McpDAL
    McpDAL --> McpModel

    AgentSvc -->|获取 Team 对应 MCP Tools| McpSvc
    AgentSvc -->|注册 MCP 工具与 Handler| ToolReg
    TurnRunner -->|execute_tool_call| ToolReg
    ToolReg -->|Handler 回调| McpSvc
    McpSvc -->|JSON-RPC 2.0 stdio| MCPProcess
```

### 2.1 四层职责边界

| 模块 / 类 | 所在层 | 核心职责 |
|---|---|---|
| `mcpController.py` | `controller` | 负责 MCP 配置的 RESTful HTTP 接口、连通性测试请求编排与权限校验 |
| `mcpService` | `service` | 管理所有 Team 的 `TeamMcpManager`；负责 stdio 进程启动/握手/调用/超时熔断/优雅关闭 |
| `TeamMcpManager` | `service` | 维护单 Team 内部所有启用的 MCP Server 实例与 client 连接；对外输出 `list[OpenAITool]` |
| `gtMcpServerManager.py` | `dal` | 数据访问层，封装 `gt_mcp_server` 表的增删改查与唯一性约束处理 |
| `gtMcpServer.py` | `model` | 数据库 ORM 模型定义 |
| `toolRegistry.py` | `service/agentService` | 扩展权限匹配规则，支持 `Category:Mcp` 与命名空间前缀过滤 |

---

## 3. 数据模型与持久化设计

### 3.1 数据库表：`gt_mcp_server`

通过迁移脚本 `assets/migrate/0021_add_gt_mcp_servers.sql` 创建，对应模型类 `GtMcpServer`（继承自 `DbModelBase`，复用 `AutoTimestampMixin`）：

| 字段名 | 类型 | 约束与默认值 | 说明 |
|---|---|---|---|
| `id` | `INTEGER` | `PRIMARY KEY AUTOINCREMENT` | 自增主键 ID |
| `team_id` | `INTEGER` | `NOT NULL` | 所属 Team ID（外键关联 `gt_team.id`） |
| `name` | `TEXT` | `NOT NULL` | 服务标识（命名空间，如 `github`、`sqlite`） |
| `scope` | `TEXT` | `NOT NULL DEFAULT 'team'` | 作用域：`team`（一期），预留 `agent` |
| `transport` | `TEXT` | `NOT NULL DEFAULT 'stdio'` | 传输方式：`stdio`（一期），预留 `http`, `sse` |
| `command` | `TEXT` | `NOT NULL` | 启动命令（如 `npx`, `python3`, `/bin/server`） |
| `args` | `TEXT` | `NOT NULL DEFAULT '[]'` | 命令行参数（JSON 数组） |
| `env` | `TEXT` | `NOT NULL DEFAULT '{}'` | 环境变量字典（JSON 对象） |
| `url` | `TEXT` | `NULL` | 远端服务 URL（预留） |
| `headers` | `TEXT` | `NOT NULL DEFAULT '{}'` | 请求头字典（预留） |
| `enabled` | `INTEGER` | `NOT NULL DEFAULT 1` | 是否启用：`1` 启用，`0` 停用 |
| `tool_allowlist` | `TEXT` | `NULL` | 工具白名单（JSON 数组，为 NULL 时导出全部工具） |
| `timeout_sec` | `INTEGER` | `NOT NULL DEFAULT 60` | 单次工具调用超时时间（秒） |
| `startup_timeout_sec` | `INTEGER` | `NOT NULL DEFAULT 30` | 启动握手超时时间（秒） |
| `created_at` | `DATETIME` | `NOT NULL DEFAULT (datetime('now'))` | 创建时间 |
| `updated_at` | `DATETIME` | `NOT NULL DEFAULT (datetime('now'))` | 更新时间 |

**索引与唯一键约束**：
* **复合唯一索引**：`UNIQUE(team_id, name)` —— 团队内 Server 名称唯一，不同 Team 允许同名；
* **查询索引**：`idx_gt_mcp_server_team_enabled (team_id, enabled)` —— 加速运行时恢复。

---

## 4. 运行时生命周期与进程治理

### 4.1 Team 恢复时序（与 `teamService.restore_team` 集成）

在 [`teamService.py:restore_team`](file:///Volumes/PDATA/GitDB/TeamAgent/src/service/teamService.py#L37-L62) 中，MCP 服务的启动必须位于 Agent 实例创建之前，确保 Agent Driver 初始化时能同步读取到可用的 MCP 工具列表：

```python
# teamService.py
async def restore_team(team_id: int, ...):
    ...
    # 1. 先启动本团队启用的 MCP Server 进程并完成握手缓存
    await mcpService.start_team_mcp_servers(team.id)

    # 2. 恢复 Agent 与 Driver（此时 Driver 读取 mcpService 工具列表）
    await agentService.load_team_agents(team.id, workspace_root=workspace_root)
    await roomService.load_team_rooms(team.id)
    ...
```

### 4.2 并发启动与启动降级（防止阻塞整队恢复）

stdio 程序（尤其是使用 `npx` 的临时下载环境）冷启动可能耗时较长。为避免单个异常 Server 拖死整支团队，采用**异步并发拉起 + 单服务隔离超时 + 异常降级**：

```python
async def start_team_mcp_servers(self, team_id: int) -> None:
    servers = await gtMcpServerManager.get_enabled_servers_by_team(team_id)
    tasks = [self._start_single_server_safe(team_id, server) for server in servers]
    # 并发拉起，单个失败不抛异常中断
    await asyncio.gather(*tasks, return_exceptions=True)

async def _start_single_server_safe(self, team_id: int, server: GtMcpServer) -> None:
    try:
        await asyncio.wait_for(
            self._start_single_server(team_id, server),
            timeout=server.startup_timeout_sec
        )
    except Exception as e:
        logger.error(f"Team {team_id} MCP Server {server.name} 启动失败，已降级: {e}")
        # 记录内存态为 FAILED，不阻断其他服务
```

### 4.3 进程安全与孤儿进程治理

stdio 子进程必须严格防范孤儿进程滞留（尤其是在热重载或崩溃时）：
1. **独立进程组创建**：
   在启动子进程时，传递 `start_new_session=True`（Unix 系统下相当于 `os.setsid()`），使子进程成为独立进程组的组长。
2. **两段式优雅退出**：
   * **第一阶段**：向进程组发送 `SIGTERM`（`os.killpg(os.getpgid(proc.pid), signal.SIGTERM)`），等待子进程正常清理释放资源（最多等待 3 秒）；
   * **第二阶段**：若超时仍未退出，向进程组强制发送 `SIGKILL` 彻底清除。
3. **关闭钩子与热重载保障**：
   * 在 `teamService.stop_team_runtime(team_id)` 中显式调用 `await mcpService.stop_team_mcp_servers(team_id)`；
   * 后端进程退出钩子中统一对所有存活子进程执行清理。

---

## 5. 桥接层与 Agent 工具集成

### 5.1 命名规范与 Schema 转换

* **命名空间格式**：`mcp__{server_name}__{tool_name}`
  * 例如：Server 命名为 `sqlite`，工具为 `read_query`，则全局工具名为 `mcp__sqlite__read_query`；
  * 正则保证：`server_name` 限制为 `^[a-z0-9_]{1,32}$`，避免命名非法。
* **Schema 转换**：
  MCP 协议标准工具响应结构包含：
  ```json
  {
    "name": "read_query",
    "description": "Execute a SELECT query",
    "inputSchema": {
      "type": "object",
      "properties": { "query": { "type": "string" } },
      "required": ["query"]
    }
  }
  ```
  直接将其转换为系统统一的 `llmApiUtil.OpenAITool`：
  ```python
  OpenAITool(
      function=OpenAIFunction(
          name=f"mcp__{server_name}__{tool['name']}",
          description=tool.get("description", ""),
          parameters=OpenAIFunctionParameter(**tool["inputSchema"])
      ),
      category=ToolCategory.MCP
  )
  ```

### 5.2 挂载到 Agent Driver 与 ToolRegistry

无论使用 `TspAgentDriver` 还是 `NativeAgentDriver`，都在其工具装配环节（`_register_host_tools` 或 `startup`）注入：

```python
# 获取本团队可用的 MCP 工具列表
mcp_tools = mcpService.get_team_mcp_tools(self.host.gt_agent.team_id)

for tool in mcp_tools:
    self.host.tool_registry.register(
        tool,
        handler=self._execute_mcp_tool_dispatch,
    )
```

调用分发处理器 `_execute_mcp_tool_dispatch`：
```python
async def _execute_mcp_tool_dispatch(
    function_args: str,
    context: Optional[ToolCallContext] = None
) -> dict[str, Any]:
    # 从 context.tool_name 解析出 server_name 与 real_tool_name
    # 格式：mcp__{server_name}__{real_tool_name}
    _, server_name, tool_name = context.tool_name.split("__", 2)
    return await mcpService.execute_tool(
        team_id=context.team_id,
        server_name=server_name,
        tool_name=tool_name,
        arguments_json=function_args,
        context=context,
    )
```

### 5.3 权限解析扩展（`toolRegistry.py`）

在 [`toolRegistry.py`](file:///Volumes/PDATA/GitDB/TeamAgent/src/service/agentService/toolRegistry.py) 中做两处扩展：
1. **新增类别枚举**：
   在 `constants.py` 的 `ToolCategory` 中增加 `MCP = "Category:Mcp"`；
2. **支持前缀模式匹配**：
   升级 `AgentToolRegistry.resolve_enabled_tool_names` 方法：
   ```python
   for spec in allow_specs:
       category = ToolCategory.from_spec(spec)
       if category is not None:
           categories.add(category)
           continue
       if spec.endswith("__*") or spec.endswith("/*"):
           # 支持前缀模糊匹配，如 mcp__github__*
           prefix = spec[:-3] if spec.endswith("__*") else spec[:-2]
           for name in ordered_names:
               if name.startswith(prefix):
                   explicit_names.add(name)
           continue
       if spec in ordered_names:
           explicit_names.add(spec)
   ```
3. **默认策略安全防护**：
   `build_runtime_allow_specs` 在 `allowed_tools is None` 时，**不包含 `Category:Mcp`**。未显式赋予 MCP 权限的普通 Agent 不会自动获得执行外部命令的权限。

---

## 6. 数据处理与调用健壮性

### 6.1 超大输出安全截断

MCP 工具（如文件搜索、日志提取、网页抓取）可能返回数十万字符的输出，直接塞给 LLM 会导致上下文溢出（Context Overflow）。
在 `mcpService.execute_tool` 返回层设置阈值保护：
* 单次返回文本限制：最大 16,384 字符（约 4,000 Tokens）；
* 超过部分截断，并追加：`\n\n[Warning: Tool output exceeded limit (16KB) and was truncated by host]`。

### 6.2 多模态返回降级

MCP 标准允许返回 `ImageContent`。一期以纯文本会话为主，当检测到返回内容包含图像数据时，提取其中的文本并附带结构化说明：
`"[Image content returned by tool; binary image rendering is currently unsupported in this turn]"`，避免解析器抛出未捕获异常。

### 6.3 异常与错误码规范

所有 MCP 内部异常统一包装为标准结构字典，支持模型自我纠偏：
```json
{
  "success": false,
  "error_code": "MCP_TOOL_EXECUTION_TIMEOUT",
  "message": "MCP server 'sqlite' timed out after 60s while executing 'read_query'"
}
```

---

## 7. HTTP API 接口设计

所有接口挂载于 `/teams/{team_id}/mcp_servers` 路径下，复用系统统一的鉴权与权限拦截：

| Method | Path | 说明 | 权限 |
|---|---|---|---|
| `GET` | `/teams/{team_id}/mcp_servers.json` | 获取指定团队的所有 MCP Server 配置及实时状态 | 团队成员 |
| `POST` | `/teams/{team_id}/mcp_servers.json` | 新增 MCP Server 配置 | 仅 Admin / Root |
| `PUT` | `/teams/{team_id}/mcp_servers/{server_id}.json` | 修改 MCP Server 配置 | 仅 Admin / Root |
| `DELETE` | `/teams/{team_id}/mcp_servers/{server_id}.json` | 删除 MCP Server | 仅 Admin / Root |
| `POST` | `/teams/{team_id}/mcp_servers/test.json` | 临时测试连接并预览工具列表（传参未持久化的配置） | 仅 Admin / Root |
| `POST` | `/teams/{team_id}/mcp_servers/{server_id}/reload.json` | 单个 MCP Server 运行时重载与工具刷新 | 仅 Admin / Root |

### 连通性测试接口（Test Connection）入参与返回示例

* **Request**: `POST /teams/1/mcp_servers/test.json`
  ```json
  {
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-sqlite", "--db", "/tmp/test.db"],
    "env": {},
    "timeout_sec": 30
  }
  ```
* **Response (Success)**:
  ```json
  {
    "success": true,
    "tools": [
      {
        "name": "read_query",
        "description": "Execute a SELECT query against the SQLite database",
        "parameters": {
          "type": "object",
          "properties": {
            "query": { "type": "string", "description": "SQL query" }
          },
          "required": ["query"]
        }
      }
    ]
  }
  ```
* **Response (Error)**:
  ```json
  {
    "success": false,
    "error_code": "MCP_HANDSHAKE_FAILED",
    "message": "Process exited with code 1: Cannot find module '@modelcontextprotocol/server-sqlite'"
  }
  ```

---

## 8. 实施影响评估与零回归保证

1. **对原生内置工具零影响**：
   内置工具与容器工具依然走 `funcToolService`，调用链与分派逻辑完全未动；
2. **对现有 TSP 机制零破坏**：
   `gtsp` 的基础通信协议（`client.initialize`、`_tsp_tools`）原样保留，MCP 工具作为额外的 `OpenAITool` 注册进 `tool_registry`，与 TSP 工具并行互不影响；
3. **数据库兼容性**：
   新增独立表 `gt_mcp_server`，不修改已有表的 schema，向下完全兼容。

# 容器执行环境 exit_to_host 保活与挂起复用 — 技术文档

> 日期：2026-11-16  
> 状态：技术方案已定稿，待开发实施  
> 产品文档：[step1_product.md](./step1_product.md)  
> 调研依据：2026-11-10 容器化部署实战与容器执行环境可用性评审

本文档定义 `exit_to_host` 保活与 `gtsp` 挂起复用的纯 Python 侧实现方案，明确模块改动边界、状态机与锁管理、异常兜底策略及测试验证要求。

---

## 1. 方案选型与核心原理

### 1.1 为什么选择“Python 侧挂起复用”而非“gtsp 内部命令”

原 v2 方案拟在 Go 侧 `gtsp` 增加 `set_exit_policy` 内部命令并在 stdin EOF 时挂起，已被否决：
- 需要跨语言修改 Go 代码、扩展 TSP 协议并重编译三平台二进制；
- 挂起后再次 `enter_container` 仍需重新 `docker start / exec` + `initialize` 握手，保活不等于复用。

**核心源码洞察**：  
`TspAgentDriver.disconnect()` 底层调用 `TSPClient.disconnect()` → `process.terminate()`，**并非**“关闭 stdin 等待 EOF”。只要在 `exit_to_host(keep_alive=True)` 时不调用 `disconnect()` / `terminate()`，并由 Python 侧继续持有 `TSPClient` 实例（保持子进程对象与 stdin 管道打开）：
- 容器内 `gtsp` 进程持续阻塞读取 stdin，不会收到 EOF，也不会退出；
- `TSPClient` 内部的 `_read_task` / `_stderr_task` 继续在后台事件循环中消费 stdout / stderr；
- 自建容器中 `gtsp` 即 PID 1，`gtsp` 存活即容器保持 `running`；外部容器中 `gtsp` 会话保持存活，其子进程组不会被 `SIGHUP` 清理；
- 下次 `enter_container` 同一容器时直接把该 `TSPClient` 切回为活跃 client，跳过容器拉起与 `initialize` 握手。

该方案**零 `gtsp` 改动、零 TSP 协议扩展**，全部变更收敛在 Python `service` 层。

---

## 2. 架构与模块改动范围

严格遵循四层架构约束（`controller -> service -> dal -> model -> util`），本次改动全部位于 `service` 层，不新增 `model` 字段或 DB 迁移（挂起池与 `_current_container` 一样只存在于 Driver 内存，进程重启后一律回落宿主）：

| 模块 / 文件 | 改动摘要 | 说明 |
|---|---|---|
| `src/service/agentService/driver/base.py` | `AgentDriver.exit_to_host(self, keep_alive: bool = False) -> bool` | 基类接口对齐形参（不支持容器的驱动仍直接返回 `True`） |
| `src/service/agentService/driver/tspDriver.py` | 新增挂起池 `_suspended_clients: dict[str, TSPClient]`；改造 `exit_to_host`、`enter_container`、`shutdown` | 核心编排：挂起、短路复用、健康检测、全量清理 |
| `src/service/agentService/agent.py` | `Agent.exit_to_host(self, keep_alive: bool = False) -> bool` | 透传 `keep_alive` 给 `self.driver.exit_to_host(keep_alive=keep_alive)` |
| `src/service/agentService/core.py` | `exit_to_host(agent_id: int, keep_alive: bool = False) -> bool` | 透传 `keep_alive`；`switch_agent_to_host` 保持默认 `keep_alive=False`（清理路径必须真正停容器） |
| `src/service/funcToolService/tools.py` | `exit_to_host` 新增 `keep_alive: bool = False`；动态生成返回文案；补充 `exit_to_host` / `create_container` docstring | 退出前记录当前容器状态（`is_managed`），修复静态文案误导并补全使用引导 |
| `src/service/containerService/core.py` | **无需改动**（`validate_enter_state` 与 `_build_container_command` 保持原样） | 见 §3.3 短路机制说明；`delete_container` 的联动见 §3.5 |

---

## 3. 详细设计

### 3.1 挂起池与单执行环境不变量

在 `TspAgentDriver.__init__` 中新增：

```python
# 已挂起的容器 TSPClient 池：container_id (12 位短 ID) -> TSPClient
# 仅在持有 self._connect_lock 或处于 shutdown 清理时读写
self._suspended_clients: dict[str, TSPClient] = {}
```

**核心不变量保持不变**：
1. `self._current_container: Optional[ContainerTarget]` 仍是当前活跃执行环境的**唯一状态源**（`None` 表示宿主，非 `None` 表示容器）。挂起池中的容器**不属于**当前活跃环境，`exec_env` / `current_container_id` 仅由 `_current_container` 派生。
2. 同一时刻最多只有一个活跃 `self._client`（当前环境）；`_suspended_clients` 表达的是“暂停待复用”，不是多容器并发执行。
3. **锁边界规范**：`self._connect_lock`（`asyncio.Lock`）不可重入。`_connect_target()` / `disconnect()` 内部会获取或假定已持有 `_connect_lock`，因此挂起池的移入/取出操作若在锁外方法（`exit_to_host` / `enter_container`）与 `connect()` 协同，必须拆分好锁粒度，禁止在持有 `_connect_lock` 时调用 `self.connect()` 或 `self.disconnect()`。

### 3.2 `TspAgentDriver.exit_to_host(keep_alive: bool = False)`

执行流程如下：

1. **已在宿主幂等检查**：
   ```python
   if self.current_container is None:
       return True
   ```
2. **分支 A：`keep_alive=False`（默认，保持现状）**：
   - 清理同一容器可能残留的挂起 client（例如此前 `keep_alive=True` 挂起后又因宿主连接失败未切出，随后改用 `keep_alive=False` 重试）：从 `_suspended_clients` 中 `pop(container_id, None)` 并 `_close_client_quietly`。
   - 调用 `await self.disconnect()`（终止当前 `self._client`、清空 `_tsp_tools`，保留 `_current_container`）。
3. **分支 B：`keep_alive=True`（保活挂起）**：
   - 在 `async with self._connect_lock:` 内执行原子移交：
     - 若 `self._client is not None`：
       - 若 `self._is_client_connected()` 为 `True`，则将 `self._client` 存入 `self._suspended_clients[container_id]`（若该 key 已有旧对象且与当前不同，先静默关闭旧对象）；
       - 若当前 `self._client` 进程已死（`not self._is_client_connected()`），不放入挂起池，直接 `_close_client_quietly(self._client)`。
       - 置 `self._client = None`、`self._tsp_tools = {}`。
     - 注意：**不修改 `self._current_container`**（遵循“只由 `connect()` 成功时更新环境状态”的既有约定）。
4. **切回宿主连接**：
   - 调用 `ok = await self.connect(ExecEnv.HOST)`。
   - 若 `ok` 为 `False`：抛出 `TogoException(..., containerService.ERROR_CONTAINER_ENTER_FAILED)`。此时 `self._current_container` 仍为原容器，Agent 重试 `exit_to_host` 不会误判为已在宿主；且在 `keep_alive=True` 场景下，原容器的 `gtsp` 已安全留在 `_suspended_clients` 中，重试时因 `self._client is None` 不会被覆盖为 `None`。
   - 若 `ok` 为 `True`：`_connect_target` 内部会先对 `self._client`（此时为 `None`）执行 `_close_client_quietly(None)`（空操作），随后建立宿主连接并发布 `AGENT_CONTAINER_STATE_CHANGED`。

### 3.3 `TspAgentDriver.enter_container(container_id: str)` 短路复用

执行流程如下：

1. **目标解析与幂等检查（不变）**：
   - `target, state = await containerService.resolve_enter_target(self.host.gt_agent.id, container_id)`
   - 若 `self.current_container_id == container_id and self.is_connected()`，直接 `return target, state`。
2. **挂起池查找与健康检测（在 `validate_enter_state` 之前短路）**：
   - 在 `async with self._connect_lock:` 内尝试取出：`suspended = self._suspended_clients.pop(container_id, None)`。
   - 若 `suspended is not None`：
     - 健康检测（与 `_is_client_connected` 同口径）：`proc = suspended.process`，判断 `proc is not None and proc.returncode is None`。
     - **若存活（命中复用）**：
       1. 关闭当前活跃连接（可能是宿主 client，也可能是直接跨容器切换时的另一个容器 client）：`await self._close_client_quietly(self._client)`，置 `self._client = None`、`self._tsp_tools = {}`；
       2. 恢复挂起 client：`self._client = suspended`；
       3. 重载工具与注册表：`self._load_tsp_tools(suspended.tools)`、`self._register_host_tools()`；
       4. 更新环境状态并广播：
          - 记录 `previous_env = self.exec_env`、`previous_container_id = self.current_container_id`；
          - `self._current_container = target`；
          - 若环境或容器 ID 发生变化，调用 `self._publish_container_state()`；
       5. 直接 `return target, state`（跳过 `validate_enter_state`、`prepare_container_gtsp`、`docker start/exec` 与 `initialize`）。
     - **若已死亡**：
       - 调用 `await self._close_client_quietly(suspended)` 清理管道与后台读任务；
       - 继续走下面的正常拉起流程。
3. **正常拉起流程（未命中或挂起已死时，完全复用现状）**：
   - `ContainerState.UNKNOWN` 报错；
   - `containerService.validate_enter_state(target, state)`；
   - 外部容器注入 `prepare_container_gtsp(target)`；
   - `await self.disconnect()` → `await self.connect(ExecEnv.CONTAINER, target)`（失败则恢复宿主并抛错）。

**为什么不需要改动 `validate_enter_state`**：
- 有存活的挂起 `gtsp` 时，复用路径在 `validate_enter_state` 之前短路返回（此时自建容器处于 `running` 是保活中的合法态）；
- 无存活挂起 `gtsp` 时才进入 `validate_enter_state`：若自建容器仍为 `running`（说明保活的 `gtsp` 意外死亡但容器未停），继续按“异常残留”拦截报错，语义完全自洽。

### 3.4 `TspAgentDriver.shutdown()` 全量清理

`shutdown()` 除关闭当前 `self._client` 外，必须清理挂起池内全部 client，防止 Agent 卸载或进程退出时遗留子进程与容器进程：

```python
suspended = list(self._suspended_clients.values())
self._suspended_clients.clear()
for client in suspended:
    try:
        await client.disconnect()
    except Exception as e:
        logger.warning("关闭挂起 TSP client 异常: agent_id=%s, error=%s", self.host.gt_agent.id, e)
```

> 说明：沿用 `TspAgentDriver.shutdown()` 现有 `await client.disconnect()` 风格（直接终止进程，不等待 shutdown request），并对每个挂起 client 独立捕获异常，确保单个关闭失败不影响其余 client 与 `await super().shutdown()`。

### 3.5 `delete_container` 与外部停止的联动

- `containerService.delete_container` 仅阻止删除 `current_container_id`（当前活跃容器）。处于挂起池中的容器已退出为非活跃态，允许被直接 `delete_container`。
- 当自建容器被 `delete_container`（`docker rm -f`）或外部被 `docker stop` / `docker rm` 停止后：
  - 容器内挂起的 `gtsp` 进程随之退出，stdout 触发 EOF，`TSPClient._read_task` 自然结束；
  - `self._suspended_clients` 中的死对象在下次 `enter_container`（健康检测淘汰）或 `shutdown()` 时被清理。
  - 若需要更及时的内存释放，可在 `agentService` 或 `TspAgentDriver` 暴露 `evict_suspended_container(container_id)` 供 `delete_container` 成功后 best-effort 清理（可选，不阻塞主路径）。

### 3.6 `tools.py` 动态文案与 `is_managed` 获取

`src/service/funcToolService/tools.py` 中 `exit_to_host` 当前签名与实现需要调整：

```python
async def exit_to_host(
    keep_alive: bool = False,
    _context: ToolCallContext = None,
) -> dict[str, Any]:
```

**退出前获取容器元数据**：
- `ExecEnvState` 是面向控制器/活动记录/列表的精简快照，不含 `is_managed` 字段，无需为工具文案污染 `ExecEnvState` 模型定义。
- 在 `tools.py` 调用 `await agent.exit_to_host(keep_alive=keep_alive)` 之前，可通过 `agent.driver.current_container`（`Optional[ContainerTarget]`，在 `Mock` 场景下用 `getattr(getattr(agent, "driver", None), "current_container", None)` 安全读取）判断是否从容器退出以及 `target.is_managed`：
  - 若退出前为宿主（`prev_container is None`）：返回幂等提示“当前已处于宿主机环境，宿主工作目录生效。”；
  - 若退出前为容器：按 `(prev_container.is_managed, keep_alive)` 4 种组合生成准确的 `message`（见 [产品文档 §3.2](./step1_product.md#32-exit_to_host-动态返回文案)）；
  - 若 `driver` / `current_container` 在单元测试 mock 中未设置（`prev_container is None` 且 `exit_to_host` 被直接 mock），需注意兼容既有单测 `test_exit_to_host_reports_host_workdir`（该用例期望在默认 mock 下返回宿主机 `workdir` 且 `message` 包含 `/workspace/self` 提示，因此仅当明确检测到 `exec_env == ExecEnv.HOST` 时走幂等文案，或将该单测同步更新为显式构造容器态）。

---

## 4. 工具 Schema 与返回契约变更

### 4.1 `exit_to_host` 工具入参变更

- 新增可选布尔参数 `keep_alive: bool = False`。
- **Schema 快照联动**：`tests/unit/service/funcToolService/test_tool_schema_snapshot.py` 与 `tests/fixtures/tool_schema_snapshot.json` 会校验所有工具的参数签名快照，实施时必须同步更新 `exit_to_host` 的快照定义。

### 4.2 `exit_to_host` 返回结构

返回字段保持向后兼容，不破坏既有调用方：

```python
{
    "success": True,
    "exec_env": ExecEnv.HOST.value,
    "workdir": agent.agent_workdir,
    "keep_alive": keep_alive,  # 可选补充，便于调用方/日志观测
    "message": dynamic_message,
}
```

---

## 5. 已知限制与后续演进

1. **资源常驻**：每个挂起容器占用一个 `gtsp` 进程（数 MB 内存）及自建容器持续 `running` 的内存/CPU 开销；多容器长期挂起无 TTL 自动回收（本期不引入后台回收定时器，由 Agent 显式 `exit_to_host(keep_alive=False)` 或 `delete_container` / `shutdown` 释放）。
2. **后台进程提示**：`keep_alive=True` 下 `run_in_background` 进程不会死亡；`keep_alive=False` 下的行为限制通过工具 docstring 与技术文档明确说明，本期不在 `gtsp` 侧增加退出前后台进程枚举接口。

---

## 6. 测试与验证计划

需在单元测试中覆盖以下场景（`tests/unit/service/agentService/test_tsp_driver_container.py`、`test_enter_exit_flow.py`、`tests/unit/service/funcToolService/test_container_tools.py`、`test_tool_schema_snapshot.py`）：

1. **`exit_to_host` 默认行为（`keep_alive=False`）**：断开并终止 `_client`，不写入 `_suspended_clients`，切回宿主。
2. **`exit_to_host(keep_alive=True)` 挂起**：不调用 `disconnect`/`terminate`，`_client` 移入 `_suspended_clients[container_id]`，切回宿主后宿主工具可用。
3. **`enter_container` 命中复用（自建 & 外部）**：从 `_suspended_clients` 取出存活 client 切回，不触发 `validate_enter_state`、`prepare_container_gtsp`、`docker start/exec` 与 `initialize`，并正确重载 `_tsp_tools` 与发布状态事件。
4. **多容器并存复用**：先后进入并保活挂起容器 A 和容器 B，再分别切回 A、B 均能命中各自独立的挂起 client。
5. **跨容器直接切换**：在容器 A 中直接 `enter_container(B)`（B 在挂起池中），A 的 client 被关闭，B 的挂起 client 被复用。
6. **挂起进程死亡兜底**：模拟挂起 client 的 `process.returncode = 1`，`enter_container` 时自动清理死对象并回退到正常校验/拉起流程（自建容器 `running` 仍报异常残留）。
7. **宿主重连失败重试**：`exit_to_host(keep_alive=True)` 时 `connect(HOST)` 失败，环境保持容器、挂起 client 不丢失；后续重试 `exit_to_host(keep_alive=True)` 或 `exit_to_host(keep_alive=False)` 行为正确。
8. **`shutdown()` 全量清理**：存在活跃 client 与多个挂起 client 时，`shutdown()` 全部断开清理且单个异常不阻塞其余清理。
9. **`tools.exit_to_host` 动态文案与 Schema 快照**：覆盖 `is_managed × keep_alive` 4 种组合文案，更新并跑通 `test_tool_schema_snapshot.py`。

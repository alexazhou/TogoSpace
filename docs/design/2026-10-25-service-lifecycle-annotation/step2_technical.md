# Service 生命周期注解化 - 技术文档

> 状态：已实施（2026-10-25，Task #39 实现 / #40 审查 / #41 测试，均完成）
> 来源设计稿：工作目录 `Service生命周期注解化管理-设计文档.md`（v1.6）
> 关联规范：[Service 模块规范](../../tech/02_mvc/service_conventions.md)

本文记录「进程级 Service 生命周期」的统一治理方案：用一对注解（装饰器）接管所有服务的启动/停止，统一状态机、异常语义与回滚，消除各服务 `startup()` 语义不一致带来的隐性缺陷。

---

## 1. 背景与问题

启动序列见 `src/backend_main.py:133-145`（阶段 1 依次启动各服务），关闭序列见 `:181-189`。治理前逐个体检：

| # | 服务 | 治理前 startup 行为 | 分类 |
|---|------|---------------------|------|
| 1 | messageBus | 计数器归零 + `_subscribers.clear()` | 重置型 |
| 2 | llmService | 无副作用 | no-op |
| 3 | funcToolService | 重建工具表 | 重置型 |
| 4 | ormService | `if _db is not None: return`，**不校验 db_path** | 有守卫（危险） |
| 5 | persistenceService | `pass` | no-op |
| 6 | agentService | `_agents = {}` 重建 | 重置型 |
| 7 | roomService | `_rooms.clear()` | 重置型 |
| 8 | schedulerService | 每次 `subscribe`（不去重）→ **重复订阅** | 不可重复 |
| 9 | presetService | `return None` | no-op |
| 10 | skillService | `_registry = {}` 重建 | 重置型 |
| 11 | teamService | `return None`（且**无 shutdown**） | 占位 |
| 12 | roleTemplateService | `return None` | 占位 |

三个病灶：

1. **静默早退**：`ormService.startup()` 已启动就直接返回、不看新 `db_path` → 漏 shutdown 时**静默复用旧库**、删库重建失效。
2. **重复订阅**：`schedulerService.startup()` 无守卫 → `messageBus.subscribe` 不去重（`messageBus.py:33`）→ 事件回调重复执行。
3. **语义混淆**：重置型服务把「清空重建」塞进 `startup()`，读代码必须逐个数行为才能判断「重复启动会发生什么」。

---

## 2. 目标

用一个**统一的注解**接管启动过程：**管理状态、捕获异常、确保只启动一次、失败必回滚**，从而消除静默早退/重复订阅、让漏 shutdown 立即 fail loud、把契约集中到一处。

参考实现：同组织 `trader` 仓库（`source/util/serviceUtil.py`）。借用其状态与注解思路，并补齐三点：**异常向上抛**（fail loud）、**只启动一次**（以状态为唯一真源）、**停止幂等 + 失败留痕**。

---

## 3. 契约设计

### 3.1 状态机

```
  Idle ──startup──▶ Starting ──成功──▶ Running
                       │                │
                       └──失败──▶ Error │ shutdown（幂等）
                                         ▼
                    Stopped ◀── Stopping
```

- **`startup` 允许态 = `{Idle, Stopped}`**；`Starting` / `Running` / `Stopping` / `Error` 再 `startup` → 抛 `TogoException(error_code="SERVICE_ALREADY_STARTED")`，**不执行 body、不改写状态**。
- **`Error` 恢复路径固定为 `shutdown() → startup()`**：`shutdown()` 在 `Error` 态**仍执行清理**并回到 `Stopped`，之后才允许再次 `startup`；不允许在 `Error` 态直接重入。
- `shutdown` **幂等且不抛错**：仅 `Idle`（从未启动）/ `Stopped`（重复关闭）态 no-op 并输出 warning；`Starting` / `Running` / `Stopping` / `Error` 均执行清理体并回到 `Stopped`。
- **失败必回滚**：`startup` 中途失败时，实现体必须清理已申请资源（含 `bind_database` 这类副作用），保证进入 `Error` 态后**无半启动残留**——这是 `Error → shutdown → startup` 恢复路径成立的前提。

### 3.2 注解的三项职责（+ 回滚）

1. **管理启动过程**：`startup` 前 `reset_to_status(Starting)`；成功置 `Running`；`shutdown` 置 `Stopping → Stopped`。
2. **捕获异常并向上抛**：`startup` 抛错 → 记 `status=Error` + `error`，**原样 re-raise**（编排层 fail fast）。
3. **确保只启动一次**：以 `status` 为唯一真源；仅 `Idle`/`Stopped` 可 `startup`。
4. **失败必回滚**：`startup` 失败时先 `await on_error()`（best-effort）回滚，再置 `Error`、再 re-raise。
5. **清理失败留痕**：`shutdown` body 抛错时**不 re-raise、仍回 `Stopped`**（铁律不变），但在 `reset_to_status(Stopped)` **之后**补 `fine=False` + `error`，使清理失败可观测（与 startup 失败路径对称）。

### 3.3 资源管理约定（startup 创建 / shutdown 清空）

- **startup：创建资源** —— 把容器初始化为空，如 `_x = {}`；
- **shutdown：清空资源** —— 对容器**原地清空**，如 `_x.clear()`；
- **禁止**在 shutdown 里把变量重新赋值为 `{}`（那是「创建」而非「清空」）；
- 例外：标量（int/str 等）无法 `clear()`，用「重置为初值」（如 `_event_id_counter = 0`）。

收益：创建归 startup、清空归 shutdown，职责清晰；`.clear()` 原地清空保持对象身份稳定，避免外部已持有的引用被悄悄换成新对象。

---

## 4. 实现方案

### 4.1 状态枚举 `src/constants.py::ServiceStatus`

```python
class ServiceStatus(EnhanceEnum):
    Idle = auto()      # 未启动
    Starting = auto()
    Running = auto()
    Stopping = auto()
    Stopped = auto()   # 停止/曾运行后停止
    Error = auto()     # 启动失败
```

### 4.2 状态对象 `src/model/coreModel/serviceStatus.py::ServiceStatusInfo`

```python
@dataclass
class ServiceStatusInfo:
    name: str
    status: ServiceStatus = ServiceStatus.Idle   # 「是否已启动」的唯一真源
    fine: bool = True
    error: str | None = None
    detail: dict = field(default_factory=dict)

    def reset_to_status(self, status: ServiceStatus) -> None:
        self.status = status
        self.fine = True
        self.error = None
        self.detail = {}
```

> 分层：`model` 层允许依赖 `util` + `constants`，符合四层架构。

### 4.3 注解实现 `src/service/serviceUtil.py`

放在 `service/` 层（可依赖 model/constants），避免 `util` 层反向依赖 `model`。**硬严格，不设灰度开关**。

对外 API：

| 注解 | 签名 | 语义 |
|------|------|------|
| `service_start_wrapper` | `(status, on_error=None)` | 管理状态 + 捕获异常向上抛 + 确保只启动一次 + 失败回滚 |
| `service_stop_wrapper` | `(status)` | 幂等停止；仅 `Idle`/`Stopped` no-op + warning；异常留痕不抛 |

关键实现点：

- **启动前置校验**（`_begin_start`）：`if status.status not in (ServiceStatus.Idle, ServiceStatus.Stopped): raise TogoException(..., error_code="SERVICE_ALREADY_STARTED")`，前置失败**不改写状态**。
- **回滚钩子**：`on_error` 为可选参数，类型 `Callable[[], Awaitable[None] | None]`；`startup` body 抛异常时先 `await on_error()`，**吞掉回滚自身异常（仅 warning）**，绝不掩盖原始启动异常；随后置 `Error` 并 `raise`。
- **停机留痕**（`_finish_shutdown`）：`finally` 中先 `reset_to_status(Stopped)`，若 body 抛错则在其后补 `fine=False` + `error`。
- **同步/异步兼容**：注解按 `inspect.iscoroutinefunction(func)` 分流，**保留被装饰函数的同步/异步形态**——异步函数返回可 await 的协程，同步函数同步执行。仓库中存在同步实现（`skillService.startup`、`schedulerService.shutdown` 等）且被业务/测试同步调用，强改会破坏调用点。契约语义不受形态影响。
- **`_reset()` 复用约定**：持有资源/有副作用的服务提供**未被注解装饰**的纯清理协程/函数 `_reset()`（幂等），`startup` 挂 `on_error=_reset`，`shutdown` body 只调 `_reset()` → 回滚与正常关闭**共用同一份清理逻辑**，防止两份实现漂移。

> 例外：`schedulerService` 的 `startup` 只申请「订阅」这一项资源，故其 `on_error` 为**收窄的** `_unsubscribe()`（只退订），不越界去停 consumer / 遍历房间（那些属 `shutdown` 的 `_reset()` 职责）。

### 4.4 ormService 方案 A + 悬空绑定修复

- **删除** `if _db is not None: return` 静默早退；二次启动由注解统一拦截（不做 `db_path` 比对），保留 `assert _db is None` 作二重防御。
- **悬空绑定**：`bind_database()` 用 peewee `DatabaseProxy.initialize(database)` 绑定全局 proxy，而 peewee **无官方 unbind**。新增 `src/model/dbModel/base.py::unbind_database()`（`_database_proxy.initialize(None)`），在 `_reset()` 末尾调用——覆盖「`bind_database` 之后、`_db = database` 之前」失败（`check_database_initialized` / `aio_connect`）的路径，避免残留**指向已 close 库的 proxy 绑定**。
- 验证：peewee 3.19.0 的 `Proxy.initialize()` 无「已初始化」守卫、可重复调用；`initialize(None)` 解绑后任何 model 访问抛 `AttributeError('Cannot use uninitialized Proxy.')`（fail loud）。

### 4.5 12 个服务接入

每个服务：新增模块级 `s_status = ServiceStatusInfo(name="xxx")`，`startup` / `shutdown` 各挂注解；有资源者提供 `_reset()` 并挂 `on_error`。

| 服务 | 模块 | `startup` 签名（sync/async） | `shutdown` | `on_error` |
|------|------|------------------------------|-----------|-----------|
| messageBus | `service/messageBus.py` | `async startup()` | async | `_reset` |
| llmService | `service/llmService/core.py` | `async startup()` | sync | —（无资源） |
| funcToolService | `service/funcToolService/core.py` | `async startup()` | sync | `_reset` |
| ormService | `service/ormService.py` | `async startup(db_path: str)` | async | `_reset` |
| persistenceService | `service/persistenceService.py` | `async startup()` | async | —（无资源） |
| agentService | `service/agentService/core.py` | `async startup()` | async | `_reset` |
| roomService | `service/roomService/core.py` | `async startup()` | sync | `_reset` |
| schedulerService | `service/schedulerService.py` | `async startup()` | sync | `_unsubscribe` |
| presetService | `service/presetService.py` | `async startup()` | async | —（无资源） |
| skillService | `service/skillService.py` | **`startup()`（同步）** | sync | `_reset` |
| teamService | `service/teamService.py` | `async startup()` | async | —（占位，**本次补 shutdown**） |
| roleTemplateService | `service/roleTemplateService.py` | `async startup()` | async | —（占位） |

> 资源型服务集合（守卫测试 R4 强制要求挂 `on_error`）：`messageBus` / `funcToolService` / `ormService` / `agentService` / `roomService` / `schedulerService` / `skillService`。

### 4.6 启动编排（`src/backend_main.py`）

顺序保持不变，异常自然向上冒泡（注解已 re-raise），不做额外 `sys.exit`：

```
阶段 1 启动：messageBus → llmService → funcToolService → ormService → persistenceService
            → agentService → roomService → schedulerService → presetService → skillService
关闭（finally，逆序）：schedulerService → agentService → persistenceService → ormService
            → funcToolService → skillService → roomService → llmService → messageBus
```

> `teamService` / `roleTemplateService` 未纳入 `backend_main` 阶段 1 启动与 finally 关闭（占位服务，注解语义需其 startup 被调用才生效）；`presetService` 已启动但 finally 未关闭。此为已知遗留，记为二期跟进项。

### 4.7 测试集成（`tests/base.py`）

- `ServiceTestCase` 新增兜底停机 `_reset_in_process_services()`（`teardown_class` 的 `finally` + `setup_class` 异常路径，按 `backend_main` 顺序 best-effort 停机）：把「漏 shutdown → 跨类静默污染」变为可自愈。
- 代价：子类漏配对的缺陷不再由测试失败暴露 → 二期拟加严格只读开关（见 §8）。

---

## 5. 影响面

### 5.1 代码（src）

| 文件 | 改动 |
|------|------|
| `src/constants.py` | 新增 `ServiceStatus` 枚举 |
| `src/model/coreModel/serviceStatus.py` | 新增 `ServiceStatusInfo` |
| `src/service/serviceUtil.py` | 新增 `service_start_wrapper` / `service_stop_wrapper` |
| `src/model/dbModel/base.py` | 新增 `unbind_database()` |
| 12 个服务模块 | 各加 `s_status` + 两注解；删除手写守卫；有资源者提供 `_reset()` 并挂 `on_error` |
| `src/service/ormService.py` | 删静默早退（方案 A）；保留 `assert _db is None`；清理收敛进 `_reset()`（含 `unbind_database()`） |
| `src/service/teamService.py` | 补 `shutdown()` |
| `src/backend_main.py` | 顺序不变 |

### 5.2 测试（tests）

| 文件 | 改动 |
|------|------|
| `tests/unit/architecture/test_service_lifecycle.py` | **新增**：架构守卫（AST 结构 + 6 态契约矩阵 + 回滚/留痕/R4） |
| `tests/integration/test_funcToolService/test_skill_service_startup.py` | 补 `skillService.shutdown()` 配对 |
| `tests/integration/test_funcToolService/test_load_skill.py` | 补 `skillService.shutdown()` 配对 |
| `tests/integration/test_message_bus/test.py` | 用例内补 `await messageBus.startup()`（严格口径下 shutdown 需先启动） |
| `tests/integration/test_scheduler_service/test.py` | 用例内补 `await scheduler.startup()`（同上） |
| `tests/real/simple_chat/test_simple_chat.py` | 补 `presetService.shutdown()` 配对 |
| `tests/base.py` | 新增 `_reset_in_process_services()` 兜底停机 |

> 测试侧本次共改动 6 个文件（5 个用例文件 + `tests/base.py`），另新增守卫测试目录 `tests/unit/architecture/`。以 `git diff` 为准。

- AgentDriver / Agent 实例**不受影响**（不带入本次范围）。

---

## 6. 决策记录（Operator 确认）

1. 已启动未 shutdown 再次 `startup` → **抛错**（`SERVICE_ALREADY_STARTED`）。
2. 注解捕获异常后 → **向上抛**（fail loud）。
3. ormService → **方案 A**（注解统一拦截，不做 db_path 校验）。
4. 范围：**不含** AgentDriver / Agent，仅进程级服务。
5. 资源管理：**startup 创建**（`_x = {}`）、**shutdown 清空**（`_x.clear()`）；禁止 shutdown 内 `_x = {}`；标量重置初值。
6. 上线方式：**一步到位**（硬严格，不设灰度期）。
7. 占位服务（teamService / roleTemplateService）→ **也加注解**，补齐 teamService 的 `shutdown()`。
8. 注解放置：`src/service/serviceUtil.py`。
9. `shutdown` 幂等：仅 `Idle`/`Stopped` no-op + warning；其余态执行清理并回 `Stopped`；均不抛错。
10. `startup` 允许态 = `{Idle, Stopped}`；`Error` 恢复路径 = `shutdown() → startup()`；`startup` 失败必须完整回滚。
11. **回滚落地机制**：`service_start_wrapper` 增 `on_error` 钩子；各服务用纯清理 `_reset()` 承担回滚并与 `shutdown` 复用；ormService 补 `unbind_database()` 消除悬空绑定。
12. **清理失败留痕**：`shutdown` 铁律不变；body 抛错时在 reset 之后补 `fine=False` + `error`。
13. **同步/异步兼容**：注解保留被装饰函数的同步/异步形态（不改调用点）。

### 仍待定（二期）

- 「进程退出时打印未 shutdown 服务清单」的泄漏报告 —— 默认不做，需要再议。

---

## 7. 验收标准

- `startup` 仅在 `Idle`/`Stopped` 允许；其余态再 `startup()` → 抛错且 `error_code=SERVICE_ALREADY_STARTED`，不执行 body、不改写状态；
- `shutdown()` 幂等、不抛错：仅 `Idle`/`Stopped` no-op + warning；其余态执行清理并回 `Stopped`；
- `shutdown()` body 抛错 → 不抛错、仍回 `Stopped`，但 `fine=False` 且 `error` 非空；
- `startup` 中途失败 → 先执行 `on_error` 回滚（best-effort），再 `status=Error`、异常向上抛，且**无半启动残留**，可经 `shutdown()→startup()` 恢复；
- `on_error` 自身抛错不影响状态机与原始异常（仅 warning）；
- `ormService`：任何二次 `startup` → 抛错；`_reset()` 后 proxy 无悬空绑定；
- `schedulerService`：重复 `startup()` 不产生重复订阅；
- 资源管理：`startup` 创建、`shutdown` 清空，无 `= {}` 混用；
- 12 个服务均挂注解与 `s_status`；
- 全量 `./scripts/run_tests.sh` 通过；新增生命周期架构守卫测试通过。

---

## 8. 风险与对策

| 风险 | 对策 |
|------|------|
| 一步到位暴露存量漏 shutdown → 短期红灯增多 | 分批修至全绿；每类服务单独跑测试验证 |
| shutdown 若也严格会误伤「未启动先 shutdown」兜底写法 | 铁律：startup 严格、shutdown 幂等 |
| clear 移到 shutdown 后，依赖「startup 顺带清空」的用例回归 | 排查并在用例前显式 shutdown；逐服务跑测试 |
| 注解 re-raise 影响启动编排 | 保持 backend_main 顺序不变，异常自然冒泡 |
| `on_error` 回滚失败或与 shutdown 逻辑漂移 | 回滚与 shutdown 共用同一 `_reset()`；回滚异常仅 warning |
| `unbind_database()` 影响依赖 proxy 生命周期的模块 | 仅解绑（`initialize(None)`），不销毁对象；全量测试验证 |
| 测试兜底停机掩盖「漏配对」 | 二期加严格只读开关（仅断言活跃态、报红列出服务名 + `s_status.error`，不修复） |

### 二期 backlog

- `_rollback_sync` 旁路：同步 `startup` + 异步 `on_error` 且在事件循环内时回滚被跳过（当前无服务命中）；
- `= {}` / `.clear()` 一致性梳理（messageBus / agentService 无外部引用，无实际影响）；
- `backend_main` 关闭序列补 `presetService` / `teamService` / `roleTemplateService`；
- 测试兜底停机严格开关（`TEAMAGENT_TEST_STRICT_LIFECYCLE=1`，只读断言）。

# Service 生命周期注解化 - 产品文档

> 状态：已实施（2026-10-25）

本文面向开发/维护者，描述「进程级 Service 生命周期」治理后的**外部可观察行为**与接入方式。技术细节见 [step2_technical.md](./step2_technical.md)。

## 背景与问题

项目里的每个 service 都是模块（非类），各自用模块级私有变量维护状态，并对外提供 `startup()` / `shutdown()` 两个生命周期函数。但治理前**各服务的 startup 语义完全不统一**，读代码必须逐个数行为才能判断「重复启动会发生什么」，由此产生三类隐患：

- **静默早退**：`ormService` 已启动就直接 `return`、不看新库路径 → 漏 shutdown 时**静默复用旧库**，删库重建失效（最难排查）。
- **重复订阅**：`schedulerService` 每次 startup 都重复 `subscribe` → 事件回调被执行多次。
- **语义混淆**：「重置型」服务把「清空重建」塞进 `startup()`，职责不清。

## 目标

用一对统一的**生命周期注解**接管所有进程级服务的启动/停止，把契约集中到一处：**只允许启动一次、失败必须回滚、停止幂等且可观测**。

## 行为契约（外部可观察）

| 场景 | 行为 |
|------|------|
| 正常 `startup()`（`Idle`/`Stopped`） | 服务进入运行态；只执行一次 |
| 非 `{Idle, Stopped}` 态再次 `startup()`（`Starting`/`Running`/`Stopping`/`Error`） | **抛错**（`SERVICE_ALREADY_STARTED`），且不执行 body、不改变状态 |
| `startup()` 中途失败 | 先**回滚已申请资源**，置错误态，再把原始异常向上抛（fail loud），不留半启动残留 |
| 启动失败后恢复 | 必须先 `shutdown()` 清理，再 `startup()` |
| `shutdown()`（运行态，及 `Starting`/`Stopping`/`Error` 等中间态） | 执行清理并回到已停止态 |
| `shutdown()`（从未启动 `Idle` / 重复关闭 `Stopped`） | 空操作 + 告警日志，不报错 |
| `shutdown()` 清理体报错 | **不向上抛错**，仍回到已停止态，但**留痕**（`fine=False` + `error`），使失败可观测 |

> 口径：`shutdown()` 除 `Idle`/`Stopped` 外一律执行清理并回到 `Stopped`（含 `Starting`/`Stopping`/`Error` 中间态）。

## 功能特性

### 1. 生命周期状态可见

每个服务持有模块级状态对象（`s_status`），状态为：`Idle → Starting → Running → Stopping → Stopped`，以及启动失败时的 `Error`（中文别名：未启动 / 启动中 / 运行中 / 停止中 / 已停止 / 启动失败）。状态是「是否已启动」的唯一真源。

### 2. 只启动一次（fail loud）

任何非 `{Idle, Stopped}` 态的再次 `startup` 都抛 `SERVICE_ALREADY_STARTED`。彻底消除「漏 shutdown 后静默复用旧资源（旧库 / 旧订阅）」的问题。

### 3. 失败必回滚、无半启动残留

启动中途失败会**先回滚本次已申请的资源**再抛错，保证进入错误态后无残留，可通过「先关闭再启动」干净恢复。对有副作用资源（如数据库全局绑定）同样成立。

### 4. 停止幂等、清理失败可观测

`shutdown()` 可安全重复调用、不抛错；清理体若失败会留下可观测痕迹（而非被静默抹平）。

### 5. 12 个进程级服务统一接入

`messageBus` / `llmService` / `funcToolService` / `ormService` / `persistenceService` / `agentService` / `roomService` / `schedulerService` / `presetService` / `skillService` / `teamService` / `roleTemplateService` 全部接入统一注解与状态对象。

## 使用说明（新增一个 Service 时）

1. 模块内定义 `s_status = ServiceStatusInfo(name="xxx")`；
2. `startup` 挂 `@service_start_wrapper(s_status[, on_error=_reset])`，`shutdown` 挂 `@service_stop_wrapper(s_status)`；
3. 资源管理遵循「**startup 创建（`_x = {}`）、shutdown 清空（`_x.clear()`）**」，禁止在 shutdown 内 `_x = {}`；标量重置为初值；
4. 持有资源/有副作用的服务，提供**未被注解装饰**的纯清理函数 `_reset()`（幂等），既作 `on_error` 又供 `shutdown` body 复用；
5. 不要手写「重复启动守卫」，契约由注解统一承担；测试里的 `startup`/`shutdown` 必须**成对**（setup 启动、teardown 关闭）。

## 验收标准摘要

- 二次启动抛 `SERVICE_ALREADY_STARTED`，不执行 body、不改状态；
- 停止幂等、不抛错；清理失败留痕（`fine=False` + `error`）；
- 启动失败先回滚、无残留、异常向上抛，可 `shutdown()→startup()` 恢复；
- 12 个服务均挂注解与 `s_status`；
- 全量 `./scripts/run_tests.sh` 通过，架构守卫测试通过。

完整验收项见 [step2_technical.md §7](./step2_technical.md)。

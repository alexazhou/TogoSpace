# Service 生命周期注解化 - 开发任务表

> 状态：已实施（2026-10-25，Task #39 实现 / #40 审查 / #41 测试 / #42 落库，均完成）
> 标记：`[x]` 已完成，`[ ]` 待完成 / 二期。

## 任务概览

按依赖排序：先落基础设施（枚举 / 状态对象 / 注解 / `unbind_database`），再做 12 个服务接入，随后修测试并对齐编排，新增架构回归测试，最后回归与落库。

---

## 任务列表

### 任务 1: 生命周期基础设施

**描述**: 新增状态枚举、状态对象与两个生命周期注解，以及 ormService 需要的 `unbind_database()`。

**依赖**: 无

**核心文件**:
- `src/constants.py`
- `src/model/coreModel/serviceStatus.py`
- `src/service/serviceUtil.py`
- `src/model/dbModel/base.py`

**子任务**:
- [x] `constants.ServiceStatus`：`Idle/Starting/Running/Stopping/Stopped/Error`
- [x] `serviceStatus.ServiceStatusInfo(name/status/fine/error/detail)` + `reset_to_status()`
- [x] `serviceUtil.service_start_wrapper(status, on_error=None)`：管理状态 + 异常向上抛 + 只启动一次 + 失败回滚
- [x] `serviceUtil.service_stop_wrapper(status)`：幂等 + 非 `Idle/Stopped` no-op & warning + 清理失败留痕
- [x] 注解保留被装饰函数同步/异步形态（`inspect.iscoroutinefunction` 分流）
- [x] `dbModel/base.py::unbind_database()`（`_database_proxy.initialize(None)`）

**验收标准**: 契约回归检查（6 态矩阵）通过；`unbind_database()` 解绑后 model 访问 fail loud。

### 任务 2: 12 个服务接入

**描述**: 每个服务加 `s_status` + 两注解，删除手写检查；有资源者提供 `_reset()` 并挂 `on_error`。

**依赖**: 任务 1

**核心文件**: 12 个服务模块（见 [step2_technical.md §4.5](./step2_technical.md)）

**子任务**:
- [x] `messageBus` / `funcToolService` / `agentService` / `roomService` / `skillService`：重置型，提供 `_reset()`
- [x] `ormService`：方案 A（删静默早退、保留 `assert _db is None`），`_reset()` 含 `unbind_database()`
- [x] `schedulerService`：`startup` 幂等订阅（先 unsubscribe 再 subscribe）；`on_error=_unsubscribe`（收窄为仅退订）
- [x] `teamService`：补 `shutdown()`
- [x] `llmService` / `persistenceService` / `presetService` / `roleTemplateService`：无资源，仅挂注解
- [x] 资源管理遵循「startup 创建 / shutdown `.clear()`」

**验收标准**: 12 个服务均挂注解与 `s_status`；契约逐条符合。

### 任务 3: 测试修复与配对对齐

**描述**: 修复受影响的用例，补齐 `startup`/`shutdown` 配对。

**依赖**: 任务 2

**核心文件**:
- `tests/integration/test_funcToolService/test_skill_service_startup.py`
- `tests/real/simple_chat/test_simple_chat.py`
- `tests/base.py`

**子任务**:
- [x] `test_skill_service_startup.py`：补 `skillService.shutdown()` 配对（12 次 `startup`）
- [x] `test_simple_chat.py`：补 `presetService.shutdown()` 配对
- [x] `tests/base.py`：新增 `_reset_in_process_services()` 兜底停机（teardown 的 `finally` + setup 异常路径）

**验收标准**: 受影响用例全绿；静态配对审计仅剩 `driver`（属排除范围）。

### 任务 4: 编排对齐与全量回归

**描述**: `backend_main` 启动/关闭顺序不变；跑全量测试修至全绿。

**依赖**: 任务 2、3

**核心文件**: `src/backend_main.py`、`./scripts/run_tests.sh`

**子任务**:
- [x] `backend_main` 顺序不变（异常自然冒泡）
- [x] 全量 `./scripts/run_tests.sh` → 1097 passed
- [x] 串行 `./scripts/run_tests.sh --serial` → 1097 passed（无顺序依赖）

**验收标准**: 全量并行 + 串行均通过。

### 任务 5: 架构回归测试（#41）

**描述**: 新增生命周期回归测试，防止未来回归。

**依赖**: 任务 2

**核心文件**: `tests/unit/architecture/test_service_lifecycle.py`

**子任务**:
- [x] AST 结构回归检查：12 服务声明 `s_status` / `startup` / `shutdown` 注解齐备
- [x] 契约回归检查：6 态迁移矩阵（放行 `{Idle,Stopped}`；拒绝 `Starting/Running/Stopping/Error`）
- [x] 回滚回归检查：失败调用 `on_error`、回滚失败不掩盖原始异常、`Error→shutdown→startup` 可恢复
- [x] 留痕回归检查：`shutdown` body 抛错时 `fine=False` + `error` 非空
- [x] R4：7 个资源型服务必须挂 `on_error`；`_reset` 未被装饰 + `shutdown` 必须复用它
- [x] 同步分支契约用例

**验收标准**: 回归测试 40 passed；对「漏加注解 / 漏传 on_error / 口径跑偏」能报红。

### 任务 6: 设计与规范落库（#42）

**描述**: 将设计落库到 repo docs，并把 `service_conventions.md` 生命周期章节改为注解口径。

**依赖**: 任务 1–5

**核心文件**:
- `docs/design/2026-10-25-service-lifecycle-annotation/step1_product.md`
- `docs/design/2026-10-25-service-lifecycle-annotation/step2_technical.md`
- `docs/design/2026-10-25-service-lifecycle-annotation/step3_tasks.md`
- `docs/tech/02_mvc/service_conventions.md`
- `docs/design/ROADMAP.md`

**子任务**:
- [x] 新增本设计目录三篇文档
- [x] 更新 `service_conventions.md` 生命周期章节（注解口径 + 修正签名 + 删除旧「setup 用 shutdown 重置」描述）
- [x] ROADMAP 版本概览与相关文档补充索引

**验收标准**: 文档与 v1.6 设计及代码一致，不复述旧口径。

### 任务 7: 二期 backlog

**描述**: 本次范围外的收敛项，待排期。

**依赖**: —

**子任务**:
- [ ] `_rollback_sync` 旁路（同步 startup + 异步 on_error 在事件循环内会跳过回滚）
- [ ] `= {}` / `.clear()` 一致性梳理（messageBus / agentService）
- [ ] `backend_main` 关闭序列补 `presetService` / `teamService` / `roleTemplateService`
- [ ] 测试兜底停机严格只读开关（`TEAMAGENT_TEST_STRICT_LIFECYCLE=1`）

---

## 测试检查清单

- [x] 二次 `startup` 抛 `SERVICE_ALREADY_STARTED`，不执行 body、不改状态
- [x] `shutdown` 幂等；仅 `Idle`/`Stopped` no-op + warning
- [x] `shutdown` body 抛错 → 不抛错、回 `Stopped`、留痕（`fine=False` + `error`）
- [x] `startup` 失败 → 先回滚、置 `Error`、原样 re-raise、无残留
- [x] `on_error` 自身失败不掩盖原始异常
- [x] `Error → shutdown → startup` 可恢复
- [x] `ormService` 二次 `startup` 抛错；`_reset()` 后无悬空绑定
- [x] `schedulerService` 重复 `startup` 不重复订阅
- [x] 12 服务注解 / `s_status` / `on_error`（资源型）齐备
- [x] 全量并行 + 串行通过

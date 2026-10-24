# Service 模块规范

## 基本原则

每个 service 是一个 **Python 模块**，不是类。用模块级私有变量（`_` 前缀）维护状态，通过模块级函数对外暴露接口。

## 文件结构约定

```
imports

s_status（模块级生命周期状态对象）

模块级私有变量（_xxx）

_reset()        ← （资源型服务）纯清理函数：必须定义在 startup() 之前（on_error=_reset 在 import 期求值）
startup()       ← 生命周期：初始化（挂启动注解）
业务函数 ...    ← 核心逻辑
shutdown()      ← 生命周期：清理（挂停止注解）
```

补充约定：

- `startup()` 必须出现在文件中靠前位置，位于所有业务函数和私有辅助函数之前。
- 私有辅助函数（如 `_load_xxx()`、`_import_xxx()`）也视为业务函数的一部分，应放在 `startup()` 之后。
- **例外（资源型服务）**：纯清理函数 `_reset()` 必须在被装饰的 `startup()` **之前**定义——`on_error=_reset` 在 import 期求值，定义在 `startup()` 之后会直接 `NameError`。它不受「私有辅助函数放在 `startup()` 之后」约束。
- `shutdown()` 应尽量靠近文件末尾；仓库现状为部分服务在 `shutdown()` 之后仍保留函数，故不强制「必须是最后一个」——**允许其后仅存在只读 / 无状态访问函数**。

## 生命周期方法

每个 service 必须实现两个生命周期方法，并统一挂**生命周期注解**（`src/service/serviceUtil.py`）：

| 方法 | 位置 | 职责 | 注解 |
|------|------|------|------|
| `startup(...)` | 文件顶部（业务函数之前） | 初始化模块状态，可接收配置参数 | `@service_start_wrapper(s_status)`；有资源者 `@service_start_wrapper(s_status, on_error=_reset)` |
| `shutdown()` | 文件末尾附近（其后仅允许只读/无状态访问函数） | 清理模块状态，无参数 | `@service_stop_wrapper(s_status)` |

模块级需声明状态对象（「是否已启动」的唯一真源）：

```python
from model.coreModel.serviceStatus import ServiceStatusInfo
s_status = ServiceStatusInfo(name="xxx")
```

### 契约（由注解承载，硬严格，不设灰度开关）

- **状态机**：`Idle → Starting → Running → Stopping → Stopped`（启动失败为 `Error`）。
- **只启动一次**：`startup` 允许态 = `{Idle, Stopped}`；`Starting` / `Running` / `Stopping` / `Error` 再 `startup` → 抛 `TogoException(error_code="SERVICE_ALREADY_STARTED")`，**不执行 body、不改写状态**。
- **失败必回滚**：`startup` 中途失败 → 先执行 `on_error` 回滚（best-effort、吞回滚异常），再置 `Error`，并**原样向上抛**（fail loud），保证无半启动残留。
- **停止幂等**：`shutdown` 仅 `Idle`（从未启动）/ `Stopped`（重复关闭）态 no-op + warning；其余态执行清理并回 `Stopped`；body 抛错**不 re-raise**，但留痕（`fine=False` + `error`），使清理失败可观测。
- **恢复路径**：`Error` 态固定走 `shutdown() → startup()`。
- **资源管理**：`startup` 创建（`_x = {}`）、`shutdown` 清空（`_x.clear()`）；**禁止**在 shutdown 内写 `_x = {}`；标量（int/str）重置为初值。
- **`_reset()` 复用**：持有资源/有副作用的服务提供**未被注解装饰**的纯清理函数 `_reset()`（幂等），既作 `on_error`，又被 `shutdown` body 复用（回滚与正常关闭共用一份逻辑，防漂移）。
- **同步/异步兼容**：注解保留被装饰函数的同步/异步形态（如 `skillService.startup` 为同步实现），不改动既有调用点。

> 完整契约、实现与决策见 [Service 生命周期注解化设计](../../design/2026-10-25-service-lifecycle-annotation/step2_technical.md)。

### 各 service 签名

| 模块 | `startup` 签名 | `shutdown` |
|------|----------------|-----------|
| `messageBus` | `async startup()` | async |
| `llmService` | `async startup()` | sync |
| `funcToolService` | `async startup()` | sync |
| `ormService` | `async startup(db_path: str)` | async |
| `persistenceService` | `async startup()` | async |
| `agentService` | `async startup()` | async |
| `roomService` | `async startup()` | sync |
| `schedulerService` | `async startup()` | sync |
| `presetService` | `async startup()` | async |
| `skillService` | `startup()`（同步） | sync |
| `teamService` | `async startup()` | async |
| `roleTemplateService` | `async startup()` | async |

> `teamService` / `roleTemplateService` 为占位服务（也接入注解）；`teamService` 的 `shutdown()` 已于本次补齐。

## 启动 / 关闭顺序（backend_main.py）

`startup` 按依赖顺序调用（阶段 1），`shutdown` 在 `finally` 块中逆序调用：

```python
# 启动（依赖顺序）—— backend_main.py 阶段 1
await messageBus.startup()
await llmService.startup()
await funcToolService.startup()
await ormService.startup(app_config.setting.db_path)
await persistenceService.startup()
await agentService.startup()
await roomService.startup()
await schedulerService.startup()
await presetService.startup()
skillService.startup()

# 关闭（finally 块，逆序）
schedulerService.shutdown()
await agentService.shutdown()
await persistenceService.shutdown()
await ormService.shutdown()
funcToolService.shutdown()
skillService.shutdown()
roomService.shutdown()
llmService.shutdown()
await messageBus.shutdown()
```

> `teamService` / `roleTemplateService` 不在阶段 1 启动序列中（占位服务，按需使用）；记二期补齐其关闭序列。

## 测试中的用法

- 集成测试继承 `tests/base.py::ServiceTestCase`，在 `async_setup_class` 中 `startup`，并在 `async_teardown_class` 中**成对 `shutdown`**（谁启动谁关闭）。
- **不要再在 setup 阶段用 `shutdown()` 重置状态**（旧写法）：严格口径下 `shutdown` 仅 `Idle`/`Stopped` no-op，重复 `startup` 会直接抛错；需要干净状态请在用例前显式 `shutdown()` 再 `startup()`。
- `tests/base.py` 提供兜底停机 `_reset_in_process_services()`（teardown 的 `finally` + setup 异常路径）用于自愈「漏配对」，但**成对仍须由用例保证**；二期将加严格只读开关暴露漏配对。

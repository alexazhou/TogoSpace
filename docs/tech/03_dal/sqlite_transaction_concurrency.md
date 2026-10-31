# SQLite 事务与并发控制

本文说明项目在 SQLite 上的事务模型、并发行为，以及为什么 `atomic()` 的根事务必须使用 `BEGIN IMMEDIATE`。

涉及代码：

- `src/service/ormService.py` — 数据库连接池与 `AioSqliteDatabase`
- `src/model/dbModel/base.py` — 唯一的业务事务入口 `atomic()`
- `src/service/agentService/core.py`、`src/service/taskService.py` — 三处业务事务调用点

---

## 0. 太长不看

1. SQLite 的写锁是**库级文件锁**，全库同时只允许一个写者；没有行锁，也没有并发写。
2. `peewee_async` 的根事务发的是裸 `BEGIN`（= `BEGIN DEFERRED`），在「先读后写」时**不调用 busy handler，立刻返回 `SQLITE_BUSY`**，表现为 `database is locked`。
3. 修复方式：根事务改发 `BEGIN IMMEDIATE`（嵌套仍用 `SAVEPOINT`），落点在 `AioSqliteDatabase` 子类，**不改三方库、不改业务代码**。
4. 改后写操作从「立刻失败」变成「排队等待」，成功路径行为不变。

---

## 1. 三层事务概念（先对齐术语）

项目里同时存在三层"事务"，极易混淆。本文档中固定用法如下：

| 层 | 名字 | 项目中的状态 | 由谁控制 |
|---|---|---|---|
| SQLite 引擎层 | **autocommit 模式**：每条 SQL 语句自成一次完整事务 | 在生效 | SQLite 自身 |
| Python 驱动层 | `sqlite3` 的 **legacy 隐式事务**（驱动自动发 BEGIN/COMMIT） | **已关闭** | `ormService.py:58` 的 `isolation_level=None` |
| 应用层 | **`atomic()`** → `BEGIN` / `SAVEPOINT` | 三处业务在用 | `src/model/dbModel/base.py:40` |

**注意**：`isolation_level=None` 表示"驱动不再替我们发任何 BEGIN/COMMIT"，所以"隐式事务"这一层在项目中**不存在**。不要用"隐式事务"来描述裸写操作，准确说法是「autocommit 下的单条语句事务」。

### 1.1 autocommit 的两个粒度

| 粒度 | 是否原子 | 说明 |
|---|---|---|
| **单条语句内部** | ✅ 原子 | 一条 `INSERT ... SELECT` 插 100 行，中途一行违反约束 → **整条语句全部回滚** |
| **两条语句之间** | ❌ 不原子 | 第 1 条成功、第 2 条失败 → **第 1 条已提交，无法回滚** |

实测（连接参数与 `SqlitePoolBackend.acquire()` 一致，`isolation_level=None`）：

```
① 单条 INSERT 后，另一个连接立刻读到 1 行          → 已自动提交
② 一条 INSERT..SELECT 插两行，第 2 行违反 NOT NULL
   报错 → 落库只有 b1，b2 未留下                    → 语句内部原子 ✅
③ 连做两条 INSERT，第 2 条报错
   落库留下了 c1                                     → 语句之间不原子 ❌
```

**推论**：单条写（`aio_save()`、`aio_create()`、`insert_many()`、`update().where()`、`delete().where()`）**不需要**包 `atomic()`，一条语句天然原子。只有「多条语句必须同生共死」时才需要 `atomic()`。

---

## 2. SQLite 锁模型

### 2.1 回滚日志模式（项目默认，未启用 WAL）

锁按 `SHARED → RESERVED → PENDING → EXCLUSIVE` 逐级升级：

| 锁 | 含义 | 并存规则 |
|---|---|---|
| `SHARED` | 读锁 | 多个连接可同时持有 |
| `RESERVED` | 写意向锁，准备写 | **全库唯一** |
| `PENDING` | 准备提交，挡新读者 | 全库唯一 |
| `EXCLUSIVE` | 提交时短暂持有 | 全库唯一 |

`RESERVED` 是库级的：**拿到的连接在整个数据库文件上独占写入权，与访问哪张表、哪一行无关。**

### 2.2 三种事务模式

| 模式 | 拿锁时机 | 适用 |
|---|---|---|
| `BEGIN DEFERRED`（默认，裸 `BEGIN` 即此） | 第一条语句才拿锁 | 只读场景 |
| `BEGIN IMMEDIATE` | BEGIN 时立刻拿 `RESERVED` | **所有写事务** |
| `BEGIN EXCLUSIVE` | BEGIN 时立刻拿 `EXCLUSIVE`（连读都挡） | 维护类操作 |

### 2.3 为什么「先读后写」会立即失败

延迟事务里「先拿 `SHARED`、再想升级成 `RESERVED`」这个动作，SQLite **不会调用 busy handler，直接返回 `SQLITE_BUSY`**。这是刻意的设计，官方 `sqlite3_busy_handler` 文档原文：

> The presence of a busy handler does not guarantee that it will be invoked when there is lock contention. **If SQLite determines that invoking the busy handler could result in a deadlock, it will go ahead and return SQLITE_BUSY to the application instead of invoking the busy handler.** Consider a scenario where **one process is holding a read lock that it is trying to promote to a reserved lock and a second process is holding a reserved lock that it is trying to promote to an exclusive lock.**

死锁如何形成：

| 时刻 | 连接 A | 连接 B |
|---|---|---|
| t1 | `BEGIN`，未拿锁 | |
| t2 | `SELECT` → 拿 `SHARED` | |
| t3 | | `BEGIN`；`INSERT` → 拿 `RESERVED` |
| t4 | `INSERT` → 想升 `RESERVED`，被 B 占 | |
| t5 | ❓ | B 提交需升 `EXCLUSIVE`，要求无其他 `SHARED` |

A 等 B 放 `RESERVED`，B 等 A 放 `SHARED`。等着也没用，所以 SQLite 直接掀桌。

**关键差异**：`BEGIN IMMEDIATE` 在事务开头、**手里一个锁都没有**时就抢写锁。此时拿不到，等待不会死锁，busy handler 正常生效。

### 2.4 与 MySQL 的对比（澄清常见误解）

MySQL 不会遇到这个问题，**原因不是"可重复读"，而是「普通 SELECT 不加锁」**：

| 步骤 | SQLite（默认模式） | MySQL InnoDB（RR/RC） |
|---|---|---|
| A: `SELECT` | 拿 `SHARED` 读锁 | **不拿锁**，读 undo 快照 |
| B: `INSERT` | 拿 `RESERVED` | 拿行锁，不受 A 影响 |
| A: `INSERT` | 需 `SHARED → RESERVED` **升级** | 直接拿行锁，**无升级动作** |

补充两点：

- MySQL 有**死锁检测器**（`innodb_deadlock_detect`），可以选择 victim 回滚；SQLite 是嵌入式库、连接可能跨进程，无法维护全局等待图，**没有检测器**，所以只能"预判必然死锁 → 不等待"。
- `SERIALIZABLE` 隔离级别下 MySQL 的普通 `SELECT` 会隐式加共享锁，**这时它也会退化到同样的处境**。

---

## 3. 问题：CI 偶发 500

### 3.1 现象

Workflow `Tests` 的 3.10 / 3.11 matrix 偶发红，唯一失败用例：

```
tests/api/test_team_controller/test.py::TestTeamController::test_team_modify_agents_with_role_template_id
assert resp.status == 200  →  实际 500
```

后端日志栈：

```
sqlite3.OperationalError: database is locked
  → peewee_async/databases.py:185 aio_execute_sql
  → src/controller/teamController.py post
  → src/service/agentService/core.py  overwrite_team_agents
  → src/dal/db/gtAgentManager.py      batch_save_agents
  → GtAgent.insert_many(...).aio_execute()
```

### 3.2 根因

`overwrite_team_agents` 的 `async with atomic()` 内是「`batch_update_agent_status` + `batch_save_agents`」，后者内部先做 `get_max_employee_number`（读）、再 `insert_many`（写）。**先读后写 + 裸 `BEGIN`**，正好踩中 2.3 的死锁预判路径。

同时 `insert_many` 之前若另一个连接持写锁 → 立即 `SQLITE_BUSY` → controller 转 500。

### 3.3 两个放大器

1. **`timeout=30` 从未生效**。`peewee.SqliteDatabase.__init__` 把 `timeout` / `pragmas` 自己吃掉（存成 `_timeout` / `_pragmas`），只有同步 `_connect()` 才用；而 `SqlitePoolBackend.acquire()` **自己 `aiosqlite.connect()` 新建连接**，完全不走那条路。实测 `db.connect_params == {}`，`PRAGMA busy_timeout` 仍是默认 `5000`。
2. **测试并发共享同一 db 文件**。`tests/base.py` 每个 worker 起一个后端进程，但多个进程共用同一 `db_path`，且 `setup_class` 用同步 `sqlite3` 对同一文件跑 `migrate_database`。写者数量远超单机场景。

### 3.4 引入时间点

二分定位到 `30e0b2cb`（`feat(workspace): use agent name for private dirs`）——它把该段写操作从「裸 `batch_save_agents`」包进了 `async with atomic()`，从「单条写、无事务」变成「先读后写的延迟事务」。`f00b6295`（迁移挪到入口）只是放大了暴露概率。

---

## 4. 项目现状：事务使用全清单

### 4.1 唯一入口

`src/model/dbModel/base.py:40`

```python
@asynccontextmanager
async def atomic() -> AsyncIterator[None]:
    async with _database_proxy.aio_atomic():
        yield
```

全项目**唯一**的异步事务封装，所有业务事务都经它。

### 4.2 `src/` 调用点（3 处业务）

| # | 位置 | 事务内做什么 | 事务外关键动作 |
|---|---|---|---|
| 1 | `base.py:51` | 封装本身 | — |
| 2 | `agentService/core.py:472` | `batch_update_agent_status`（离职）+ `batch_save_agents` | 事务前改名目录；`IntegrityError` 时回滚目录 |
| 3 | `taskService.py:169` | `_transition_with_event`：`save_task(status)` + `append_event` | 事务后 `_publish_event_appended` |
| 4 | `taskService.py:301` | `create_task`：`save_task(force_insert)` + `append_event` | 事务后 publish |

第 3 处是热点，3 个上游调用：

- `taskService.py:197` `auto_progress_dependents`（依赖满足自动推进，**循环内逐个任务开事务**）
- `taskService.py:221` `claim_task`（认领置位）
- `taskService.py:456` `update_task`（公开接口：状态流转 / 追加评论）

覆盖的业务线：**改任务状态、建任务、改成员、认领、自动推进**。

### 4.3 隐藏调用点

`peewee_async/aio_model.py:394` 的 `aio_get_or_create` 内部走 `aio_atomic()`。项目当前**未使用**，但改后会一并变 IMMEDIATE（其本身就是「事务外 SELECT → 事务内 CREATE」，改后更安全）。

### 4.4 测试中的事务

| 位置 | 用途 |
|---|---|
| `tests/integration/task_helpers.py:20, :41` | 造「任务+事件」，走**真** `atomic()` |
| `tests/unit/service/test_task_service.py:46` | `_passthrough_atomic` **替身**顶掉 |
| `tests/unit/service/agentService/test_workdir_rename.py:58` | 同上 |

**注意**：两个单测把 `atomic` 换成不加锁的穿透实现，因此**测不到** `BEGIN IMMEDIATE` 行为。要验证新行为必须写在 `tests/integration/`（走真 `atomic`）。

### 4.5 不在事务内的写操作

约 80 处单条写，走 autocommit 的单语句事务，**天然原子、不需要改**。

但有 **3 处多步写没包事务**，是独立隐患（不在本次修复范围）：

| 位置 | 问题 |
|---|---|
| `agentService/core.py:504-505` `overwrite_team_agent_employ_status` | 两次独立 `batch_update_agent_status`，中途失败 → 在岗/离岗状态不一致 |
| `roomService/core.py:399` `batch_save_rooms` | `for room in rooms: await save_room(room)`，N 个独立事务，失败留半截 |
| `funcToolService/tools.py:621` `save_agent` 工具 | 目录改名 + 单条写，有补偿无事务 |

### 4.6 完全不受影响：迁移

`src/db.py` 使用**另一套同步 sqlite3**（`connect_sqlite()` → `sqlite3.connect` + `with conn:`），走 peewee 同步路径，与 `peewee_async` 无关。`BEGIN IMMEDIATE` 补丁碰不到它。

（但迁移仍是测试锁竞争来源之一：`setup_class` 用同步 sqlite3 对同一文件跑 `migrate_database`，与后端进程并发。）

---

## 5. 为什么改不了配置：`peewee_async` 没有官方开关

### 5.1 peewee 同步层有官方 API

`peewee.py:3838`

```python
def begin(self, lock_type=None):
    statement = 'BEGIN %s' % lock_type if lock_type else 'BEGIN'
```

用法：`with db.atomic('IMMEDIATE'):`（实测发出 `BEGIN IMMEDIATE`）。

### 5.2 `peewee_async` 没有，且上游最新版也没有

| 位置 | 现状 |
|---|---|
| `peewee_async/transactions.py:24-28` | `begin()` 写死 `sql = "BEGIN"`，**无 lock_type 参数** |
| `peewee_async/databases.py:100` | `def aio_atomic(self)` —— **不收任何参数** |
| `peewee_async/databases.py:126` | `Transaction(connection, is_savepoint=_is_nested)`，类名硬编码 |

上游 `05bit/peewee-async`：最新 tag `v2.1.0` 与 master 分支这两处**一字未改**；issue 搜索 `BEGIN IMMEDIATE` / `busy_timeout` / `lock_type` 均 **0 条相关**。项目安装版本为 `1.2.1`（`peewee` 3.19.0）。

**根因是架构性的**：`_aio_atomic` 完全绕开 peewee 的事务机制——不调 `db.transaction()`、不调 `db.begin()`，而是自己造 `Transaction` 类直接发 SQL。**因此在 peewee 层配置的一切，异步路径一律收不到。**

---

## 6. 方案

### 6.1 落点：`AioSqliteDatabase` 子类（非 monkeypatch）

`src/service/ormService.py`：

```python
class _ImmediateTransaction(Transaction):
    """根事务发 BEGIN IMMEDIATE；嵌套事务仍走 SAVEPOINT。"""

    async def begin(self) -> None:
        if self.savepoint:
            await super().begin()
        else:
            await self.execute("BEGIN IMMEDIATE")


class AioSqliteDatabase(AioDatabase, peewee.SqliteDatabase):
    pool_backend_cls = SqlitePoolBackend

    @contextlib.asynccontextmanager
    async def _aio_atomic(self, use_savepoint: bool = False):
        async with self.aio_connection() as connection:
            ctx = connection_context.get()
            assert ctx is not None
            is_root = not ctx.transaction_is_opened
            if not is_root and not use_savepoint:
                raise peewee.OperationalError("Transaction already opened")
            try:
                async with _ImmediateTransaction(connection, is_savepoint=not is_root):
                    if is_root:
                        ctx.transaction_is_opened = True
                    yield
            finally:
                if is_root:
                    ctx.transaction_is_opened = False
```

**为什么不用 monkeypatch**：`peewee_async.transactions.Transaction.begin = xxx` 会全局污染、影响同进程内所有 `peewee_async` 用户、不便测试。子类只是"自己的 Database 替换自己这一层的 protected 实现"，三方库文件零改动、无 `sys.modules` 操作。

**为什么必须自己发 SQL**：`BEGIN IMMEDIATE` 本质就是一条 SQL，绕不过；差别只在写进一个 4 行方法（所有调用方自动受益）vs 散落在每个业务函数里。

### 6.2 配套：busy timeout 用官方参数（`pool_params`）

实测：

| 写法 | `connect_params` | 实际 `PRAGMA busy_timeout` |
|---|---|---|
| `AioSqliteDatabase(path, timeout=30)` | `{}` | 5000 ← **被 peewee 吃掉** |
| `AioSqliteDatabase(path, pragmas=(("busy_timeout", 30000),))` | `{}` | 5000 ← **无效** |
| `AioSqliteDatabase(path, pool_params={"timeout": 30})` | `{"timeout": 30}` | **30000 ✅** |

通道：`init_pool_params()`（`databases.py:54-69`）把 `connect_params.pop('pool_params', {})` 合并进 `self.pool_params` → `PoolBackend.__init__` → `connect_params` → `acquire()` 的 `aiosqlite.connect(db, **connect_params)`。

⚠️ `pragmas=` 无效：`peewee.SqliteDatabase` 只在同步 `_connect()` 里应用 `_pragmas`，异步池自己建连不走那条路。网上"给 peewee 加 `pragmas={'busy_timeout': ...}`"的答案对本项目**无效**。

`pool_params={"timeout": 30}` 确实生效（holder 持写锁 0.5s，另一连接纯等锁）：

```
pool_params={'timeout': 0.2}  -> FAIL 于 0.50s（等 0.2s 放弃）
pool_params={'timeout': 30}   -> OK   于 0.57s（等到 holder 释放）
```

### 6.3 改动清单

| 改动 | 位置 | 性质 |
|---|---|---|
| `_ImmediateTransaction` + 覆写 `_aio_atomic` | `ormService.py`，`AioSqliteDatabase` 类内 | 自己的子类 |
| `pool_params={"timeout": 30}` | `ormService.py:115` 构造函数 | 官方参数 |
| `timeout=30` 加注释说明是死参数 | 同上 | 防误读 |

**业务代码、`base.py` 的 `atomic()`、三处调用点、DAL、Controller 全部零改动。**

---

## 7. 验证结果

环境：`1e5071c9`（master），独立脚本，**单进程、无并发干扰**。

### 7.1 事务语义（4 个场景全过）

```
1) 正常提交 + 嵌套
   SQL: BEGIN IMMEDIATE → SAVEPOINT → RELEASE SAVEPOINT → COMMIT
   落库: r1 r2 r3                                        ✅

2) 嵌套内抛异常
   SQL: BEGIN IMMEDIATE → SAVEPOINT → ROLLBACK TO SAVEPOINT → COMMIT
   落库: o1 o2（bad 未落库）                              ✅ 只回滚到 savepoint

3) 外层抛异常
   SQL: BEGIN IMMEDIATE → ROLLBACK                        ✅ 整体回滚

4) aio_transaction 嵌套 → OperationalError "Transaction already opened"  ✅ 原语义保留
```

### 7.2 竞争场景（先读后写，即 `overwrite_team_agents` 形态）

```
原版（裸 BEGIN）        -> FAIL database is locked (0.001s)
BEGIN IMMEDIATE 版      -> OK   (0.213s)   ← 等对方提交后成功
```

### 7.3 并发压测（N 个并发「先读后写」事务）

| 并发数 | 原版 DEFERRED | BEGIN IMMEDIATE |
|---|---|---|
| 2 | 成功 1/2，`database is locked` | 成功 2/2 |
| 4 | 成功 1/4 | 成功 4/4（0.01s） |
| 8 | 成功 1/8 | 成功 8/8（0.10s） |

原版是 **1/N 成功**：第一个抢到锁的赢，其余全部立即失败。

### 7.4 排队代价

单次事务持锁时长（3 条 INSERT，200 次采样）：

```
p50 = 0.847ms   p99 = 1.293ms   max = 1.356ms
```

16 个并发写事务全部排队跑完 **0.48s**。

### 7.5 对其他操作的影响

A 持有 `BEGIN IMMEDIATE` 写锁期间，B 的反应：

| B 的操作 | 是否被挡 | 用时 |
|---|---|---|
| `SELECT`（纯读） | **不被挡** | 0.00s |
| `INSERT`（单条写） | 被挡 | 0.47s（等 A 提交后成功） |
| `UPDATE`（单条写） | 被挡 | 0.46s（等 A 提交后成功） |

**B 不会因为锁升级而立即失败**——B 是纯写、无读，等待点可等，busy handler 正常生效：

```
A 持锁 0.5s / B 等待上限 0.2s  ->  FAIL database is locked (0.51s)   ← 等到上限才放弃
A 持锁 0.5s / B 等待上限 5s    ->  OK   (0.59s)
```

### 7.6 对照：排队现象改之前就有

```
原版 DEFERRED、B 纯写另一张表（一次读都没做）：B 被挡=True，等到 0.36s
```

**写锁是库级的，SQLite 从来是"单写者排队"。** `BEGIN IMMEDIATE` 没有新增排队，只是把「排不上队就立刻失败」改成「排不上队就老实排队」。

---

## 8. 改动后的行为对照

| 场景 | 改之前 | 改之后 |
|---|---|---|
| 两人同时改任务状态 | 1 个成功、1 个**立即 500** | 都成功，后者排队几十毫秒 |
| 改任务时有人单条写 Agent | 先读后写者**可能立即 500** | 排队，正常成功 |
| 改任务时有人读 | 正常 | 正常（**无变化**） |
| 有人持锁超过 `busy_timeout` | 失败 | 失败（**一样**） |

唯一副作用是**延迟**，不是**失败**。

---

## 9. 纪律与风险

### 9.1 `atomic()` 块内禁止耗时操作（重要）

`BEGIN IMMEDIATE` 把拿写锁提到事务开头，事务期间**全库所有写者排队**。因此：

> **`atomic()` 块内禁止任何网络请求、LLM 调用、文件 IO、`asyncio.sleep`。**

当前三处调用点均为纯 SQL（毫秒级），安全。但若有人往事务里塞 HTTP 请求，全库写操作会被卡住直到 `busy_timeout` 超时。**这条应写进代码注释。**

### 9.2 自死锁（结构上已避免）

同一协程若用**两个不同连接**各开一个 `BEGIN IMMEDIATE`，会互相永久等待（SQLite 无死锁检测）。项目使用单连接 + savepoint 嵌套，走不到此路径。

### 9.3 `busy_timeout` 是硬上限

所有写者排队，但持锁时间超过别人 `busy_timeout` 时对方仍会失败。这是 `pool_params={"timeout": 30}` 值得一起上的原因——把上限从默认 5s 拉高 6 倍。

### 9.4 不要手写 `BEGIN`

若某段代码自己 `execute("BEGIN")` 再进 `atomic()`，会撞 "cannot start a transaction within a transaction"。

### 9.5 未来升级 Python 3.12+

3.12 起 `sqlite3.connect()` 新增 `autocommit` 参数（PEP 249 对齐）。当前 3.11.8 无此属性（`hasattr(sqlite3.Connection, 'autocommit') == False`），CI 的 3.10/3.11 matrix 不受影响。升 3.12+ 时 `isolation_level` 与 `autocommit` 互斥报错，`ormService.py:58` 需同步调整。

---

## 10. 待跟进

| 事项 | 说明 |
|---|---|
| **3 处多步裸写补事务** | 见 4.5，比锁问题更值得单独跟进 |
| **回归测试** | 建议 `tests/integration/` 补：断言根事务发 `BEGIN IMMEDIATE`、嵌套发 `SAVEPOINT`、N 个并发先读后写事务全部成功 |
| **上游 PR** | 给 `aio_atomic(lock_type=None)` 加参数（照抄 peewee 同步层 `begin(lock_type)` 设计），约 10 行。上游两年未动该模块，合并时间不可控，**不能作为交付路径** |
| **WAL 模式** | 可让读者不阻塞写者，但「先开快照读、后想写」会拿到 `SQLITE_BUSY_SNAPSHOT`（同样立即返回、同样不调 busy handler）。且 `journal_mode` 是写入库文件的持久属性、多出 `-wal`/`-shm` 文件、影响 `backup_database()` 一致性语义、需检查点。**不建议顺手开**，留作独立议题 |

---

## 11. 参考

- SQLite `sqlite3_busy_handler`（本文引用的死锁原文）：https://sqlite.org/c3ref/busy_handler.html
- SQLite 官方论坛「timeout not obeyed」→ 维护者回复"符合设计"：https://www.sqlite.org/forum/forumpost/116d22cc59
- SQLite 事务语法（三种 BEGIN 的锁时机）：https://sqlite.org/lang_transaction.html
- SQLite WAL 模式：https://www.sqlite.org/wal.html

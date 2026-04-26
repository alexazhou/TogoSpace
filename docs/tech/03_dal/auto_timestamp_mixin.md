# 自动时间字段注入（AutoTimestampMixin）

本文说明数据库模型层的自动时间字段策略，以及 `AutoTimestampMixin` 的设计与实现逻辑。

---

## 背景与目标

项目中所有数据库模型都继承自 `DbModelBase`，并包含两个公共时间字段：

- `created_at`
- `updated_at`

我们希望实现以下统一约束：

1. `insert(...)` 默认自动补齐 `created_at/updated_at`，业务层不手动传。
2. `update(...)` 默认自动补齐 `updated_at`，业务层不手动传。
3. `insert(...).on_conflict(update={...})`（upsert）在冲突更新时默认自动补齐 `updated_at`。
4. 若调用方显式提供时间字段，则尊重显式值，不覆盖。

其中第 3 点是最容易遗漏的场景，因为它不是直接走 `update(...)`，而是走 `insert` 链式 `on_conflict(update=...)`。

---

## 代码位置

- Mixin：`src/model/dbModel/auto_timestamp_mixin.py`
- 基类：`src/model/dbModel/base.py`
- 典型调用方：
  - `src/dal/db/gtDeptManager.py`
  - `src/dal/db/gtRoleTemplateManager.py`
  - `src/dal/db/gtSystemConfigManager.py`

`DbModelBase` 通过多继承接入该能力：

```python
class DbModelBase(AutoTimestampMixin, peewee_async.AioModel):
    ...
```

---

## 三种写法的最终行为

### 1) 普通 insert

```python
GtXxx.insert(name="n").aio_execute()
```

行为：

- 自动注入 `created_at = now`
- 自动注入 `updated_at = now`

若显式传入：

```python
GtXxx.insert(name="n", created_at=old, updated_at=old)
```

则保留显式值，不覆盖。

### 2) 普通 update

```python
GtXxx.update(name="n2").where(...).aio_execute()
```

行为：

- 自动注入 `updated_at = now`

若显式传入 `updated_at`，则保留显式值。

### 3) upsert（insert + on_conflict）

```python
GtXxx.insert(...)
  .on_conflict(
      conflict_target=[...],
      update={GtXxx.name: "n2"},
  )
  .aio_execute()
```

行为：

- 在 `update={...}` 中自动补上 `GtXxx.updated_at: now`

若已显式写入：

```python
update={GtXxx.name: "n2", GtXxx.updated_at: explicit_ts}
```

则保留显式值，不覆盖。

---

## 实现设计

### 1. 时间字段 key 识别（避免重复注入）

Peewee 中更新字典的 key 可能有两种写法：

- 字符串 key：`"updated_at"`
- 字段对象 key：`GtXxx.updated_at`

`_has_timestamp_key(payload, field_name)` 同时识别两种形式，避免把“已传入字段对象 key”的情况误判为“未传”，从而重复注入。

### 2. 普通 insert/update 的注入

- `_inject_insert_timestamps(...)`
  - 未提供 `created_at` 时注入
  - 未提供 `updated_at` 时注入
- `_inject_updated_at(...)`
  - 未提供 `updated_at` 时注入

注入时根据 payload 是否使用字段对象 key 决定写入格式，保持风格一致。

### 3. on_conflict(update=...) 的自动注入

关键逻辑在 `_patch_insert_query_for_conflict_timestamp(query)`：

1. 拦截 `insert()` 返回的 query。
2. 包装 query 的 `on_conflict(...)`。
3. 当检测到 `kwargs["update"]` 为映射对象时，先对该 `update` 字典执行 `_inject_updated_at(..., use_field_keys=True)`。
4. 再调用原始 `on_conflict(...)`。

由于 Peewee 链式调用会 `clone()` 生成新 query，mixin 同时包装了 `clone()`，保证如下链路仍生效：

```python
GtXxx.insert(...).returning(...).on_conflict(update={...})
```

---

## 方法清单（AutoTimestampMixin）

- `_now()`
  - 返回当前时间，统一时间源。
- `_has_timestamp_key(payload, field_name)`
  - 判断 payload 是否已包含目标时间字段（兼容 str/Field key）。
- `_uses_field_keys(payload)`
  - 判断 payload 是否以 Peewee 字段对象作为 key。
- `_inject_insert_timestamps(payload)`
  - insert 场景注入 `created_at/updated_at`。
- `_inject_updated_at(payload, use_field_keys=None)`
  - update/upsert 更新字典注入 `updated_at`。
- `_patch_insert_query_for_conflict_timestamp(query)`
  - 为 insert query 注入 on_conflict 自动更新时间能力。
- `insert(...)`
  - 注入 insert 时间并 patch query。
- `insert_many(...)`
  - 批量注入 insert 时间并 patch query。
- `update(...)`
  - 注入 `updated_at`。

---

## 使用建议

1. 业务层默认不要手动传 `updated_at`。
2. 仅在确有业务语义（如历史回放、数据修复）时显式传时间字段。
3. `on_conflict(update={...})` 场景无需再手写 `GtXxx.updated_at: GtXxx._now()`。
4. 若后续新增 `DbModelBase` 子类，可自动获得该能力，无需额外接入。

---

## 兼容性与边界说明

1. 该能力只作用于继承 `DbModelBase`（即继承 `AutoTimestampMixin`）的模型。
2. 若绕过模型层直接写原生 SQL，不会自动注入时间字段。
3. 若调用方显式传入 `updated_at`，框架不会覆盖显式值。
4. 当前策略使用应用侧 `datetime.now()`；若未来需改为数据库时间函数，可统一在 `_now()` 或注入层升级。

---

## 已有测试覆盖

- `tests/integration/test_dal_manager/test.py::TestDalManagers::test_db_model_insert_on_conflict_auto_injects_updated_at`
  - 验证 upsert 自动注入 `updated_at`
  - 验证显式 `updated_at` 不被覆盖

同时有相关 manager 的 upsert 场景回归测试，确保业务行为保持一致。

# DAL 模块规范

## 基本原则

DAL（Data Access Layer）负责数据库访问，每个 Manager 是一个 **Python 模块**（不是类），通过模块级函数对外暴露接口。

DAL 层只负责数据持久化，不包含业务逻辑。业务逻辑应由 Service 层处理。

## 对象类型边界

DAL Manager 的输入输出对象**必须**使用对应的 `GtXxx` 数据库对象：

| 方向 | 允许的类型 | 禁止的类型 |
|------|-----------|-----------|
| 输入（对象参数） | `GtXxx` 及其子类 | `GtCoreXxx`、`Any`、`dict` 等 |
| 输出（返回值） | `GtXxx`、`GtXxx \| None`、`list[GtXxx]` | 其他领域对象、`Any` |

示例：

```python
# ✓ 正确：输入输出均为 GtAgent
async def batch_save_agents(team_id: int, agents: list[GtAgent]) -> None:
    ...

async def get_agent(team_id: int, name: str) -> GtAgent | None:
    ...

# ✗ 错误：接受其他类型对象
async def save_agent(agent: GtCoreAgent) -> None:  # 应为 GtAgent
    ...

async def update_agent(agent: Any) -> None:  # 类型不明确
    ...
```

转换责任：
- **Service 层**负责将业务对象（如 `GtCoreAgent`）转换为数据库对象（`GtAgent`）后再调用 DAL
- **Service 层**负责将 DAL 返回的 `GtXxx` 转换为业务对象供上层使用
- DAL 层不做类型转换，保持纯粹的数据库操作

## 文件结构约定

```
src/dal/
├── __init__.py          ← 包标记（不做 Manager 聚合导出）
└── db/
    ├── __init__.py      ← 包标记（不做 Manager 聚合导出）
    └── gtXxxManager.py  ← 对应 model.dbModel.gtXxx
```

每个 Manager 文件的结构：

```
imports（from __future__ import annotations 放在最前）

私有辅助函数（_xxx）

公共接口函数（按功能分组）
```

## 文件命名

| Model 文件 | DAL Manager 文件 | 数据表名 |
|-----------|-----------------|---------|
| `model/dbModel/gtAgent.py` | `dal/db/gtAgentManager.py` | `agents` |
| `model/dbModel/gtTeam.py` | `dal/db/gtTeamManager.py` | `teams` |
| `model/dbModel/gtRoom.py` | `dal/db/gtRoomManager.py` | `rooms` |

命名规则：
- Model 类名：`GtXxx`（大驼峰）
- Manager 文件：`gtXxxManager.py`（小驼峰）
- 数据表名：由 Model 的 `Meta.table_name` 定义

## 函数命名规范

### 查询函数

| 函数名模式 | 返回类型 | 说明 |
|-----------|---------|------|
| `get_xxx(id)` | `GtXxx \| None` | 按 ID 查单条 |
| `get_xxx_by_yyy(team_id, name)` | `GtXxx \| None` | 按业务字段查单条 |
| `get_xxxs_by_yyy(team_id)` | `list[GtXxx]` | 按条件查多条 |
| `get_all_xxxs()` | `list[GtXxx]` | 查全部 |
| `xxx_exists(name)` | `bool` | 存在性检查 |

### 写入函数

| 函数名模式 | 返回类型 | 说明 |
|-----------|---------|------|
| `upsert_xxx(...)` | `GtXxx` | 插入或更新，返回最终行 |
| `update_xxx(id, ...)` | `GtXxx` | 按字段更新，返回更新后行 |
| `batch_save_xxxs(...)` | `None` | 批量保存（有 id 更新，无 id 插入） |
| `batch_update_xxx_status(...)` | `None` | 批量更新指定字段 |
| `delete_xxx(id)` | `None \| bool` | 删除指定记录 |

## 查询模式

### 单条查询

```python
async def get_agent(team_id: int, name: str) -> GtAgent | None:
    return await GtAgent.aio_get_or_none(
        (GtAgent.team_id == team_id) & (GtAgent.name == name)
    )
```

### 多条查询

```python
async def get_agents_by_team(team_id: int) -> list[GtAgent]:
    return list(
        await GtAgent.select()
        .where(GtAgent.team_id == team_id)
        .order_by(GtAgent.name)
        .aio_execute()
    )
```

### 按 ID 列表批量查询

```python
async def get_agents_by_ids(agent_ids: list[int]) -> list[GtAgent]:
    if not agent_ids:
        return []
    return list(
        await GtAgent.select()
        .where(GtAgent.id.in_(agent_ids))  # type: ignore[attr-defined]
        .aio_execute()
    )
```

注意：`in_()` 方法需要 `# type: ignore[attr-defined]` 注释以消除类型检查警告。

## Upsert 模式

使用 `insert().on_conflict()` 实现插入或更新：

```python
async def upsert_role_template(
    template_name: str,
    model: str | None,
    soul: str = "",
    ...
) -> GtRoleTemplate:
    await (
        GtRoleTemplate.insert(
            template_name=template_name,
            model=model,
            soul=soul,
            ...
        )
        .on_conflict(
            conflict_target=[GtRoleTemplate.template_name],  # 冲突检测字段
            update={
                GtRoleTemplate.model: model,
                GtRoleTemplate.soul: soul,
                GtRoleTemplate.updated_at: GtRoleTemplate._now(),
            },
        )
        .aio_execute()
    )

    # upsert 后重新查询返回
    row = await get_role_template_by_name(template_name)
    if row is None:
        raise RuntimeError(f"role template upsert failed: {template_name}")
    return row
```

要点：
- `conflict_target` 指定唯一索引字段
- `update` 字典中必须包含 `updated_at: GtXxx._now()`
- 执行后需重新查询返回最新行

## 更新模式

### 按字段更新

```python
async def update_agent(
    agent_id: int,
    name: str,
    role_template_id: int,
    model: str,
    driver: DriverType
) -> GtAgent:
    agent = await GtAgent.aio_get_or_none(GtAgent.id == agent_id)
    if agent is None:
        raise ValueError(f"Agent ID '{agent_id}' not found")

    agent.name = name
    agent.role_template_id = role_template_id
    agent.model = model
    agent.driver = driver
    await agent.aio_save()

    return agent
```

### 批量更新

```python
async def batch_update_agent_status(agent_ids: list[int], status: EmployStatus) -> None:
    if not agent_ids:
        return
    await GtAgent.update(employ_status=status).where(
        GtAgent.id.in_(agent_ids)  # type: ignore[attr-defined]
    ).aio_execute()
```

要点：
- 空列表检查避免无效查询
- `GtXxx.update()` 自动注入 `updated_at`（由 `DbModelBase` 处理）

## 删除模式

```python
async def delete_room(room_id: int) -> None:
    await GtRoom.delete().where(GtRoom.id == room_id).aio_execute()

async def delete_role_template(template_id: int) -> bool:
    deleted = await (
        GtRoleTemplate.delete()
        .where(GtRoleTemplate.id == template_id)
        .aio_execute()
    )
    return bool(deleted)  # 返回是否删除成功
```

## 批量保存模式

区分「有 ID 更新」和「无 ID 插入」：

```python
async def batch_save_agents(team_id: int, agents: list[GtAgent]) -> None:
    if not agents:
        return

    to_create = []
    to_update = []

    for agent in agents:
        if agent.id is not None:
            to_update.append(agent)
        else:
            agent.team_id = team_id
            to_create.append(agent)

    if to_create:
        await GtAgent.bulk_create(to_create)

    for agent in to_update:
        await agent.aio_save()
```

## 返回值规范

| 场景 | 返回值 |
|------|--------|
| 查询单条可能不存在 | `GtXxx \| None` |
| 查询单条必须存在 | `GtXxx`（不存在时抛异常） |
| 查询多条 | `list[GtXxx]`（空列表而非 None） |
| Upsert | `GtXxx`（返回最终行） |
| 删除 | `None` 或 `bool`（表示是否成功） |
| 批量操作 | `None` |

## 异步约定

所有数据库操作都是异步的：

| 同步方法 | 异步方法 |
|---------|---------|
| `Model.get()` | `Model.aio_get()` |
| `Model.get_or_none()` | `Model.aio_get_or_none()` |
| `Model.create()` | `Model.aio_create()` |
| `model.save()` | `model.aio_save()` |
| `query.execute()` | `query.aio_execute()` |

所有 DAL 函数必须是 `async def`。

## 跨 Manager 调用

DAL Manager 可以调用其他 Manager（同层依赖）：

```python
# gtTeamManager.py
from . import gtAgentManager, gtRoleTemplateManager

async def get_team_config(name: str) -> TeamConfig | None:
    team = await get_team(name)
    if team is None:
        return None

    agent_rows = await gtAgentManager.get_agents_by_team(team.id)
    template_rows = await gtRoleTemplateManager.get_role_templates_by_ids(
        [agent.role_template_id for agent in agent_rows]
    )
    ...
```

禁止反向依赖上层（Service、Controller）。

## 导出规范

不在 `src/dal/__init__.py` 或 `src/dal/db/__init__.py` 做 Manager 聚合导出。

```python
from dal.db import gtAgentManager, gtTeamManager
```

Service/Controller 层统一通过 `from dal.db import gtXxxManager` 引用。

## 类型标注

- 所有函数必须有完整类型标注
- 使用 `from __future__ import annotations` 支持现代类型语法
- 返回类型使用 `|` 而非 `Union`
- 列表使用 `list[T]` 而非 `List[T]`

## 错误处理

| 场景 | 处理方式 |
|------|---------|
| 查询可能不存在 | 返回 `None`，由调用方判断 |
| 必须存在但未找到 | 抛 `ValueError` 或 `RuntimeError` |
| Upsert 后查询失败 | 抛 `RuntimeError` |

```python
# 可能不存在 → 返回 None
async def get_agent(team_id: int, name: str) -> GtAgent | None:
    return await GtAgent.aio_get_or_none(...)

# 必须存在 → 抛异常
async def update_agent(agent_id: int, ...) -> GtAgent:
    agent = await GtAgent.aio_get_or_none(GtAgent.id == agent_id)
    if agent is None:
        raise ValueError(f"Agent ID '{agent_id}' not found")
    ...
```

## 依赖关系

```
┌─────────────────┐
│   Controller    │
└───────┬─────────┘
        │
┌───────▼─────────┐
│    Service      │
└───────┬─────────┘
        │
┌───────▼─────────┐
│      DAL        │  ← 可引用同层其他 Manager
└───────┬─────────┘
        │
┌───────▼─────────┐
│     Model       │  ← dbModel 定义数据结构
└─────────────────┘
```

DAL 层可 import：`model` + `util` + 标准库 + 第三方（peewee 等）

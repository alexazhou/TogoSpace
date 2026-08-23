# Role 管理工具开发计划

> 2026-05-11 | 小马哥 | 计划中

---

## 一、背景

角色模板（Role Template）控制 Agent 工具可见性（`allowed_tools`）。当前底层已完备，但 Agent 无法通过工具操作角色模板。

| 层 | 状态 |
|---|---|
| 模型 `gtRoleTemplate.py` | ✅ |
| DAL `gtRoleTemplateManager.py`（完整 CRUD） | ✅ |
| Service `roleTemplateService.py`（`4425879` 已修复全字段 upsert） | ✅ |
| Agent 工具 `tools.py` | ❌ 无 |

---

## 二、目标

在 `tools.py` 新增 4 个工具：列表、详情、创建/更新、删除。

---

## 三、工具设计

### 3.1 `get_role_templates`

```python
async def get_role_templates(_context: ToolCallContext = None) -> dict:
```

- 调用 `get_all_role_templates()`，列表不含 `soul`（太长），从 `i18n` 提取 `display_name`
- 全局资源，不受 team 上下文限制

### 3.2 `get_role_template`

```python
async def get_role_template(role_name: str, _context: ToolCallContext = None) -> dict:
```

- 调用 `get_role_template_by_name()`，返回完整字段含 `soul`

### 3.3 `save_role_template`

```python
async def save_role_template(
    name: str, type: str, soul: str, allowed_tools: list[str],
    model: str | None = None, i18n: dict | None = None,
    _context: ToolCallContext = None
) -> dict:
```

- 校验 `type` ∈ `{"SYSTEM", "USER"}`；按 name upsert 全字段；委托 `roleTemplateService.save_role_template()`

### 3.4 `delete_role_template`

```python
async def delete_role_template(role_name: str, _context: ToolCallContext = None) -> dict:
```

- 先查模板 ID → **查 `gtAgent.role_template_id` 引用**，若被使用则拒绝并返回 Agent 名单
- 未引用则调用 `delete_role_template()`

---

## 四、REGISTRY

```python
"get_role_templates": get_role_templates,
"get_role_template": get_role_template,
"save_role_template": save_role_template,
"delete_role_template": delete_role_template,
```

---

## 五、安全约束

| 约束 | 处理 |
|------|------|
| `type` 枚举 | 仅允许 `SYSTEM` / `USER` |
| 删除保护 | 工具层强制检查引用，被使用则拒绝 |
| 权限隔离 | 依赖 `allowed_tools`，无工具的 Agent 不可调用 |

---

## 六、测试

`tests/test_role_tools.py`：列表/详情/新建/更新/删除/删除不存在/删除正在使用（拒绝）/非法 type。确保现有 707 测试通过。

---

## 七、文件变更

仅 `src/service/funcToolService/tools.py`，DAL/Service 无需改动。纯新增代码，回滚只需移除 REGISTRY 条目。

---

## 八、后续

阶段 2：Agent 和部门管理写入工具（`add_agent_to_dept` / `remove_agent_from_dept` / `update_agent_role`）。

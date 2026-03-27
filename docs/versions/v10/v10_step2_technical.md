# V10: 组织树与部门管理 - 技术文档

## 1. 架构概览

V10 在 V7 Team 扁平成员列表的基础上，引入层级部门树（`dept_tree`）和完整成员运行配置（`members`）。核心改动集中在两个方向：

- **配置层**：`src/util/configTypes.py` 中的 `TeamConfig` / `AgentConfig` 新增 `dept_tree`、`model`、`driver` 字段
- **持久层**：新增 `depts` 表存储扁平化的部门树，含 `member_ids`（JSON 数组）和 `manager_id` 外键；`team_members` 表仅新增成员状态和运行参数字段

运行时的 Agent 调度逻辑**不变**；部门信息仅影响 Agent 的 system prompt 注入和 API/前端的展示，不引入新的调度路径。

---

## 2. 配置结构变更

### 2.1 新增 `DeptNodeConfig`（`src/util/configTypes.py`）

`dept_tree` 为递归嵌套结构，新增 Pydantic 模型解析：

```python
class DeptNodeConfig(BaseModel):
    dept_name: str
    dept_responsibility: str = ""
    manager: str                          # 必填，部门主管成员名
    members: List[str] = Field(default_factory=list)   # 本部门成员名单（含主管）
    children: List["DeptNodeConfig"] = Field(default_factory=list)

DeptNodeConfig.model_rebuild()
```

约束：

- `manager` 必须出现在 `members` 中，否则启动时报错
- 每个节点的 `manager` 不得为空（部门不允许无主管）

### 2.2 扩展 `AgentConfig`（`src/util/configTypes.py`）

```python
class AgentConfig(BaseModel):
    name: str           # 成员在团队内的昵称
    role_template: str  # RoleTemplate 名称
    model: Optional[str] = None                        # 覆盖 AgentTemplate 中的 model
    driver: dict[str, Any] = Field(default_factory=dict)  # 覆盖 AgentTemplate 中的 driver
```

> `driver` 字段格式与现有 `AgentTemplate.driver` 相同，`type` 可为 `native` / `claude_sdk` / `tsp`。

### 2.3 扩展 `TeamConfig`（`src/util/configTypes.py`）

```python
class TeamConfig(BaseModel):
    name: str
    ...
    members: List[AgentConfig] = Field(default_factory=list)
    dept_tree: Optional[DeptNodeConfig] = None   # 新增，可选；未配置时沿用扁平模式
    preset_rooms: List[TeamRoomConfig] = Field(default_factory=list)
    ...
```

---

## 3. 数据库设计

### 3.1 新增 `depts` 表

```sql
CREATE TABLE depts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id        INTEGER NOT NULL REFERENCES teams(id),
    name           TEXT    NOT NULL,
    responsibility TEXT    DEFAULT "",
    parent_id      INTEGER REFERENCES depts(id),  -- NULL 表示根节点
    manager_id     INTEGER NOT NULL REFERENCES team_members(id),  -- 主管的 member id
    member_ids     TEXT    NOT NULL DEFAULT "[]",                 -- 序列化的 int 数组，存储本部门成员 id 列表
    created_at     TEXT    NOT NULL,
    updated_at     TEXT    NOT NULL,
    UNIQUE (team_id, name)
);
```

说明：

- 树形层级通过 `parent_id` 自引用实现，扁平存储，查询时按需重建树
- `manager_id` 为外键，指向 `team_members.id`，值必须同时出现在 `member_ids` 数组中
- `member_ids` 底层存储为序列化字符串，通过 `JsonField(List[int])` 自动转换，ORM 层读写均为 `list[int]`，存储本部门成员的 `team_members.id` 列表（含主管）
- `team_id + name` 唯一，同一 Team 内部门名不重复
- 根节点的 `parent_id` 为 `NULL`

### 3.2 扩展 `team_members` 表

在现有 `(team_id, name, agent_name, updated_at)` 基础上新增：

```sql
ALTER TABLE team_members ADD COLUMN employ_status TEXT DEFAULT "on_board";  -- on_board / off_board
ALTER TABLE team_members ADD COLUMN model  TEXT DEFAULT "";
ALTER TABLE team_members ADD COLUMN driver TEXT DEFAULT "{}";      -- JSON 字符串
```

说明：

- `employ_status = "off_board"` 表示该成员已从所有部门移除，处于休闲状态；判断成员归属时以 `depts.member_ids` 为准
- `model`/`driver` 存在时优先级高于 `AgentTemplate` 中的对应字段

### 3.3 完整性约束（应用层保障）

数据库不直接编码以下约束，由 `deptService` 在写入前校验：

1. `depts.manager_id` 必须出现在该行的 `depts.member_ids` 数组中
2. 同一 member id 不得同时出现在多个部门的 `member_ids` 数组中
3. 移除当前主管时，必须在同一事务中指定新主管并更新 `depts.manager_id`

---

## 4. 核心模块改动

### 4.1 新增 `deptService`（`src/service/deptService.py`）

承担部门树的全部 CRUD 和完整性校验，其他 service 不直接读写 `depts` 表。

主要接口：

```python
async def import_dept_tree(team_id: int, node: DeptNodeConfig) -> None
    """递归将 dept_tree 配置写入数据库（首次导入；已存在时跳过）。"""

def get_dept_tree(team_id: int) -> DeptNodeConfig | None
    """从 DB 重建树形结构，返回根节点；无部门时返回 None。"""

async def move_member(team_id: int, member_name: str, target_dept_name: str,
                      is_manager: bool = False) -> None
    """将成员（含 off_board 成员）移入指定部门，可选设为主管。"""

async def remove_member(team_id: int, member_name: str,
                        new_manager: str | None = None) -> None
    """
    将成员从所在部门的 members 列表中移除，并将 team_members.employ_status 设为 off_board。
    若该成员为当前部门的 manager，new_manager 必须提供，否则抛出异常。
    操作在单个事务内完成：更新 depts.member_ids / depts.manager_id + 更新 team_members.employ_status。
    """

async def set_dept_manager(team_id: int, dept_name: str, manager_name: str) -> None
    """变更部门主管，新主管必须已是该部门成员。"""

def get_off_board_members(team_id: int) -> list[GtTeamMember]
    """返回所有 employ_status=off_board 的成员。"""
```

### 4.2 扩展 `teamService`

`teamService.startup()` 中在导入 members 之后，增加部门树导入：

```python
# 现有逻辑
await gtTeamMemberManager.upsert_team_members(team_id, team_config.members)

# V10 新增
if team_config.dept_tree:
    await deptService.import_dept_tree(team_id, team_config.dept_tree)
```

`teamService.create_team()` 同理，在成员写入后触发部门树导入。

### 4.3 新增 DAL Manager（`src/dal/db/gtDeptManager.py`）

封装 `depts` 表的查询与写入：

```python
async def get_dept_by_name(team_id: int, name: str) -> GtDept | None
async def get_all_depts(team_id: int) -> list[GtDept]
async def upsert_dept(team_id: int, name: str, responsibility: str,
                      parent_id: int | None) -> GtDept
```

`team_members` 的部门相关字段变更集中在现有 `gtTeamMemberManager` 中扩展，不新建 Manager。

### 4.4 新增 ORM 模型（`src/model/dbModel/gtDept.py`）

```python
class GtDept(DbModelBase):
    team_id:        int = peewee.IntegerField()
    name:           str = peewee.CharField()
    responsibility: str = peewee.TextField(default="")
    parent_id:      int = peewee.IntegerField(null=True)
    manager_id:     int = peewee.IntegerField()          # 主管的 team_members.id
    member_ids: list[int] = JsonField(List[int], default=list)  # 自动序列化，存储 team_members.id 列表

    created_at:     str = peewee.CharField(default=lambda: datetime.now().isoformat())
    updated_at:     str = peewee.CharField(default=lambda: datetime.now().isoformat())

    class Meta:
        table_name = "depts"
        indexes = ((("team_id", "name"), True),)
```

`member_ids` 字段使用自定义的 peewee 泛型字段类 `JsonField(List[int])` 自动处理序列化与反序列化，上层直接读写 `list[int]`，无需手动调用 `json.loads` / `json.dumps`。

### 4.5 扩展 `GtTeamMember` 模型

在现有字段基础上新增（去掉原有的 `dept_id` / `is_manager`）：

```python
employ_status: str = peewee.CharField(default="on_board")  # on_board / off_board
model:  str = peewee.CharField(default="")
driver: str = peewee.TextField(default="{}")       # JSON 字符串
```

### 4.6 Agent 上下文注入（`agentService`）

Agent 在构建 system prompt 时，新增一个"部门上下文块"。`agentService` 在创建 Agent 实例时，从 `deptService` 查询该成员的部门信息，并注入到 `system_prompt` 末尾：

```
---
组织信息：
- 所在部门：{dept_name}（{dept_responsibility}）
- 上级部门：{parent_dept_name}（主管：{parent_manager_name}）
- 本部门主管：{manager_name}（如果自己是主管，则省略此行）
- 本部门其他成员：{member_1}, {member_2}, ...
---
```

若该 Team 未配置 `dept_tree`，则不注入此块，行为与 V9 保持一致。

### 4.7 `model`/`driver` 覆盖逻辑

在 `agentService` 创建 Agent 实例时，合并优先级：

```
AgentConfig.model > AgentTemplate.model > SettingConfig 默认模型
AgentConfig.driver > AgentTemplate.driver > 默认 native driver
```

已通过 `normalize_driver_config()` 处理 `AgentTemplate.driver`，V10 在调用前先将 `AgentConfig.driver` 合并进去：

```python
merged_driver = {**agent_template.driver, **member_config.driver} if member_config.driver else agent_template.driver
merged_model  = member_config.model or agent_template.model
```

---

## 5. API 变更

### 5.1 新增部门树端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/teams/{team_id}/dept_tree.json` | 返回当前部门树（树形 JSON） |
| `PUT` | `/teams/{team_id}/dept_tree/{dept}/manager.json` | 变更部门主管 |
| `POST` | `/teams/{team_id}/dept_tree/{dept}/agents.json` | 将成员加入部门（可选 `is_manager`） |
| `DELETE` | `/teams/{team_id}/dept_tree/{dept}/agents/{agent}.json` | 将成员移出部门（若为主管需在请求体中传 `new_manager`） |
| `GET` | `/teams/{team_id}/dept_agents.json?employ_status=off_board` | 查询所有休闲成员 |

### 5.2 `GET /teams/{team_id}/dept_tree.json` 响应格式

```json
{
  "dept_name": "执行委员会",
  "dept_responsibility": "...",
  "manager": "alice",
  "members": ["alice", "bob", "eve"],
  "children": [
    {
      "dept_name": "技术部",
      "dept_responsibility": "...",
      "manager": "bob",
      "members": ["bob", "carol", "dave"],
      "children": []
    }
  ]
}
```

### 5.3 现有接口兼容性

- 现有 Team/Room/Message 接口路径保持不变（`.json` 形式），不破坏原有字段结构
- 部门信息通过新增 `dept_tree` / `dept_agents` 接口提供
- 运行时成员详情继续通过 `GET /teams/{team_id}/agents/{agent}.json` 获取

---

## 6. 启动流程变更

```
现有：加载配置 → 导入 Team → 导入 Members → 创建 Agent 实例 → 创建 Room → 启动调度

V10：加载配置 → 导入 Team → 导入 Members（含 model/driver）
             → 导入 dept_tree（若存在）
             → 创建 Agent 实例（注入部门上下文）
             → 创建 Room → 启动调度
```

若 `dept_tree` 缺失或配置校验失败，系统应明确报错并中止启动，不允许静默跳过。

---

## 7. 数据迁移

V10 对 `team_members` 表新增字段均有默认值（`employ_status="on_board"`、`model=""`、`driver="{}"`），存量数据无需手动迁移：

- 现有成员默认进入"无部门归属"状态（不在任何部门 `member_ids` 列表中），不影响现有调度
- `depts` 表为全新表，仅在 Team 配置了 `dept_tree` 时才有数据

`ormService` 在启动时检查表结构，若目标列缺失则执行 `ALTER TABLE` 补列。

---

## 8. 实施顺序建议

1. 新增 `DeptNodeConfig`，扩展 `AgentConfig`（`model`/`driver` 字段），更新 `TeamConfig`
2. 新增 `GtDept` ORM 模型，扩展 `GtTeamMember` 字段，`ormService` 补充建表/补列逻辑
3. 新增 `gtDeptManager`，扩展 `gtTeamMemberManager`（部门相关读写）
4. 新增 `deptService`，实现 `import_dept_tree`、`remove_member`、`move_member` 等接口
5. 扩展 `teamService.startup()` / `create_team()`，调用 `deptService.import_dept_tree()`
6. 扩展 `agentService`，在创建 Agent 时注入部门上下文，合并 `model`/`driver` 覆盖
7. 新增 Controller / 路由（`dept_tree` 相关 REST 接口）
8. 补充单元测试（完整性约束校验、主管移除原子性、driver 合并优先级）

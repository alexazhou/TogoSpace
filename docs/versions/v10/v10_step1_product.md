# V10: 组织树与部门管理 - 产品文档

## 目标

在 V7 团队化组织的基础上，将 Team 内的成员关系从扁平列表升级为**层级组织树**。引入 Leader 与部门的概念，使团队结构更贴近真实企业协作场景。支持对组织结构进行动态编辑，被移除的成员进入休闲状态而非删除，可随时重新挂载。

---

## 功能特性

- **组织树模型**：Team 内成员以树形层级组织，每个节点可拥有任意数量的直接下属，根节点为团队最高负责人。
- **部门概念**：组织树的每个节点对应一个部门，每个部门可独立配置名称和职责描述。
- **Leader 声明**：成员列表中通过 `is_manager` 字段显式指定某成员为其所在部门的负责人。
- **组织树编辑**：支持对现有组织树进行动态调整，包括新增下属关系、变更汇报线、提升或下移成员层级。
- **休闲状态**：从组织树中移除某成员时，该成员不会被删除，而是进入"休闲（idle）"状态，保留其配置与历史上下文，可随时重新挂载到组织树中。
- **上下文注入**：Agent 在生成回复时，自动感知所在部门的名称、职责和汇报链，为角色扮演与分工协作提供结构化背景。

---

## 核心概念

### 组织树

每个 Team 拥有一棵组织树，树中每个节点对应一个**部门**，节点之间通过父子关系表达上下级隶属。每个部门节点包含：部门元数据（名称、职责）、成员名单（`members`）和主管（`manager`）。

```
Team: Acme Corp
└── 执行委员会（主管：Alice，成员：Alice / Bob / Eve）
    ├── 技术部（主管：Bob，成员：Bob / Carol / Dave）
    └── 财务部（主管：Eve，成员：Eve / Frank）
```

### 成员列表

成员列表独立于组织树，存储每个人的 Agent 运行配置：使用哪个 Agent 模板、哪种驱动、哪个模型。成员列表不描述归属关系，归属关系由组织树节点的 `members` 和 `manager` 字段定义。

| 成员 | Agent 模板 | 驱动 | 模型 |
|------|-----------|------|------|
| Alice | executive_agent | native | claude-opus-4-6 |
| Bob | engineer_agent | claude_sdk | claude-sonnet-4-6 |
| Carol | backend_dev | claude_sdk | claude-sonnet-4-6 |
| Dave | frontend_dev | native | claude-haiku-4-5 |
| Eve | finance_agent | native | claude-sonnet-4-6 |
| Frank | accountant | tsp | claude-haiku-4-5 |

驱动类型说明：

| 驱动 | 说明 |
|------|------|
| `native` | 标准 LLM 对话驱动，直接调用模型 API |
| `claude_sdk` | 基于 Claude Agent SDK，支持工具调用（文件读写、代码执行等），需配置 `allowed_tools` |
| `tsp` | 轻量级任务执行驱动，适合信息检索、文件定位等结构化子任务 |

### Leader 与部门

每个部门在 `manager` 字段中指定一名主管，该主管即为该部门的 **Leader**，在对话中代表部门与上下级沟通。以下约束始终成立：

- 每个部门**必须有且仅有一名主管**，不允许存在无主管的部门。
- 主管必须同时出现在该部门的 `members` 名单中。
- 部门名称和职责描述未填写时使用默认占位描述。

### 休闲状态

成员可处于以下两种组织状态之一：

| 状态 | 含义 |
|------|------|
| **在职（active）** | 挂载在某部门的 `members` 中，参与正常调度 |
| **休闲（idle）** | 已从部门移除，不参与调度，但保留配置与历史上下文 |

移除普通成员时，该成员直接进入休闲状态，部门结构不变。**移除当前主管时，必须在同一操作中指定该部门的新主管**，否则操作被拒绝。休闲成员随时可被重新加入任意部门并指定是否担任主管。

---

## 效果演示

### 组织树配置示例

```json
{
  "team": "Acme Corp",
  "dept_tree": {
    "dept_name": "执行委员会",
    "dept_responsibility": "负责公司整体战略方向与跨部门协调决策。",
    "manager": "alice",
    "members": ["alice", "bob", "eve"],
    "children": [
      {
        "dept_name": "技术部",
        "dept_responsibility": "负责产品研发、系统架构设计与技术风险管控。",
        "manager": "bob",
        "members": ["bob", "carol", "dave"],
        "children": []
      },
      {
        "dept_name": "财务部",
        "dept_responsibility": "负责公司财务规划、预算管理与合规审查。",
        "manager": "eve",
        "members": ["eve", "frank"],
        "children": []
      }
    ]
  },
  "members": [
    {
      "name": "alice", "role_template": "executive_agent", "model": "claude-opus-4-6",
      "driver": { "type": "native" }
    },
    {
      "name": "bob", "role_template": "engineer_agent", "model": "claude-sonnet-4-6",
      "driver": { "type": "claude_sdk", "allowed_tools": ["Read", "Write", "Edit", "Bash"] }
    },
    {
      "name": "carol", "role_template": "backend_dev", "model": "claude-sonnet-4-6",
      "driver": { "type": "claude_sdk", "allowed_tools": ["Read", "Write", "Edit", "Bash"] }
    },
    {
      "name": "dave", "role_template": "frontend_dev", "model": "claude-haiku-4-5",
      "driver": { "type": "native" }
    },
    {
      "name": "eve", "role_template": "finance_agent", "model": "claude-sonnet-4-6",
      "driver": { "type": "native" }
    },
    {
      "name": "frank", "role_template": "accountant", "model": "claude-haiku-4-5",
      "driver": { "type": "tsp" }
    }
  ]
}
```

### Agent 上下文注入示例

Bob 在聊天室中发言时，其系统提示将包含如下结构化信息：

```
你是 Bob，担任 CTO，隶属于"执行委员会"，向 Alice（CEO）汇报。
你领导"技术部"，职责是：负责产品研发、系统架构设计与技术风险管控。
你的直接下属是 Carol（后端开发）和 Dave（前端开发）。
```

### 移除成员与休闲状态示例

**场景一：移除普通成员（Dave）**

直接移除，部门无需额外操作：

操作前 `members: ["bob", "carol", "dave"]` → 操作后 `members: ["bob", "carol"]`，Dave 进入休闲状态。

---

**场景二：移除主管（Bob），同时指定新主管（Carol）**

Bob 是技术部主管，移除时必须在同一请求中指定新主管，否则操作被拒绝。

**操作前（技术部节点）：**

```json
{
  "dept_name": "技术部",
  "manager": "bob",
  "members": ["bob", "carol", "dave"],
  "children": []
}
```

**操作后（技术部节点）：**

```json
{
  "dept_name": "技术部",
  "manager": "carol",
  "members": ["carol", "dave"],
  "children": []
}
```

Bob 进入休闲状态，Carol 接任主管。技术部始终保持有主管的状态，部门结构和其余成员不受影响。Bob 的 Agent 配置与历史上下文完整保留，可随时重新加入任意部门。

---

## 产品边界

### V10 包含

- 以独立的组织树描述部门层级，支持多层嵌套，每个节点含成员名单与主管声明
- 以独立的成员列表存储每个人的 Agent 模板、驱动和模型配置
- 部门名称和职责描述的配置与读取
- 通过 API 对组织树进行增、删、调整操作
- 成员的 idle / active 状态管理
- Agent 发言时注入汇报链与部门信息
- Web 前端展示组织树结构

### V10 不包含

- 跨 Team 的汇报关系
- 成员同时隶属于多个部门（每个成员只能关联一个部门）
- 组织变更的历史审计日志
- 基于组织层级的权限控制

---

## 验收标准

- [ ] `dept_tree` 以树形结构定义部门层级，支持超过两层嵌套，每个节点包含成员名单与主管字段。
- [ ] `members` 列表存储每个人的 `role_template`（模板）、`model` 和 `driver`（含 `type` 及驱动专属选项）配置，与组织归属解耦。
- [ ] 每个部门的 `manager` 必须出现在该部门的 `members` 名单中，违反时系统报错。
- [ ] 每个部门必须有且仅有一名主管；移除当前主管的操作若未同时指定新主管，系统拒绝执行。
- [ ] 每个部门可独立配置名称和职责描述，未配置时使用默认值。
- [ ] Agent 发言时的 Prompt 中包含当前所在部门名称、职责及汇报链信息。
- [ ] 支持通过 API 将普通成员设为 idle 状态，该成员脱离所属部门，其余成员不受影响。
- [ ] 移除主管时，API 要求在同一请求中指定新主管，操作原子完成。
- [ ] 休闲状态的成员可通过 API 重新关联到指定部门（并可指定是否为主管）。
- [ ] Web 前端能以树形可视化方式展示当前部门结构及各部门成员，并标注 idle 成员。
- [ ] 组织结构的调整不影响正在运行的聊天室调度。

---

## 使用说明

### 配置文件结构

Team 配置文件新增 `dept_tree` 顶层字段：

- `dept_tree`：递归树形结构，描述部门层级与人员归属。每个节点包含 `dept_name`（部门名称）、`dept_responsibility`（部门职责描述）、`manager`（主管成员名）、`members`（本部门成员名单）和 `children`（子部门列表）。
- `members`：成员运行配置列表，每条记录包含 `name`（成员名）、`role_template`（RoleTemplate 名称）、`model`（使用的模型）和 `driver`（驱动配置对象，含 `type` 字段及驱动专属选项，如 `claude_sdk` 需配置 `allowed_tools`）。成员的部门归属由 `dept_tree` 定义，此处不重复声明。

### API 操作

| 操作 | 说明 |
|------|------|
| `GET /teams/{team_id}/dept_tree.json` | 获取当前部门树结构 |
| `PUT /teams/{team_id}/dept_tree/{dept}/manager.json` | 变更某部门的主管 |
| `POST /teams/{team_id}/dept_tree/{dept}/agents.json` | 将成员加入某部门（可指定是否担任主管） |
| `DELETE /teams/{team_id}/dept_tree/{dept}/agents/{member}.json` | 将成员从部门移除（进入 idle）；若为主管，请求体中须指定继任主管 |
| `GET /teams/{team_id}/dept_agents.json?employ_status=off_board` | 查询所有休闲成员 |

### 观察方式

- Web 前端在 Team 详情页新增"组织架构"标签，展示层级树形视图，idle 成员在列表中单独分组显示。
- Agent 发言气泡的鼠标悬停提示中可展示其部门归属和汇报链信息。

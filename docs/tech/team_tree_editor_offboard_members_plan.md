# 团队组织树离职成员处理方案

## 背景

后端会返回团队下的成员历史记录，包括 `ON_BOARD` 和 `OFF_BOARD`。

当前问题是：前端组织树把所有成员都当作当前成员渲染。同名复用时，例如：

```text
#1 111 OFF_BOARD
#2 111 ON_BOARD
```

组织树会显示两个 `111`。

结论：后端保持全量返回，前端按页面语义过滤。

## 原则

1. 后端接口不按前端视图裁剪数据。
2. 当前组织树只展示 `ON_BOARD` 成员。
3. `OFF_BOARD` 成员作为历史数据保留，但当前 UI 不展示。
4. 前端树内部尽量用 `agentId` / 节点 id 定位，不继续依赖 `name` 作为唯一标识。
5. 新成员保存要先拿到后端 id，再生成部门树。

## 当前结构

组织树编辑器内部用一棵 `DraftOrgNode` 树存 UI 状态：

```ts
type DraftOrgNode = {
  id: string;
  kind: 'member' | 'pending';
  agentId: number | null;
  deptId: number | null;
  memberName: string;
  roleTemplateId: number | null;
  model: string;
  driver: string;
  employeeNumber: string;
  deptName: string;
  deptResponsibility: string;
  children: DraftOrgNode[];
};
```

其中：

- `draftOrgTree`：当前编辑中的树
- `committedOrgTree`：最近一次保存/加载成功的树
- 新成员保存前 `agentId === null`

## 改造方案

### 1. 前端加载后分层成员

加载 `getAgentsByTeamId(teamId)` 后拆分：

- `activeAgents`: `employ_status === 'ON_BOARD'` 或状态为空
- `offBoardAgents`: `employ_status === 'OFF_BOARD'`

组织树只使用 `activeAgents` 构建。

### 2. 详情页和编辑器都过滤在职成员

- 团队详情页成员图：只展示在职成员
- 设置页组织树编辑器：只把在职成员放进 `draftOrgTree`
- 离职成员不展示，不参与重复名校验、fallback 树、extra agent 补挂
- 当前在职成员继续禁止重名

### 3. 节点定位改用稳定 id

把“展示名”和“节点身份”拆开：

- `DraftOrgNode.id`：前端树节点 id，永远唯一，新成员保存前也有
- `DraftOrgNode.agentId`：后端 Agent id，保存后才稳定
- `DraftOrgNode.memberName`：只用于展示、输入、重复名校验和保存 payload，不用于定位

`TeamGraphNode` 同步携带：

```ts
type TeamGraphNode = {
  id: string;              // DraftOrgNode.id
  agentId: number | null;  // 后端 Agent id
  name: string;
  ...
};
```

内部操作从按 `memberName` 查找改成按：

- 前端节点 `id`：编辑、删除、添加下属、部门编辑
- `agentId`：打开成员详情、保存后生成部门树

重点替换：

- 编辑成员：传 `nodeId`
- 删除成员：传 `nodeId`
- 添加下属：传父节点 `nodeId`
- 打开详情：传 `agentId`，同时带 `nodeId` 兜底

新增基础查找函数：

```ts
findNodeById(root, nodeId)
removeNodeById(root, nodeId)
```

编辑状态从：

```ts
editingMemberName
```

迁移为：

```ts
editingNodeId
```

这样即使存在两个同名 `111`，前端树内部也能稳定区分它们。

### 4. 保存改成两阶段

当前问题：新成员保存前没有 `agentId`，直接生成 `DeptTree` 会漏掉新成员。

新流程：

1. 从 `draftOrgTree` 生成成员保存 payload
2. 保存成员，拿到后端返回的真实 `id`
3. 把新成员 id 回填到 `draftOrgTree`
4. 基于回填后的树生成 `DeptTree`
5. 保存 `DeptTree`
6. 用最终数据刷新 `committedOrgTree` / `draftOrgTree`

补充约束：

- 父部门的 `agent_ids` 需要包含直属下属负责人
- 子部门负责人同时也会出现在子部门自己的 `agent_ids`
- 也就是“主管 + 下属负责人”已经满足父部门最少 2 人

## 已确认

1. 离职成员当前 UI 不展示。
2. 当前在职成员继续禁止重名。
3. `TeamDetailPage` 改用 `/agents/list.json?team_id={id}`，前端过滤出在职成员展示。

## 验收

1. 同名离职 + 在职成员存在时，组织树只显示在职成员。
2. 新增成员保存后，部门树里使用真实后端 `agentId`。
3. 新成员作为负责人时，`manager_id` 正确。
4. 删除/编辑成员不会误操作同名离职成员。

# 团队组织树编辑器当前实现

本文记录当前 Web 前端 `TeamTreeEditor` 的真实实现，重点说明：

- 页面加载时如何从后端数据构建编辑态组织树
- 页面渲染时如何从内存树派生图结构
- 页面保存时如何从内存树导出 API payload

本文对应当前组件与接口：

- `frontend/src/components/TeamTreeEditor.vue`
- `frontend/src/components/TeamMembersCard.vue`
- `frontend/src/components/TeamMemberGraph.vue`
- `frontend/src/components/TeamMemberTreeNode.vue`
- `frontend/src/composables/useMemberEditorDialog.ts`
- `frontend/src/api.ts`

说明：

- 本文描述的是“当前实现”，不是 V10 设计稿
- 本文用于替代旧的团队关系编辑器文档

## 1. 目标

当前 `TeamTreeEditor` 已经改成“单一组织树状态驱动”的实现。

也就是说，编辑器内部不再同时维护：

- 成员列表
- 父子关系表
- 部门名映射表
- 部门职责映射表
- 待添加槽位数组

而是统一维护一棵编辑树 `draftOrgTree`，所有渲染、编辑、保存都围绕这棵树进行。

## 2. 核心内存结构

文件：`frontend/src/components/TeamTreeEditor.vue`

当前核心类型：

```ts
type DraftOrgNode = {
  id: string;
  kind: 'member' | 'pending';
  agentId: number | null;
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

当前核心状态：

```ts
const committedOrgTree = ref<DraftOrgNode | null>(null);
const draftOrgTree = ref<DraftOrgNode | null>(null);
```

语义如下：

- `committedOrgTree`
  最近一次从后端成功加载，或最近一次保存成功后得到的“已提交树”
- `draftOrgTree`
  当前正在编辑中的树

此外还有少量 UI 状态：

- `editingPendingSlotId`
- `editingDepartmentMemberName`
- `departmentEditorName`
- `departmentEditorResponsibility`
- `confirmState`

这些状态只负责弹窗和编辑器，不再承载组织结构本身。

## 3. 加载逻辑

### 3.1 加载入口

当 `props.teamId` 变化时，`TeamTreeEditor` 会触发一次并行加载：

```ts
const [deptTree, teamAgents, roleTemplates, nextFrontendConfig] = await Promise.all([
  getDeptTree(requestTeamId),
  getAgentsByTeamId(requestTeamId),
  getRoleTemplates(),
  getFrontendConfig(),
]);
```

对应接口：

- `GET /teams/{teamId}/dept_tree.json`
- `GET /agents/list.json?team_id={teamId}`
- `GET /role_templates/list.json`
- `GET /config/frontend.json`

### 3.2 从后端结构转成编辑树

加载完成后，调用：

```ts
syncCommittedState(deptTree, nextMembers)
```

其中 `syncCommittedState()` 会做两件事：

1. 保存 `committedAgents`
2. 调用 `buildDraftOrgTree(tree, agents)` 构造 `committedOrgTree`
3. 用深拷贝生成 `draftOrgTree`

### 3.3 `buildDraftOrgTree()` 的规则

`buildDraftOrgTree()` 负责把后端 `DeptTreeNode` 转成前端编辑树：

- 每个部门节点的 `manager` 会转成一个 `DraftOrgNode(kind='member')`
- 当前部门里除 `manager` 外、且不属于子部门 `manager` 的成员，会被挂成当前节点的直接 `children`
- 子部门会递归转成子节点
- 如果后端成员列表里还有未出现在部门树里的成员，会被补挂到根节点下
- 如果后端根本没有 `dept_tree`，则退化为“首个成员为根，其余成员挂到根下”

因此当前前端编辑器看到的始终是一棵“成员树”，部门信息只是成员节点上的附加字段：

- `deptName`
- `deptResponsibility`

## 4. 渲染逻辑

### 4.1 渲染入口

编辑器不直接把 `draftOrgTree` 传给图组件，而是先转成展示专用结构 `TeamGraphNode`：

```ts
const graphRootNode = computed<TeamGraphNode | null>(() => (
  draftOrgTree.value ? toGraphNode(draftOrgTree.value, props.teamName) : null
));
```

然后传入：

- `TeamMembersCard`
- `TeamMemberGraph`
- `TeamMemberTreeNode`

### 4.2 `toGraphNode()` 的职责

`toGraphNode()` 只做视图投影，不改变业务结构：

- `DraftOrgNode.kind === 'pending'` -> 图上的 `+ 成员` 占位节点
- `DraftOrgNode.kind === 'member'` -> 普通成员节点
- `deptName` -> 节点 overline
- `roleTemplateId` -> 节点 subtitle
- `employeeNumber` -> 员工号展示

### 4.3 部门按钮显示规则

部门相关操作按钮是否出现，不是由 `deptName` 决定，而是由节点是否有子节点决定。

`TeamMemberTreeNode.vue` 中：

```ts
const showDepartmentAction = computed(() => props.node.kind === 'member' && !!props.node.children.length);
```

也就是说：

- 只有当前节点下存在子节点时，才会出现“查看部门 / 编辑部门”
- 叶子成员即使有 `deptName`，当前 UI 也不会显示部门操作按钮

这和当前“只有有下属的人才算部门负责人”的语义是一致的。

### 4.4 画布布局、拖拽与缩放

图区域的布局与手势由：

- `frontend/src/components/TeamMemberGraph.vue`
- `frontend/src/components/useTeamGraphLayout.ts`

共同完成。

当前核心状态包括：

- `panX`
- `panY`
- `zoom`
- `isPanning`

画布整体通过 `canvasStyle` 做变换：

```ts
transform: translate(-50%, 0) translate(${panX}px, ${panY}px) scale(${zoom})
```

语义如下：

- `translate(-50%, 0)`
  负责以中线为基准居中
- `panX/panY`
  负责拖动画布偏移
- `scale(zoom)`
  负责滚轮缩放

当前拖拽与缩放规则：

1. 编辑态下，如果 pointer 起点落在按钮上，不启动拖拽
2. 只读态下，节点区域本身也可以作为拖拽起点
3. 鼠标滚轮会调整 `zoom`，范围限制在 `0.6 ~ 1.8`
4. 缩放或内容变化后，会重新计算边界并自动把画布复位到一个居中的合法位置

### 4.5 边界测量规则

`useTeamGraphLayout.ts` 的 `updateMetrics()` 会测量当前图中的有效可见内容。

当前参与边界计算的元素包括：

- 根节点
- 普通成员节点
- 成员操作按钮
- 各类连接线与轨道

这比旧文档里“只按主节点测量”的策略更完整，因为现在 hover 操作按钮也会被纳入边界计算。

拖拽边界采用“至少保留一圈可见边”的策略：

- `keepVisiblePx = 10`

也就是说内容允许部分移出视口，但不允许整个有效内容完全离开可视区域。

## 4.6 顶层连接线

`useTeamGraphLayout.ts` 还会计算：

- `railStartX`
- `railEndX`

用于控制顶层成员之间横向连接轨道的左右留白，使轨道能和最左/最右顶层节点对齐。

## 5. 编辑逻辑

### 5.1 编辑模式开启

点击“编辑团队组织”后：

- `isReadonly = false`
- 如果当前还没有树，则插入一个根 `pending` 节点

这样可以支持“从空团队开始创建第一个成员”。

### 5.2 添加下属

调用：

```ts
addSubordinate(parentName)
```

流程：

1. 深拷贝当前 `draftOrgTree`
2. 找到目标父节点
3. 如果父节点之前没有直属下属，且 `deptName` 为空，则自动生成 `新部门n`
4. 向 `children` 末尾插入一个 `pending` 节点

因此，“创建部门”的动作现在不是独立按钮，而是通过“第一次给某成员添加下属”隐式触发。

### 5.3 编辑 pending 节点

pending 节点进入成员编辑器后，保存逻辑会：

1. 校验成员名不为空
2. 校验成员名不重复
3. 构造一个新的 `member` 节点
4. 用它替换原先的 `pending` 节点

### 5.4 编辑已有成员

成员编辑器保存时，会直接修改树节点本身：

- `memberName`
- `roleTemplateId`
- `model`
- `driver`

这里已经不再需要额外维护：

- `teamMemberRoleDrafts`
- `teamMemberModelDrafts`
- `teamMemberDriverDrafts`

### 5.5 编辑部门

部门编辑器保存时，会直接修改对应节点：

- `deptName`
- `deptResponsibility`

### 5.6 删除成员

删除时会从树中直接移除对应节点。

当前前端删除行为是“从编辑树中移除该成员节点及其子树”，保存后交由后端成员保存接口和部门树接口共同收敛最终状态。

## 6. 保存逻辑

### 6.1 保存入口

点击“保存”时，调用：

```ts
saveTeamMembers()
```

### 6.2 保存前导出两份 payload

保存前会从 `draftOrgTree` 导出两份数据：

```ts
const nextMembers = buildMembersSavePayload();
const nextDeptTree = buildDeptTreePayload();
```

两者职责不同：

- `nextMembers`
  成员配置列表
- `nextDeptTree`
  后端需要的 `dept_tree`

### 6.3 成员保存 payload

`buildMembersSavePayload()` 会遍历整棵树中所有 `kind='member'` 节点，导出：

```ts
{
  id,
  name,
  role_template_id,
  model,
  driver,
}
```

然后调用：

- `PUT /teams/{teamId}/members/save.json`

### 6.4 部门树保存 payload

`buildDeptTreePayload()` 会把成员树重新折叠成后端的 `DeptTreeNode`。

当前折叠规则是：

- 根节点总是导出为部门节点
- 有直属下属的成员导出为部门节点
- 带下属的直属成员，会同时计入父部门的 `agent_ids`
- 叶子成员不会再单独导出成一个部门节点
- 叶子成员会被并入上级部门的 `agent_ids` 列表

这也是当前”不要给每个人都创建一个部门”的关键实现点。

示意如下：

前端编辑树：

```text
小马哥(dept=总部)
├─ 王老师
├─ 小A
└─ 小刘(dept=新部门1)
   └─ 小孩哥
```

导出的后端 `dept_tree` 会更接近：

```json
{
  "id": 1,
  "name": "总部",
  "responsibility": "总部职责",
  "manager_id": 101,
  "agent_ids": [101, 102, 103, 104],
  "children": [
    {
      "id": 2,
      "name": "新部门1",
      "responsibility": "",
      "manager_id": 104,
      "agent_ids": [104, 105],
      "children": []
    }
  ]
}
```

然后调用：

- `PUT /teams/{teamId}/dept_tree/update.json`

### 6.5 保存顺序

当前保存顺序是：

1. `saveMembersByTeamId(teamId, nextMembers)`
2. 如果部门树有变化，再 `setDeptTree(teamId, nextDeptTree)`

也就是说，当前依然是两个 API，且不是事务性的。

如果第一个成功、第二个失败，会出现：

- 成员配置已更新
- 组织树未更新

这是当前实现的一个已知约束。

## 7. 保存成功后的状态回写

保存成功后：

1. 从成员保存接口返回结果中筛出在职成员
2. 使用 `nextDeptTree + nextAgents` 再次调用 `syncCommittedState()`
3. 重建 `committedOrgTree`
4. 用它覆盖 `draftOrgTree`

因此保存成功后，前端会把当前编辑树重新视作新的 committed 基线。

## 8. 当前实现的优点

相对旧的扁平 draft map 模式，当前实现有几个明显好处：

1. 组织结构只有一个事实来源
   `draftOrgTree` 就是编辑态真相，不需要在多份 map 间保持一致

2. 增删节点更直接
   添加下属、删除成员、替换 pending 都是树操作，不需要同步多份索引

3. 渲染和保存链路更清晰
   渲染时：树 -> `TeamGraphNode`
   保存时：树 -> `members payload` + `dept_tree payload`

4. 更符合组织树编辑器的心智模型
   编辑的就是一棵树，而不是“若干表拼成一棵树”

## 9. 当前已知约束

1. 部门操作按钮仍按“是否有子节点”控制
   不是按 `deptName` 是否存在控制

2. 保存仍是两个 API
   成员配置和组织树不是一个事务

3. 删除成员目前是直接删树节点
   是否最终转换成后端的 `OFF_BOARD` 语义，依赖成员保存接口与部门树保存接口共同作用

4. 创建团队后首次进入编辑器时，根节点仍然是“成员根”
   不是一个独立于成员的“虚拟公司节点”

## 10. 建议的后续方向

如果后续继续演进，建议优先考虑：

1. 后端提供单一的“团队组织保存”接口
   一次提交成员配置和组织树，避免前端双写

2. 明确“部门节点”和“成员节点”是否要继续共用一个节点模型
   当前是“成员兼部门负责人”的混合模型

3. 明确删除成员时的最终业务语义
   是“完全移出团队”，还是“保留成员但下树进入 idle”

4. 给当前实现补一组前端交互测试
   尤其覆盖：
   - 从空团队创建根节点
   - 给成员添加第一个下属
   - 叶子成员不落独立部门节点
   - 保存后重新加载仍能还原

# 团队关系编辑器逻辑梳理

本文描述当前 Web 前端“团队成员关系图”这套实现的结构、数据流和交互规则。这里的“团队关系编辑器”对应当前组件实现：

- `frontend/src/components/TeamMembersCard.vue`
- `frontend/src/components/TeamMemberGraph.vue`

文档基于当前工作区代码状态整理，包含尚未提交的交互调整。

## 1. 目标与使用场景

这套组件承担两类页面场景：

1. 编辑态
   页面入口：`/teams/new`
   用户可以选择成员、移除成员，关系图作为团队草稿的可视化编辑区域。

2. 只读态
   页面入口：`/teams/:teamId/settings/teams?detailTeamId=:id`
   用户查看团队成员结构，可拖动画布浏览，并从成员节点进入 agent 详情页。
   只读态右上角应提供“编辑”按钮，点击后跳转到编辑态页面。

当前它不是通用组织架构编辑器，而是一个“单负责人 + 多成员”的固定结构视图。

## 2. 页面与路由入口

### 2.1 创建团队页

文件：`frontend/src/pages/TeamCreatePage.vue`

- 页面维护 `selectedAgents: string[]`
- `toggleAgent(agentName)` 负责成员增删
- `TeamMembersCard` 只接收 `selectedAgents` 和 `toggleAgent`
- `readonly` 默认为 `false`

这里关系图是一个纯前端草稿视图，不直接操作后端。

### 2.2 团队详情页 / 设置页

文件：`frontend/src/pages/SettingsPage.vue`

- 通过 `detailTeamId` 选中团队详情
- `selectedTeamMembers` 来自 `selectedTeamDetail.members.map(member => member.name)`
- `TeamMembersCard` 以 `readonly` 方式渲染

这里只读态不会修改团队成员，但允许：

- 拖动画布
- hover 查看节点
- 通过操作按钮跳转 agent 详情

### 2.3 Agent 详情页

文件：`frontend/src/router.ts`

- 路由：`/teams/:teamId/agents/:agentName`
- 只读态成员卡片上的“查看”按钮会跳转到该路由

## 3. 数据模型

组件输入非常简单：

```ts
{
  teamName: string;
  selectedAgents: string[];
  readonly?: boolean;
}
```

内部约定：

- `selectedAgents[0]` 是负责人 `leaderAgent`
- `selectedAgents.slice(1)` 是普通成员 `memberAgents`

这意味着当前关系图并不支持：

- 多层级结构
- 多个负责人
- 自定义父子关系
- 任意节点排序编辑

它本质上是一个固定的两层树：

```text
Leader
  ├─ Member 1
  ├─ Member 2
  ├─ ...
```

## 4. 渲染结构

### 4.1 组件层次

`TeamMembersCard`

- 负责面板壳层和标题“团队成员”
- 内部直接渲染 `TeamMemberGraph`

`TeamMemberGraph`

- 负责所有关系图可视化和交互

### 4.2 节点组成

当前每个节点不是单个按钮，而是：

```text
member-card-shell
  ├─ member-card-button   // 主卡片
  └─ member-action-button // hover/focus 时出现
```

负责人节点使用：

- `team-root-shell`
- `team-root member-card-button`

成员节点使用：

- `member-node-shell`
- `member-node member-card-button`

### 4.3 空槽位规则

`visibleMemberSlots` 在不同模式下的规则不同：

```ts
[
  ...memberAgents,
  { name: '', agent: '' }
]
```

作用：

- 编辑态显示 `+ 成员`
- 只读态不显示空槽位

也就是说：

- 编辑态会保留一个末尾空槽，作为补充成员入口
- 只读态只展示真实成员数据，不展示占位节点

## 5. 交互规则

### 5.1 主卡片点击

函数：`handlePrimaryAction(agentName)`

- 编辑态：点击负责人或成员卡片，触发 `emit('toggleAgent', agentName)`
- 只读态：点击主卡片不做业务动作

也就是说：

- 编辑态主卡片是“快速移除/切换”
- 只读态主卡片主要承担 hover、视觉反馈和拖拽承载

### 5.2 hover 操作按钮

函数：`handleActionButton(agentName)`

当前规则：

1. 编辑态
   hover 或 focus 节点时应显示 `编辑`
   如果该节点允许移除，还应额外显示 `移除`
   负责人卡片不显示 `移除`，因为 leader 不能被移除
   `编辑` 用于进入该成员的编辑动作，`移除` 才负责调用成员移除逻辑

2. 只读态
   hover 或 focus 节点时出现 `查看`
   点击后 `router.push({ name: 'agent-detail', ... })`

为了避免和拖拽冲突，操作按钮额外做了：

- `@pointerdown.stop`
- `@click.stop`

这意味着点击操作按钮不会把事件继续传给拖动画布逻辑。

### 5.3 hover 视觉反馈

节点 hover 时的视觉变化：

- `transform: translateY(-2px)`
- 边框高亮
- 背景切换为 `selected`

只读态当前也保留这套 hover 效果，不再额外覆盖掉。

## 6. 拖拽逻辑

### 6.1 拖拽入口

核心状态：

- `panX`
- `panY`
- `isPanning`

核心函数：

- `startPan`
- `movePan`
- `endPan`

触发方式：

- 在 `member-graph` 根容器上监听 pointer 事件
- 编辑态下，如果事件命中 `button`，不会启动拖拽
- 只读态下，节点本身也允许作为拖拽起点

### 6.2 画布结构

关系图并不是直接移动每个节点，而是整体移动 `.member-canvas`：

```ts
transform: translate(-50%, 0) translate(${panX}px, ${panY}px)
```

其中：

- `translate(-50%, 0)` 负责以中线为基础居中
- `panX/panY` 负责用户拖拽偏移

### 6.3 几何测量

函数：`updateMetrics()`

当前维护两套边界数据：

1. `baseContent*`
   用于绘制虚线包围框
   坐标系相对 `.member-canvas`

2. `dragContent*`
   用于计算拖拽边界
   坐标系相对 `.member-graph`
   但会减去当前 `panX/panY`，恢复成“未偏移的基础内容边界”

这样做的原因是：

- 虚线框的定位要跟随 canvas 内部坐标
- 拖拽夹取必须基于 graph 可视区域坐标
- 如果两者混用，会出现左右不对称或某一边越界的问题

### 6.4 拖拽边界规则

函数：`clampPan(nextX, nextY)`

当前规则是：

- 设定 `keepVisiblePx = 10`
- 横向保证边界框至少保留 `10px`
- 纵向同理

当前公式：

```ts
minX = keepVisiblePx - dragContentMaxRight
maxX = graphWidth - keepVisiblePx - dragContentMinLeft
minY = keepVisiblePx - dragContentMaxBottom
maxY = graphHeight - keepVisiblePx - dragContentMinTop
```

这套逻辑的语义是：

- 允许内容部分移出可视区
- 但不允许边界框完全离开视口

注意：

- 它不是“始终完整可见”
- 它也不是“边缘贴住才停止”
- 而是“至少保留一条 `10px` 的可见边”

### 6.5 默认复位位置

函数：`resetPan()`

逻辑是把内容边界放回 graph 视口的中间位置：

```ts
panX = (graphWidth - dragContentMaxRight - dragContentMinLeft) / 2
panY = (graphHeight - dragContentMaxBottom - dragContentMinTop) / 2
```

触发时机：

- `onMounted`
- `selectedAgents` 变化后
- `ResizeObserver` 响应容器尺寸变化后

## 7. 虚线包围框的作用

样式类：`.member-bounding-frame`

作用有两个：

1. 视觉调试
   直接把“当前参与拖拽边界计算的有效内容区域”画出来

2. 逻辑锚点
   `baseContentMinLeft/baseContentMaxRight/baseContentMinTop/baseContentMaxBottom`
   这四个值既驱动虚线框绘制，也代表“节点包围盒”

当前虚线框只包主节点，不包含 hover 出现的操作按钮。

这也是后续讨论“顶部/左右是否应该把悬浮按钮也算进边界”时的关键分界点：

- 如果边界只看主节点，hover 按钮可以先被裁切
- 如果边界要看可交互整体区域，就要把按钮也纳入测量

另外，拖拽边界有一条明确约束：

- 关系图四周边框应保持等宽
- 不允许在任意一个方向出现“还未到边界就提前遮挡”的情况
- 无论是主节点、连线、虚线边界框，还是 hover 出现的操作按钮，都应遵守这条规则

## 8. 当前实现的设计取舍

### 8.1 已经明确的规则

- 第一位成员就是负责人
- 关系图是固定两层结构
- 只读态允许拖拽
- 只读态不显示 `+ 成员` 空槽位
- 编辑态下 leader 卡片不显示 `移除`
- 编辑态主卡片点击直接切换成员
- 节点 hover/focus 会显示操作按钮
- 操作按钮不参与拖拽起点

### 8.2 当前仍值得确认的点

1. 拖拽边界应该以什么为准
   当前按主节点包围盒算，不包含 hover 操作按钮。
   但从交互期望看，四周边框应等宽，不能只在顶部、底部或某一侧提前遮挡，因此边界对象需要和最终可见内容保持一致。

2. 纵向裁切是否符合预期
   现阶段产品预期已经明确：
   不能接受某个方向先被裁切、而其他方向仍有富余空间的情况。
   也就是说，边界策略需要满足“四周边框等宽，不允许某个方向提前遮挡”。

3. 编辑态主卡片点击是否应保留
   当前主卡片点击和 hover 的 `移除` 按钮都能移除成员，存在一定语义重叠。

4. 只读态编辑入口的路由设计
   现在文档层面已明确：只读态右上角应显示“编辑”按钮，并能进入编辑态。
   但具体是跳转独立编辑页，还是复用现有创建/设置页中的某一态，还需要在实现前定下来。

## 9. 推荐的后续梳理顺序

如果后面要继续收敛这套编辑器，建议按下面顺序做决策：

1. 先确认“产品语义”
   它到底是“固定两层关系图”，还是准备演进成“可编辑组织树”。

2. 再确认“边界对象”
   拖拽边界到底按主节点、虚线框，还是“节点 + hover 操作按钮”的整体外接框来算。

3. 最后确认“编辑动作”
   编辑态是否保留主卡片点击移除，还是统一改成只有 hover 操作按钮执行动作。

只有这三点定下来，后面的拖拽、hover、裁切策略才会稳定，不会反复互相牵连。

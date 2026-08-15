# 前端质量问题修复计划

## 背景

本轮评审确认了几类已经影响到代码一致性和运行质量的问题：

1. 设置页 section 定义分叉，`App.vue` 会跳转到 `general`，但 `SettingsPage.vue` 不接受该 section。
2. 房间列表加载阶段存在预览 N+1 请求，并且预览展示依赖 `teamAgentsState`，会和并行加载产生竞态。
3. 团队摘要里的部门统计少算叶子部门。
4. 现有测试主要覆盖纯复制逻辑和简单组件，核心状态流没有直接护栏。

## 修复目标

### 1. 统一设置页 section 来源

- 抽出 settings section 常量，作为 `App.vue`、`SettingsPage.vue`、设置导航的统一来源。
- 把 `general`、`runtime` 纳入合法 section。
- 保持 `quickInit` 继续作为特殊动作，而不是普通路由 section。

### 2. 去掉房间预览的 N+1 和竞态

- `loadTeamRooms()` 只请求房间列表，不再为每个房间额外请求消息。
- 房间预览优先使用：
  - 当前缓存消息
  - 已有房间状态中的 preview
  - 默认空态文案
- 在消息加载成功、实时消息到达、Agent 列表更新后，统一重算受影响房间 preview，避免显示裸 id。

### 3. 修正团队摘要统计

- 抽出部门统计纯函数。
- `countDeptNodes()` 改为所有部门节点都计数，包括叶子节点。

### 4. 补充回归测试

- 为 settings section 常量提供集中定义，减少“写错字符串”类回归。
- 为房间预览纯函数补测试，覆盖缓存消息、历史 preview 回退、默认空态。
- 为部门统计纯函数补测试，覆盖叶子节点和层级计算。

## 实施步骤

1. 新增 settings section 常量模块，替换页面中的散落字符串。
2. 新增房间 preview 纯函数模块，重构 `runtimeStore.ts` 的房间加载和 preview 更新逻辑。
3. 新增团队摘要统计纯函数模块，替换 `SettingsPage.vue` 内联实现。
4. 补充 vitest 用例。
5. 跑 `npm run test:run` 和 `npm run build`。
6. 启动前端，在浏览器验证：
   - 顶部设置入口能进入系统状态页
   - 设置侧边栏可切换 `general / teams / roles / models / runtime`
   - 控制台页加载和房间切换正常
   - 创建房间后能正常返回控制台并刷新房间列表

## 验收标准

1. 点击顶部设置按钮后，不再被重定向到错误 section。
2. `SettingsPage.vue` 不再存在和导航定义分叉的 section 枚举。
3. 房间列表刷新不再触发每房间一次消息请求。
4. 已加载消息和实时消息都能正确更新 preview。
5. 部门数统计包含叶子部门。
6. 新增测试通过，构建通过，浏览器关键路径验证通过。

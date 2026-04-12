# 前端改造计划

## 1. 背景

当前前端整体可用，`build` 也能通过，但已经出现一批比较明确的技术债。

这些问题不是孤立的，主要集中在几类：

- WebSocket 连接与实时状态分散
- 核心页面和弹窗组件过大
- API 层混入 UI 副作用
- 全局状态边界不清晰
- 前端缺少 lint 和测试护栏

这份文档不展开实现细节，主要记录：

- 需要改造哪些项目
- 每个项目的目标方向
- 推荐的改造先后顺序

## 2. 改造项目清单

### 2.1 WebSocket 与实时状态中心

当前问题：

- `ConsolePage` 自己连 WS
- `AgentDetailDialog` 自己连 WS
- 同一个 `/ws/events.json` 可能被多个组件重复连接
- 实时事件解析分散在多个组件内
- 没有全局唯一的状态订阅和管理中心

目标方向：

- 单一 WebSocket 连接
- 全局唯一实时状态中心
- 页面和组件只订阅状态切片

### 2.2 ConsolePage 拆分

当前问题：

- [frontend/src/pages/ConsolePage.vue](/Volumes/PDATA/GitDB/TogoAgent/frontend/src/pages/ConsolePage.vue) 体量过大
- 页面里同时混合：
  - 路由逻辑
  - 房间/消息加载
  - WS 重连
  - 房间消息更新
  - Agent 状态更新
  - 左栏布局拖拽
  - 创建房间弹窗状态

目标方向：

- 拆成页面壳 + composables + 较轻的展示组件
- 页面只保留编排职责

### 2.3 AgentActivityDialog 拆分

当前问题：

- [frontend/src/components/AgentActivityDialog.vue](/Volumes/PDATA/GitDB/TogoAgent/frontend/src/components/AgentActivityDialog.vue) 已经是大型组件
- 同时承担：
  - 详情拉取
  - 活动列表拉取
  - WS 连接
  - 活动流 upsert
  - 自动滚动
  - 状态展示

目标方向：

- 详情展示和活动流状态分开
- 实时活动订阅接入全局中心
- 弹窗只保留展示和交互

### 2.4 SettingsPage 拆分

当前问题：

- [frontend/src/pages/SettingsPage.vue](/Volumes/PDATA/GitDB/TogoAgent/frontend/src/pages/SettingsPage.vue) 体量过大
- 团队概览、团队详情、目录信息、确认弹窗、路由切换都压在同一页里

目标方向：

- 按 section 拆成 composables
- SettingsPage 只保留路由编排和页面壳

### 2.5 API 层去 UI 副作用

当前问题：

- [frontend/src/api.ts](/Volumes/PDATA/GitDB/TogoAgent/frontend/src/api.ts) 中直接调用全局 toast
- API 层现在同时承担：
  - 请求发送
  - 错误解析
  - UI 错误提示

目标方向：

- API 层只负责返回数据或抛错误
- UI 提示交给页面、action 或状态层决定

### 2.6 全局状态边界收敛

当前问题：

- [frontend/src/appUiState.ts](/Volumes/PDATA/GitDB/TogoAgent/frontend/src/appUiState.ts) 维护一部分全局状态
- [frontend/src/teamStore.ts](/Volumes/PDATA/GitDB/TogoAgent/frontend/src/teamStore.ts) 维护另一部分全局状态
- 页面内部还有一些“事实状态”保存在局部
- 状态来源分散，后续会越来越难追踪

目标方向：

- 明确哪些属于全局共享状态
- 明确哪些保持页面局部状态
- 避免同一事实在多个层次重复维护

### 2.7 统一 normalize 层

当前问题：

- normalize 逻辑散落在 `api.ts`、`ConsolePage.vue`、`AgentDetailDialog.vue`
- 同一类后端字段兼容逻辑重复出现

目标方向：

- 建立统一 normalize 层
- 页面不直接消费后端原始结构

### 2.8 工程护栏

当前问题：

- [frontend/package.json](/Volumes/PDATA/GitDB/TogoAgent/frontend/package.json) 目前只有 `dev/build/preview`
- 没有 lint
- 没有前端测试

目标方向：

- 增加 lint
- 增加最小测试护栏
- 给关键实时链路和核心页面提供回归保障

## 3. WebSocket 与全局状态中心方案

### 3.1 单一连接

前端全局只维护一条 `/ws/events.json` 连接。

### 3.2 全局唯一状态中心

前端需要一个统一的运行时状态中心，负责：

- 接收 WebSocket 原始事件
- 归一化事件结构
- 更新前端全局状态
- 暴露查询和订阅接口

### 3.3 页面按需订阅

页面和组件只订阅自己需要的状态切片，例如：

- `ConsolePage` 订阅当前 team 的房间、消息、agent 状态、连接状态
- `AgentActivityDialog` 订阅指定 agent 的状态和活动流

## 4. 推荐目录结构

建议新增：

```text
frontend/src/realtime/
├── wsClient.ts
├── eventNormalizer.ts
├── runtimeStore.ts
└── selectors.ts
```

建议职责：

- `wsClient.ts`
  负责唯一连接和重连
- `eventNormalizer.ts`
  负责统一事件和数据结构
- `runtimeStore.ts`
  负责全局唯一实时状态
- `selectors.ts`
  负责向页面暴露稳定的查询接口

## 5. 建议的改造顺序

### 第一阶段：建立实时基础设施 `已完成`

先建立：

- `wsClient.ts`
- `eventNormalizer.ts`
- `runtimeStore.ts`
- `selectors.ts`

目标：

- 先把单一连接和全局状态中心建起来
- 不急着大规模改页面

当前进度：

- 已建立全局唯一 WS 连接
- 已建立统一事件 normalize 层
- 已建立 `runtimeStore`
- 已建立 `selectors`
- `App.vue` 已接入全局 realtime client

### 第二阶段：迁移 ConsolePage `进行中`

优先迁移 `ConsolePage`，因为它是当前最重、最核心、最容易继续膨胀的页面。

目标：

- 去掉页面内的 WS 连接和重连逻辑
- 去掉页面内的实时事件分发逻辑
- 保留页面自己的路由、输入框、布局状态

当前进度：

- `ConsolePage` 已移除自建 WS 与重连逻辑
- 房间列表、成员列表、消息窗口已拆成独立面板组件
- 运行时数据读取已接到 `useConsoleRuntimeState.ts`
- 消息滚动逻辑已抽到 `useConsoleMessageScroll.ts`
- 仍待继续下沉：
  - 左侧分栏拖拽
  - 创建房间弹窗状态与提交流程
  - 其他页面级编排状态

### 第三阶段：迁移 AgentActivityDialog `部分完成`

在实时中心稳定后，再迁移 `AgentActivityDialog`。

目标：

- 去掉它自己的 socket 与重连逻辑
- 将 agent 活动流改为全局订阅
- 组件只保留展示和交互

当前进度：

- 组件已从 `AgentDetailDialog` 更名为 `AgentActivityDialog`
- 已移除组件自建 WS 与重连逻辑
- 已改为通过 `runtimeStore` + `selectors` 读取活动和状态
- 后续仍可继续拆出更轻的详情展示和活动列表逻辑

### 第四阶段：收敛全局状态边界 `未开始`

在 `runtimeStore` 起来之后，再统一收敛：

- `appUiState`
- `teamStore`
- 页面中的局部事实状态

目标：

- 让“全局共享状态”和“页面局部状态”边界稳定下来

### 第五阶段：收敛 normalize 逻辑 `部分完成`

把散落在页面、弹窗和 API 层里的 normalize 逻辑统一收进一层。

目标：

- 页面不再直接兼容后端原始结构
- 后端字段变化时，影响范围尽量小

当前进度：

- WebSocket 事件 normalize 已进入 `frontend/src/realtime/eventNormalizer.ts`
- 页面读接口已开始通过 `frontend/src/realtime/selectors.ts` 收口
- API 返回结构 normalize 仍有一部分留在 `api.ts`

### 第六阶段：拆 SettingsPage `未开始`

实时链路稳定后，再处理 `SettingsPage` 这类大页面。

原因：

- 它复杂，但不是实时链路核心路径
- 适合放在实时改造之后单独推进

### 第七阶段：去掉 API 层 UI 副作用 `未开始`

把 `api.ts` 中直接触发 toast 的逻辑逐步迁走。

原因：

- 这是长期维护问题
- 但不需要抢在实时层之前处理

### 第八阶段：补工程护栏 `未开始`

最后补：

- lint
- 最小前端测试
- 关键页面和实时链路回归测试

原因：

- 在主要结构稳定后补，收益最大

## 6. 当前推荐结论

推荐采用的总体方向是：

- 单一 WebSocket 连接
- 全局唯一实时状态中心
- 页面按需订阅状态切片
- 大页面逐步拆分
- API 层逐步去 UI 副作用
- 最后补 lint 和测试护栏

不推荐继续沿用：

- 每个组件自己连 socket
- 每个组件自己做事件解析和重连
- 页面本地状态直接充当后端实时真相
- 在没有测试护栏的情况下继续堆大组件

## 7. 当前阶段性进度

已完成：

- 全局唯一 WebSocket 连接
- 统一 realtime 事件 normalize
- `runtimeStore` 与 `selectors`
- `ConsolePage` 的三块主面板拆分
- `ConsolePage` 的运行时状态与消息滚动 composable 拆分
- `AgentActivityDialog` 接入全局实时状态中心

进行中：

- 继续收敛 `ConsolePage` 的页面编排逻辑

未开始：

- `SettingsPage` 拆分
- API 层去 UI 副作用
- lint 与前端测试护栏

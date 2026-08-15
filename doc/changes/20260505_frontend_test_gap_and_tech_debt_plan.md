# 前端测试缺口与技术债治理计划

## 背景

本轮前端代码巡检确认了两个问题同时存在：

1. 当前测试基线是绿的，但覆盖面偏窄，不能有效保护核心状态流。
2. 一些关键页面和状态模块已经累积出明显的结构性技术债，继续直接加功能，回归成本会越来越高。

当前 `frontend/src` 下有大量页面、组件和状态逻辑，但现有测试主要集中在：

- 少量纯函数
- 一个基础 UI 组件
- `runtimeStore` 的单条消息排序分支

结论：现在不是“完全没测试”，而是“测试对高风险逻辑保护不够”。

## 现状判断

### 1. 测试覆盖结构失衡

现有测试大多覆盖容易测的纯逻辑，真正高风险的部分反而没有护栏：

- WebSocket 生命周期
- 控制台页的路由与实时状态同步
- 设置页的数据加载、保存、确认流
- 团队组织树编辑器的真实保存链路
- API 层的鉴权和错误处理

### 2. 大文件承担了过多职责

以下文件都已经接近或超过“应当拆分”的体量：

- `src/pages/SettingsPage.vue`
- `src/components/team/TeamTreeEditor.vue`
- `src/realtime/runtimeStore.ts`
- `src/realtime/wsClient.ts`
- `src/api.ts`

这些文件的问题不只是“长”，而是：

- 状态源混在一起
- 副作用和视图逻辑耦合
- 同一文件同时负责数据加载、状态变换、错误处理、UI 反馈
- 使真实测试很难落地

## 核心问题清单

### 1. `wsClient.ts` 缺少状态机测试

文件：`src/realtime/wsClient.ts`

当前风险：

- 首次连接、重连、超时、鉴权、异常关闭都在一个状态机里。
- 代码分支多，但目前没有对应测试。
- 鉴权模式下连接建立后的状态切换依赖后端首条消息，存在长时间停留在 `connecting` 的风险。

建议：

- 为连接成功、重连、超时、无 token、鉴权成功、异常关闭分别补测试。
- 优先把“状态迁移规则”从副作用里抽出来，至少做到可以通过 mock socket 稳定验证。

### 2. `runtimeStore.ts` 只测到了一个分支

文件：`src/realtime/runtimeStore.ts`

当前风险：

- 实时消息写入
- 未读数更新
- 房间 preview 更新
- `agent_status`
- `room_status`
- `schedule_state`
- `room_added`

这些都是控制台页的核心行为，但当前只覆盖了 `message_changed` 的重新排序。

建议：

- 把 `applyRealtimeEvent()` 当成 reducer 来测。
- 按事件类型补成表驱动测试。
- 补上消息去重、当前房间 unread 清零、preview 回写等关键断言。

### 3. `SettingsPage.vue` 是主要技术债之一

文件：`src/pages/SettingsPage.vue`

当前问题：

- 路由 section 处理、团队详情加载、摘要聚合、保存、启停团队、删除团队、清空数据、面包屑、滚动条 hover 状态，都在一个 SFC 中。
- 存在多处“操作后手动刷新 + watcher 再刷新”的重复请求模式。
- 当前没有针对这些流程的测试。

建议：

- 拆出 composable：
  - `useSettingsRouting`
  - `useTeamSummaries`
  - `useTeamDetailState`
  - `useTeamMutations`
- 先拆状态与副作用，再补测试。
- 不建议继续直接往这个页面追加业务分支。

### 4. `TeamTreeEditor.vue` 的测试没有测到真实实现

文件：

- `src/components/team/TeamTreeEditor.vue`
- `src/components/team/__tests__/TeamTreeEditorUtils.test.ts`

当前问题：

- 现有测试文件复制了一份组件内部纯函数逻辑。
- 这意味着测试通过，不等于组件真实实现没有回归。
- 真实高风险链路还没有覆盖：
  - 首屏加载
  - 在职成员过滤
  - 新成员编辑与重复名校验
  - 两阶段保存
  - 部门树回填
  - 删除成员和 pending slot 流程

建议：

- 把纯函数正式抽到独立模块，停止“复制实现到测试文件”的做法。
- 将保存 payload、部门树 payload、树遍历工具拆到独立文件后直接测试真实导出函数。
- 再补少量组件级测试，验证关键交互链路。

### 5. `api.ts` 集中了过多协议与错误处理逻辑

文件：`src/api.ts`

当前问题：

- 请求 URL、鉴权 header、401 处理、全局错误 toast、后端不可达提示、WebSocket URL 构造、响应归一化，全都放在同一个文件。
- 一旦错误分支回归，影响范围是全局的。
- 当前没有对 `requestJson()` 关键分支的测试。

建议：

- 先补 `requestJson()` 的分支测试：
  - 正常请求
  - 401 + `auth_required`
  - 代理层后端不可达
  - 非 JSON 错误响应
  - 网络异常
- 后续按职责拆成：
  - `apiClient.ts`
  - `apiError.ts`
  - `apiMappers.ts`
  - `wsUrl.ts`

### 6. `ConsolePage.vue` 与 `useConsoleRuntimeState.ts` 缺少流程级测试

文件：

- `src/pages/ConsolePage.vue`
- `src/composables/useConsoleRuntimeState.ts`

当前问题：

- 房间路由参数、当前选中房间、实时上下文、首屏刷新、重连后刷新、移动端侧栏状态互相影响。
- 当前没有测试证明这些 watcher 组合在关键路径上始终正确。

建议：

- 先为 `useConsoleRuntimeState()` 补单测。
- 覆盖：
  - 切房间时是否同步更新 route
  - `force` / `syncRoute` / `replaceRoute` 分支
  - `clearSelectedRoom()` 和 `clearRuntimeContext()`
- 后续再补 `ConsolePage.vue` 的轻量集成测试。

### 7. `teamStore.ts` 体量不大，但属于低成本补齐项

文件：`src/teamStore.ts`

当前问题：

- `localStorage` 读写
- `loadTeams()` 成功 / 失败状态
- `clearTeams()`
- `preferredTeamId`

这些逻辑都不复杂，但目前也没有测试。

建议：

- 作为低成本单测项尽快补齐。
- 这部分适合作为前端测试基线模板，给后续 store/composable 测试提供范例。

## 推进策略

原则：先给高风险状态逻辑补护栏，再拆最重的页面与组件。

### 第一阶段：先补最值钱的状态测试

目标：

- 给最容易引发线上回归的状态流建立护栏

范围：

- `src/realtime/wsClient.ts`
- `src/realtime/runtimeStore.ts`
- `src/composables/useConsoleRuntimeState.ts`
- `src/teamStore.ts`
- `src/api.ts` 的 `requestJson()` 关键错误分支

验收：

1. 关键状态迁移有稳定测试。
2. 重连、未读数、路由同步、鉴权失败等场景可回归。
3. 不改业务行为的前提下，测试可以稳定运行。

### 第二阶段：拆设置页

目标：

- 把 `SettingsPage.vue` 从“大而全页面”拆成可测状态单元

范围：

- 路由 section 状态
- 团队摘要加载
- 团队详情加载
- 团队保存与危险操作

验收：

1. 页面主要副作用进入 composable。
2. 重复刷新链路减少。
3. 关键保存流和路由流可测试。

### 第三阶段：拆组织树编辑器

目标：

- 让组织树逻辑不再依赖“复制实现到测试文件”

范围：

- 树遍历工具
- payload 生成
- draft / committed 状态转换
- 两阶段保存辅助逻辑

验收：

1. `TeamTreeEditorUtils.test.ts` 不再复制生产实现。
2. 关键树操作直接测试真实导出函数。
3. 保存链路至少有一组组件级回归测试。

## 实施顺序建议

1. 先做第一阶段，不先动页面结构。
2. 第一阶段完成后，评估哪些状态边界已经被测试锁住。
3. 再开始拆 `SettingsPage.vue`。
4. 最后处理 `TeamTreeEditor.vue`，因为这块逻辑最重、影响面最大。

## 暂不建议现在做的事

1. 暂不建议直接追求测试覆盖率数字。
2. 暂不建议一口气把所有大文件全部拆完。
3. 暂不建议先写大量组件快照测试。

原因：

- 当前最缺的是“高风险行为的精确测试”，不是覆盖率报表。
- 如果没有阶段边界，重构范围会迅速失控。
- 快照测试对这里的状态问题帮助有限。

## 决策建议

建议按下面顺序推进：

1. 先做第一阶段测试补齐。
2. 第一阶段完成后再决定是先拆设置页，还是先拆组织树编辑器。
3. 如果希望风险最小，优先拆设置页。
4. 如果希望尽快降低后续功能开发阻力，再处理组织树编辑器。

## 产出定义

本计划文档的用途是：

- 作为后续前端治理的任务清单
- 帮助逐阶段决策
- 避免在未建立测试护栏前直接大改高风险页面

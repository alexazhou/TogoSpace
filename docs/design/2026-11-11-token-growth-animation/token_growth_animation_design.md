# Token 数字增长放大动画设计

- 日期：2026-11-11
- 状态：已实现
- 影响范围：`frontend/src/components/agent/AgentActivityToken.vue`、`frontend/src/components/agent/AgentActivityItem.vue`、`frontend/src/composables/useGrowingScale.ts`
- Demo：[demo/token-growth-demo.html](./demo/token-growth-demo.html)

## 1. 背景与目标

Agent 活动列表里的推理 token 数字，原先靠 `useAnimatedNumber` 做 250ms 数值补间：数字平滑增长，但视觉上只是「数字在变」，流式输出很快时缺少节奏感。

目标是给这行数字加一点「游戏式」的跳动感：每次 token 进度更新时数字弹一下，观感与数据到达的节奏一致，同时**不能干扰阅读**——数字位置、箭头位置、左侧文字都不能被动画推着走。

经过几轮调整，最终确定的规则是「每次更新弹一下，弹多高由本次速度决定，500ms 内衰减回原大小」。本文件记录最终方案、算法参数、实现位置和验证结果。

### 迭代过程（结论追溯）

| 阶段 | 方案 | 问题 |
|------|------|------|
| v1 | 整块 token 文本整体缩放 | 放大时把左侧文字顶开、整串向左移动 |
| v2 | 按「↑ 输入」「↓ 输出」分段缩放 | 段内的箭头也跟着放大、位置漂移 |
| v3 | 箭头与数字拆成独立节点，只缩放数字 | 位置稳定，但放大后长时间保持大小不变，不够「跳」 |
| v4 | 每次更新弹起后衰减，固定峰值 | 有跳动感，但快流和慢流的幅度一样 |
| v5（最终） | 峰值由本次速度决定，500ms 线性衰减 | 快流弹得大、慢流轻轻跳，连续推送自然接上 |

## 2. 效果规则（视觉约定）

1. **只有数字缩放**。`↑` / `↓` 箭头、空格、左侧输入数字都不参与动画，位置和大小完全不变。
2. **数字左边缘锚定**。`transform-origin: left center`，放大时向右生长，不会把左边的箭头或文字顶开。
3. **只改 `transform`**。不使用 `font-size`、`width` 等会触发 reflow 的属性，布局宽度始终按原始字号计算。
4. **每次数值变大触发一次**。首次渲染、数值不变、数值变小都不触发；正在进行的衰减不会被中途取消，只是不再叠加新峰值。
5. **幅度随速度**。本次增长越快，峰值越高；再慢也保留一点跳动，再快也有上限。
6. **连续流不抖**。后端约 200ms 更新一次，衰减周期 500ms，下一次更新时上一次放大还剩约六成，新峰值与当前值取大者，因此连续推送形成连贯的「呼吸」而不是「掉回 1 再重弹」。
7. **尊重系统设置**。`prefers-reduced-motion: reduce` 时完全不启动动画，尺寸恒为 1。

## 3. 算法

### 3.1 速度测量

在 composable 内部对目标值做 `watch`（`flush: 'sync'`），每次值变化时计算：

```text
elapsedMs = now - previousTime
speed     = (next - previous) / (elapsedMs / 1000)   // tokens/s
```

只统计「变大」的更新。第一次拿到值（`previous === null`）直接记录时间基准，不触发动画；`elapsedMs <= 0` 时视速度为无穷大，直接取上限。

### 3.2 峰值映射

```text
peak = 1 + clamp(speed × 0.0028, 0.06, 0.30)
```

| 速度 | 峰值缩放 |
|------|----------|
| 0 t/s（下限） | 1.06 |
| 50 t/s | 1.14 |
| 100 t/s | 1.28 |
| ≥ 107 t/s | 1.30（上限） |

`0.0028` 的含义是每 1 t/s 增加 0.28% 的尺寸。上下限的作用：极慢的流（如 20 t/s 以下）仍有 6% 的可见跳动；极快的流不会让数字大到破坏行内排版。

### 3.3 衰减与连续性

峰值设定后，用 `requestAnimationFrame` 逐帧计算：

```text
progress = clamp((now - decayStart) / 500, 0, 1)
scale    = 1 + (decayFrom - 1) × (1 - progress)
```

- 线性衰减，500ms 整回到 1，然后取消 rAF，不再排帧。
- 每次新更新：`decayFrom = max(当前 scale, 新峰值)`，`decayStart = now`。取 `max` 保证新峰值不会低于当前值，避免向下的跳变。
- 衰减期间数值变小：不触发新峰值，让已有衰减自然走完。

以 50 t/s、200ms 更新为例（无中间渲染误差时）：

| 时刻 | 事件 | scale |
|------|------|-------|
| 0ms | 第一次增长，峰值 1.14 | 1.140 |
| 200ms | 下一帧衰减到 1.084，同时新峰值 1.14 接上 | 1.140 |
| 400ms | 衰减到 1.084，新峰值接上 | 1.140 |
| ... | 持续流保持约 1.08–1.14 的呼吸区间 | |

### 3.4 参数表

| 常量 | 值 | 位置 | 说明 |
|------|-----|------|------|
| `GROWING_DECAY_MS` | 500 | `useGrowingScale.ts` | 衰减周期；与 200ms 更新节奏匹配 |
| `SCALE_PER_SPEED` | 0.0028 | 同上 | 速度 → 幅度斜率 |
| `MIN_SCALE_EXTRA` | 0.06 | 同上 | 最小放大增量 |
| `MAX_SCALE_EXTRA` | 0.30 | 同上 | 最大放大增量 |

调整建议：若觉得整体太夸张，先降 `SCALE_PER_SPEED` 与 `MAX_SCALE_EXTRA`；若觉得连续流抖动，先加大 `GROWING_DECAY_MS`（但不要超过更新间隔的 3 倍以上，否则会长期保持放大状态）。

## 4. 实现

### 4.1 `useGrowingScale`

文件：`frontend/src/composables/useGrowingScale.ts`

```ts
export function useGrowingScale(
  target: () => number | null,
  options: GrowingScaleOptions = {},
): Ref<number>
```

- 入参是 getter，返回缩放值的 `Ref<number>`，组件直接绑定到 inline style。
- 内部状态：`previous` / `previousTime`（测速）、`decayFrom` / `decayStart`（当前衰减）、`raf`（帧句柄）。
- `scaleForSpeed(speed)` 独立导出，便于单测和复用。
- `options.now` 允许测试注入时钟；`options.decayMs` 允许覆盖衰减周期。
- `onScopeDispose` 里停止 watch 并 `cancelAnimationFrame`，组件卸载后不再响应、不再排帧。

### 4.2 组件接线

token 展示块已经从 `AgentActivityItem.vue` 拆成独立组件 `AgentActivityToken.vue`：metadata 推导、数值补间、增长放大、箭头/数字渲染和 token 相关样式都收在组件内部；父组件只渲染 `<AgentActivityToken :activity="activity" />`。这样 `AgentActivityItem`（约 1700 行）不再承载 token 细节，token 规则也有单独的落点。

```ts
const tokenUpScale = useGrowingScale(() => tokenUpTarget.value);
const tokenDownScale = useGrowingScale(() => tokenDownTarget.value);
```

输入、输出两段各自独立实例，互不影响：只有真正在增长的那一段会放大。

token 视图的 computed 里把箭头和数字拆成两个字段：

```ts
upArrow: hasUp ? `${t('agent.tokenInferenceUpArrow')} ` : '',
upNumber: hasUp ? String(upValue) : '',
downArrow: hasDown ? ` ${t('agent.tokenInferenceDownArrow')} ` : '',
downNumber: hasDown ? String(downValue) : '',
```

模板中箭头与数字是相邻的两个 `<span>`，只有数字节点写 inline `transform`：

```html
<span class="agent-activity-item__tokens-arrow">{{ tokenView.upArrow }}</span><span
  class="agent-activity-item__tokens-number"
  :style="{ transform: `scale(${tokenUpScale})` }"
>{{ tokenView.upNumber }}</span>
```

CSS：

```css
.agent-activity-item__tokens-arrow { display: inline-block; }

.agent-activity-item__tokens-number {
  display: inline-block;
  transform-origin: left center;
  will-change: transform;
}
```

注意模板里箭头与数字之间不能有换行/缩进空白以外的额外空格——空格被刻意放进箭头字符串（`'↑ '` / `' ↓ '`），保证 DOM 文本与改造前的「↑ 8421 ↓ 20」逐字一致。现有测试 C1–C7 会检查文本内容，这也是拆分方案能落地的前提。

### 4.3 i18n

原先「↑ / ↓」写死在 `tokenInferenceUp` / `tokenInferenceDown` 的完整文案里。拆分后新增两个只含符号的 key：

```json
"tokenInferenceUpArrow": "↑",
"tokenInferenceDownArrow": "↓"
```

`zh-CN.json` 与 `en.json` 同步添加。完整文案 key 保持不动，tooltip（`title`）仍使用目标文案。

### 4.4 无障碍与清理

- `prefers-reduced-motion: reduce`：`useGrowingScale` 在初始化时直接返回恒为 1 的 ref，不注册 watch、不排帧。
- 作用域销毁：`onScopeDispose` 停止 watch 并取消在途 rAF；组件卸载后的测试用例验证不会再排新帧。
- 动画只改 `transform`，不触发 layout；`will-change: transform` 提前提升合成层，避免首帧抖动。

## 5. Demo

独立 Demo：[`demo/token-growth-demo.html`](./demo/token-growth-demo.html)

- 单文件、无外部依赖，直接用浏览器打开即可（不需要 Vite 或后端）。
- 算法与 `useGrowingScale.ts` 保持一致：同样的常量、同样的「增量 / 实际经过时间」测速方式。
- 默认随机速度 50–100 t/s，每 200ms 更新一次；可切换为固定速度滑杆（10–150 t/s）。
- 支持暂停/继续；实时显示当前速度、本次峰值、当前缩放值。
- 列表里有一条静态对照行，方便观察「只有数字缩放、箭头和左侧文字不动」。

Demo 是给设计/评审看的可视化参考，不参与构建，也不被前端代码引用。

## 6. 验证

### 6.1 单元测试

`frontend/src/composables/__tests__/useGrowingScale.test.ts`（S1–S10）：

| 用例 | 判据 |
|------|------|
| S1 | 速度映射：0 → 1.06，50 → 1.14，100 → 1.28，极大值夹到 1.30 |
| S2 | 首次值不放大 |
| S3 | 200ms 增 10（50 t/s）峰值 1.14 |
| S4 | 200ms 增 20（100 t/s）峰值 1.28 |
| S5 | 250ms 时 1.07，500ms 回到 1 且不再排帧 |
| S6 | 连续推送能接上：200ms 后从 1.084 直接接回 1.14，不掉回 1 |
| S7 | 150 t/s 触顶 1.30 |
| S8 | 变小不触发新峰值，已有衰减继续 |
| S9 | 卸载后不再响应目标变化 |
| S10 | reduced-motion 下恒为 1 |

`frontend/src/components/agent/__tests__/AgentActivityItem.grow.test.ts`（G-A1–G-A5）验证组件接线：

- 输入、输出两段各自独立缩放，只有变化的一段放大；
- 数值变小时不放大；
- 箭头节点没有 inline transform，且动画过程中节点不被替换；
- 两段同时增长时各自取到自己的峰值。

回归：`AgentActivityItem.tween.test.ts` C1–C7 全部通过；其中 C7 的「在途帧数量」断言从 `toBe(1)` 改为 `toBeGreaterThan(0)`，因为补间与增长放大现在各自会排帧，数量归属不是该用例的判据。

执行结果：4 个测试文件 / 29 个用例通过，`npx vue-tsc --noEmit` 无错误。

### 6.2 运行时采样

在 Demo 的随机 50–100 t/s 模式下，用浏览器脚本采样峰值附近的缩放值：

| 本次速度 | 峰值（测得） | 理论值 |
|----------|--------------|--------|
| 51 t/s | 1.13 | 1.143 |
| 76 t/s | 1.21 | 1.213 |
| 87 t/s | 1.22 | 1.244 |
| 99 t/s | 1.26 | 1.277 |

测得值略低于理论值，原因是采样发生在峰值设定后的第一帧（已衰减约 16ms），属于预期。动画期间箭头的 `left` 保持 904.5px 不变，验证了「箭头位置固定」。

## 7. 边界与已知限制

- **只在数值变大时触发**。数值变小（流式估算回落到后端真值）直接落位，不播放反向动画——这是 `useAnimatedNumber` 已有的约定，增长动画沿用。
- **首值不弹**。组件首次挂载时没有历史速度，无法定义「本次速度」，因此从 1 开始。
- **放大是视觉溢出**。`transform` 不改变布局盒宽度，最大 1.30 时数字视觉上会超出自身盒宽约 30%。对 6 位数（约 90px）意味着约 27px 的右向溢出；如果未来 token 数位数更长、或右侧增加了紧邻元素，可下调 `MAX_SCALE_EXTRA` 或在数字后预留 `padding-right`。
- **仅作用于活动列表的 token 数字**。其他位置的数字（如上下文容量进度条）不共享此动画。
- **不引入缓动曲线**。线性衰减在 500ms、60fps 下已足够平滑，且与 200ms 的固定更新节奏配合更可预测；若改用 ease-out，连续推送时「接上」的位置会变化，需要重新调参。

## 8. 相关文件

| 文件 | 作用 |
|------|------|
| `frontend/src/composables/useGrowingScale.ts` | 增长放大算法与生命周期 |
| `frontend/src/components/agent/AgentActivityToken.vue` | token 显示块：metadata 推导、补间、增长放大、箭头/数字 DOM 拆分、样式 |
| `frontend/src/components/agent/AgentActivityItem.vue` | 活动行主体；渲染 `<AgentActivityToken>`，不再包含 token 细节 |
| `frontend/src/locales/zh-CN.json` | 新增 `tokenInferenceUpArrow` / `tokenInferenceDownArrow` |
| `frontend/src/locales/en.json` | 同上 |
| `frontend/src/composables/__tests__/useGrowingScale.test.ts` | S1–S10 |
| `frontend/src/components/agent/__tests__/AgentActivityItem.grow.test.ts` | G-A1–G-A5 |
| `frontend/src/components/agent/__tests__/AgentActivityItem.tween.test.ts` | 回归 C1–C7 |
| `docs/design/2026-11-11-token-growth-animation/demo/token-growth-demo.html` | 独立可视化 Demo |

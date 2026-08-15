# Frontend Theme Schema 重构方案

## 背景

当前前端主题系统已经具备基本能力：

- 通过 `frontend/src/style.css` 中的 `:root` 定义默认主题变量
- 通过 `:root[data-theme='light']` 覆盖 light 主题变量
- 通过 `frontend/src/App.vue` 将 `theme-mode` 写入 `document.documentElement.dataset.theme`

整体机制是成立的，但变量体系已经出现语义漂移和局部失配，主要问题包括：

- 存在未定义但被使用的变量，例如 `--text`、`--panel-bg-elevated`、`--banner-error-border`
- 存在历史业务命名变量被跨场景复用，例如 `--team-create-panel-border`
- 存在语义重复或边界不清的变量，例如 `--panel-border` 和 `--panel-border-strong`
- 组件内仍有少量零散硬编码颜色，削弱主题一致性

这会导致两个直接问题：

- 维护者难以判断新样式应该依赖哪个变量
- 后续继续扩展主题时，变量数量会增长但语义不会更清晰

因此，本次建议不是继续局部修补，而是先定义一套稳定的 Theme Schema，再分批迁移组件。

## 目标

- 建立一套统一、可扩展的主题变量 schema
- 保证 dark 和 light 使用完全一致的变量集合
- 让组件只依赖标准语义 token，而不是页面历史命名
- 消除未定义变量和明显冗余变量
- 为后续新增页面和组件提供统一主题接入方式

## 非目标

- 本次方案不追求一次性重写全部样式
- 本次方案不改变页面布局、交互逻辑和主题切换机制
- 本次方案不引入第三方主题库或 CSS-in-JS 方案

## 设计原则

### 1. 只保留语义 token

变量名应该表达“视觉角色”，而不是“业务来源”。

推荐：

- `--surface-panel`
- `--surface-chat`
- `--surface-pill`
- `--text-primary`
- `--border-default`

不推荐：

- `--team-create-control-border`
- `--room-title-text`

### 2. 组件消费稳定分层

组件内优先只使用以下几类 token：

- page
- surface
- text
- border
- interactive
- state
- shadow

### 3. 同一 schema 适配多主题

dark 和 light 必须定义同一批字段，只允许值不同，不允许字段集合不同。

### 4. 允许兼容过渡，不允许长期双轨

迁移阶段可以保留旧变量映射，但最终组件应迁移到新 schema，旧变量需要逐步删除。

## 推荐 Theme Schema

第一版 schema 建议控制在少量高频核心 token 内，先覆盖 80% 的通用视觉需求。

### Page

- `--bg-canvas`
- `--bg-canvas-accent-left`
- `--bg-canvas-accent-right`

### Surface

- `--surface-page`
  页面基底
- `--surface-panel`
  主面板背景
- `--surface-panel-muted`
  次级容器、列表卡片等
- `--surface-panel-deep`
  更深一层的背景，如滚动区轨道等
- `--surface-overlay`
  弹层、菜单、浮层背景
- `--surface-elevated`
  高于普通容器的背景，用于头像底板、悬浮徽标等
- `--surface-chat`
  聊天主舞台背景
- `--surface-input`
  输入区背景
- `--surface-pill`
  顶栏胶囊、轻量按钮、统计 pill 背景

### Text

- `--text-primary`
  主文案
- `--text-secondary`
  次级说明、辅助文案
- `--text-tertiary`
  更弱提示、禁用或低优先级文字
- `--text-on-accent`
  强强调背景上的文字

### Border

- `--border-subtle`
  轻边框、分隔线
- `--border-default`
  常规边框
- `--border-strong`
  强调边框、结构边界

### Interactive

- `--interactive-selected`
  激活态背景
- `--interactive-hover-border`
  hover 边框
- `--interactive-focus-border`
  focus 边框
- `--interactive-focus-ring`
  focus 光晕

### State

- `--state-success`
- `--state-danger`
- `--state-warning`
- `--state-info`

### Shadow

- `--shadow-panel`

### Component-Scoped Alias

这类变量不建议作为全局主 schema，但在部分确实需要局部语义的场景下可以保留：

- `--scrollbar-track`
- `--scrollbar-thumb`
- `--scrollbar-thumb-hover`
- `--bubble-left`
- `--bubble-right`
- `--bubble-right-text`

这些变量应建立在基础 schema 之上推导，而不是随意独立命名。

## 参考变量定义

以下是第一版建议的变量形态，供实现时参考。

```css
:root {
  --surface-page: #0e1621;
  --surface-panel: #17212b;
  --surface-panel-muted: #202b36;
  --surface-panel-deep: #101822;
  --surface-overlay: rgba(23, 33, 43, 0.96);
  --surface-elevated: #223040;
  --surface-chat: var(--surface-page);
  --surface-input: color-mix(in srgb, var(--surface-panel) 74%, var(--surface-panel-deep) 26%);
  --surface-pill: var(--surface-elevated);

  --text-primary: #d9e1ea;
  --text-secondary: #7f91a4;
  --text-tertiary: #4c5e72;
  --text-on-accent: #fffaf5;

  --border-subtle: #243f57;
  --border-default: #3a6080;
  --border-strong: #44586f;

  --interactive-selected: #2b5278;
  --interactive-hover-border: #365c82;
  --interactive-focus-border: #365c82;
  --interactive-focus-ring: rgba(54, 92, 130, 0.24);

  --state-success: #56d4b0;
  --state-danger: #f85149;
  --state-warning: #ffce54;
  --state-info: #69aae6;

  --shadow-panel: 0 10px 30px rgba(0, 0, 0, 0.24);
  color-scheme: dark;
}

:root[data-theme='light'] {
  --surface-page: #edf3fb;
  --surface-panel: rgba(255, 255, 255, 0.88);
  --surface-panel-muted: #f4f8fc;
  --surface-panel-deep: #e9f0f8;
  --surface-overlay: rgba(255, 255, 255, 0.96);
  --surface-elevated: #ecf2f9;
  --surface-chat: rgba(255, 255, 255, 0.72);
  --surface-input: #f8fbff;
  --surface-pill: var(--surface-elevated);

  --text-primary: #213244;
  --text-secondary: #72859b;
  --text-tertiary: #8ba0b7;
  --text-on-accent: #f8fbff;

  --border-subtle: #e4ecf5;
  --border-default: #d6e1ee;
  --border-strong: #c3d2e2;

  --interactive-selected: #dbeaf9;
  --interactive-hover-border: #7aa7d6;
  --interactive-focus-border: #7aa7d6;
  --interactive-focus-ring: rgba(122, 167, 214, 0.24);

  --state-success: #1d9a79;
  --state-danger: #d94d46;
  --state-warning: #e19812;
  --state-info: #2f6fb2;

  --shadow-panel: none;
  color-scheme: light;
}
```

## 旧变量到新变量的映射建议

以下是迁移时建议采用的映射关系。

### 建议直接替换

- `--body-bg` -> `--bg-canvas`
- `--panel-bg` -> `--surface-1`
- `--surface-soft` -> `--surface-2`
- `--surface-quiet` -> `--surface-3`
- `--text-strong` -> `--text-primary`
- `--muted` -> `--text-secondary`
- `--hint-text` -> `--text-tertiary`
- `--panel-border` -> `--border-default`
- `--panel-border-strong` -> `--border-strong`
- `--divider` -> `--border-subtle`
- `--selected` -> `--interactive-selected`
- `--focus-border` -> `--interactive-focus-border`
- `--focus-glow` -> `--interactive-focus-ring`
- `--good` -> `--state-success`
- `--danger` -> `--state-danger`
- `--warn` -> `--state-warning`

### 建议删除历史业务语义

- `--team-create-panel-border`
- `--team-create-control-border`
- `--team-create-node-border`

这三个变量建议逐步迁移为：

- `--border-default`
- `--border-strong`
- `--border-subtle`

由组件自行选择对应层级，而不是继续依赖业务来源命名。

### 建议保留为局部 alias

- `--chat-divider`
- `--bubble-left`
- `--bubble-right`
- `--bubble-right-text`
- `--scrollbar-track`
- `--scrollbar-thumb`
- `--scrollbar-thumb-hover`

聊天区、输入区、顶栏胶囊等不再建议继续使用 `--chat-bg`、`--composer-bg`、`--pill-bg` 这类旧名字，建议直接纳入 `surface` 家族：

- `--surface-chat`
- `--surface-input`
- `--surface-pill`

剩余局部变量短期内可以继续存在，但建议改为基于新 schema 推导：

```css
--chat-bg: var(--surface-3);
--chat-divider: var(--border-subtle);
--scrollbar-track: var(--surface-3);
--scrollbar-thumb: var(--interactive-focus-border);
```

## 已发现的明确问题

以下问题建议作为第一批修复项处理。

### 未定义变量

- `--text`
- `--panel-bg-elevated`
- `--banner-error-border`

### 仅定义未使用或语义价值偏弱

- `--accent-soft`

### 语义已漂移的变量

- `--team-create-panel-border`
- `--team-create-control-border`
- `--team-create-node-border`

## 组件迁移策略

建议按“基础组件优先、业务组件后移”的顺序进行。

### Phase 1: 建立 schema 和兼容层

修改文件：

- `frontend/src/style.css`

工作内容：

- 增加新 schema
- 对旧变量增加兼容映射
- 补齐未定义变量，保证当前页面不回归

阶段目标：

- 不改组件逻辑
- 页面视觉基本不变
- 主题变量结构先稳定下来

### Phase 2: 迁移基础通用组件

建议优先处理：

- `frontend/src/components/ui/ConfirmDialog.vue`
- `frontend/src/components/ui/ToggleSwitch.vue`
- `frontend/src/components/ui/LabeledSwitch.vue`
- `frontend/src/components/ui/CustomSelect.vue`
- `frontend/src/components/layout/TopBar.vue`

迁移规则：

- 禁止继续引入新的旧变量
- 优先使用 `surface / text / border / interactive / state`

### Phase 3: 迁移高复用业务组件

建议优先处理：

- `frontend/src/components/chat/ChatPanel.vue`
- `frontend/src/components/chat/MessageStream.vue`
- `frontend/src/components/console/RoomListSection.vue`
- `frontend/src/components/agent/AgentCardBase.vue`
- `frontend/src/components/agent/AgentActivityDialog.vue`

### Phase 4: 迁移页面和设置模块

建议处理：

- `frontend/src/pages/SettingsPage.vue`
- `frontend/src/pages/TeamCreatePage.vue`
- `frontend/src/components/settings/*`
- `frontend/src/components/team/*`

### Phase 5: 清理旧变量

完成组件迁移后：

- 删除无引用旧 token
- 删除兼容映射
- 保持最终 schema 简洁

## 组件侧使用规范

为了避免重构完成后再次发散，建议新增以下约束：

- 普通面板背景优先使用 `--surface-1`
- 次级块、输入区、列表项优先使用 `--surface-2`
- 页面或滚动底色优先使用 `--surface-3`
- 主文案使用 `--text-primary`
- 辅助文案使用 `--text-secondary`
- 弱提示、禁用或注释使用 `--text-tertiary`
- 分割线使用 `--border-subtle`
- 一般卡片边框使用 `--border-default`
- 强结构边界使用 `--border-strong`
- 激活态背景使用 `--interactive-selected`
- focus 边框使用 `--interactive-focus-border`
- focus 光晕使用 `--interactive-focus-ring`
- 成功、失败、警告提示统一使用 `--state-*`

## 验收标准

完成第一轮重构后，至少满足以下条件：

- dark 和 light 主题字段集合一致
- `rg "var\\(--text\\|var\\(--panel-bg-elevated\\|var\\(--banner-error-border"` 无残留未定义变量使用
- `team-create-*` 不再作为新增组件的主题依赖
- 主题切换前后页面无明显闪烁或失效
- 主要页面视觉与当前版本基本一致

## 风险与注意事项

### 1. 不要同时改 token 和视觉设计

本次目标是收敛主题变量，不是顺手重做视觉风格。否则容易把问题混在一起。

### 2. 允许保留少量局部 alias

消息气泡、滚动条、聊天区等局部主题语义可以保留别名，但必须能追溯到基础 schema。

### 3. 避免一次性大改全部文件

建议每一批迁移都能独立回归，避免大范围样式回归后难以定位。

## 建议的首个实现动作

建议真正开工时，第一步只做一件事：

- 在 `frontend/src/style.css` 中补全新的 theme schema
- 为现有旧变量增加兼容映射
- 修复 3 个未定义变量

这一步完成后，再进入组件迁移。

这样可以把风险控制在最小范围内，并为后续迁移建立统一基础。

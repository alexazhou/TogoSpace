# 前端基础组件重复清单（去重待办）

> 记录人：Claude Code
> 记录日期：2026-08-18
> 范围：`frontend/src/components/`（Vue 3 + 手写 scoped CSS，无第三方组件库）

## 背景

前端不使用 antd / element-plus 等组件库，所有基础组件均为手写 Vue SFC + scoped CSS。由于缺少统一的组件层，同一套「基础组件的样式/结构」在不同页面被反复复制。本清单记录调查到的重复项，作为后续去重重构的 backlog。

## 已完成：UiTag（tag / chip / badge 类）

commit `1cab9bd0` 已将以下 tag 类收敛为 `src/components/ui/UiTag.vue`（`tone / size / shape` 三个维度）：

| 原 class | 覆盖文件数 | 说明 |
|---|---|---|
| `.model-tag` | 2 | 模型名 / 输入类型 |
| `.role-chip`（含 --system/--user/--draft） | 3 | 角色、技能、模板编辑 |
| `.svc-chip`（含 --enabled/--disabled） | 2 | 服务状态 |
| `.service-capability-tag` | 1 | 第三方服务能力 |
| `.custom-tag` | 1 | 自定义标记 |
| `.leader-badge` | 1 | 部门负责人 |
| `.team-summary-chip` | 1 | 团队汇总计数 |
| `.driver-badge`（含 .online） | 1 | 底层驱动状态 |
| `.model-vision-badge` | 1 | Vision 标识 |

## 待办重复项（按价值排序）

### A. 弹窗外壳 editor-*（8 处，最严重）

以下 8 个 settings 弹窗逐字复制同一套 `editor-*` 结构样式，仅 `width: min(...)` 不同：

- ExtraParamsConfigSection / HeadersConfigSection / ModelEditorDialog / ModelTestDialog / ProviderEditorDialog / ContextConfigSection / RoleTemplateEditorDialog / DefaultModelsSection

重复的类：`.editor-overlay`、`.editor-dialog`、`.editor-head`、`.editor-head-copy`、`.editor-actions`、`.editor-actions-trailing`、`.editor-actions-leading`、`.editor-close`、`.editor-eyebrow`

**建议**：抽 `ModalDialog.vue`（overlay + 标题/eyebrow/close + actions 插槽），或将这套样式提到全局 `src/style.css`。

### B. 表单字段 svc-*（6+ 处）

`.svc-input`（height 40px / 圆角 12px / 同款 border+bg）在 6 个文件定义完全一致；`.svc-field / .svc-select / .svc-textarea / .svc-form-grid / .svc-field--wide / .svc-input--flex` 同理：

- svc-input：BaseUrlSection / HeadersConfigSection / ExtraParamsConfigSection / ModelEditorDialog / ProviderEditorDialog / ContextConfigSection
- svc-field：ModelSlotItem / ExtraParamsConfigSection / ModelEditorDialog / ProviderEditorDialog / ContextConfigSection / ThirdPartyServicesSection

**建议**：抽 `FormField.vue` 或全局表单类。

### C. 设置表格 chrome（2–4 处）

- `.settings-table`（th/td/head 等）在 4 个文件重复：RolesSettingsSection / SkillsSettingsSection / ModelsSettingsSection / ProviderModelsTable
- `.models-table-wrap / .models-cell-* / .models-empty / .actions-th / .text-danger` 在 ModelsSettingsSection 与 ProviderModelsTable 逐字重复（UiTag 只清了 tag，表格外层未动）
- `.roles-table-section / .roles-empty / .roles-cell-name / .settings-table-wrap / .section-actions` 在 RolesSettingsSection 与 SkillsSettingsSection 重复

**建议**：抽 `DataTable.vue` 或共享表样式。

### D. 状态类小件（3–5 处）

| 模式 | 文件数 | 涉及 |
|---|---|---|
| `.status-dot`（+ --success/--error） | 5 | AgentActivityDialog / AgentListSection / ModelTestDialog / TopBar / ThirdPartyServicesSection |
| `.error-banner` | 5 | AgentActivityPanel / AgentTaskPanel / TeamCreateSection / AgentActivityDialogShell / TeamDetailPage |
| `.loading-card` | 4 | AgentActivityPanel / AgentActivityDialogShell / AgentTaskPanel / TeamDetailPage |
| `.empty-card` / `.empty-state` | 2–3 | SettingsPage / TeamsSettingsSection / AgentLibraryCard / TeamDetailPage |

**建议**：抽 `StatusDot.vue` / `EmptyState.vue` / `LoadingCard.vue`。

### E. Markdown 样式（2 处）

`MessageStream.vue` 内 `:deep(.markdown-content) / .markdown-code-block` 是 `MarkdownContent.vue` 组件样式的第二份拷贝（气泡内配色）。

**建议**：MessageStream 复用 MarkdownContent 组件或共享样式变量。

### F. Confirm 弹窗 chrome（2 处）

`SkillDetailDialog.vue` 又抄了一份 `confirm-overlay / confirm-head / confirm-eyebrow / confirm-dialog / confirm-actions`，与 `ConfirmDialog.vue` 重复。

### G. settings 布局脚手架（5–8 处）

`.config-section`（8）、`.section-head`（5）、`.section-status`（6）、`.section-head--compact`（4）、`.editor-form`（2）等在 settings 各个 section 重复。

另外 `SettingsPage.vue` 重复声明其子组件（GeneralSettingsSection / TeamsSettingsSection / SettingsNavSidebar 等）的卡片类：`.driver-card / metric-card / status-card / status-grid / team-card / team-summary-row / team-card-* / settings-sidebar` 等。

### H. 组件库内部

- `CustomSelect.vue` 与 `CustomMultiSelect.vue` 共享 `.custom-select*` 内部类但分成两个组件（可考虑合并基类）
- `LabeledSwitch.vue` 与 `ToggleSwitch.vue` 均定义 `.is-checked`
- `.advanced-toggle / .advanced-toggle__state` 在 QuickInitModal / ModelEditorDialog / RoleTemplateEditorDialog 3 处重复

## 建议不动的「假阳性」

以下为同名不同义的通用状态/响应式类，不构成组件重复，统一反而破坏语义：

- `active / selected / is-open / is-active / is-selected / is-empty / is-checked / dragging`
- `bp-*`（响应式断点工具类）、`status-dot-pulse` 等仅用于单处状态变换的类

## 优先级建议

| 优先级 | 项 | 理由 |
|---|---|---|
| P0 | A 弹窗外壳 | 消除量最大、结构完全一致 |
| P1 | B 表单字段 / C 表格 chrome | 6+ / 4 处，改动机械 |
| P2 | D 状态小件 / E Markdown / F Confirm | 3–5 处 |
| P3 | G 布局脚手架 / H 组件库内部 | 涉及面广，需先统一设计 |

## 备注

- 本文档由调查扫描生成，重复项统计基于 `grep` 类级扫描 + 人工核实，个别计数可能有出入。
- 改造需保持视觉不回归（或明确接受的微调），并同步 `__tests__`。
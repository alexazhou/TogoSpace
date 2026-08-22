# 前端开发约定

> 适用范围：`frontend/`（Vue 3 + Vite + TypeScript，手写 UI 组件，**不使用** antd / element-plus 等第三方组件库）
> 相关：组件去重 backlog → `docs/tech/11_refactor/frontend_ui_duplication.md`

## 1. 尽量使用通用 UI 组件

前端的所有基础组件均为手写 Vue SFC + scoped CSS，统一放在 `src/components/ui/`。由于没有组件库兜底，最容易出现「同一套基础组件的样式/结构在不同页面被反复复制」的问题。

**约定：**

- 写任何 UI 前，先检索 `src/components/ui/` 是否已有可复用组件，**优先复用**，而不是在新页面复制样式/结构。
- 现有通用组件（会随去重不断扩充）：
  - 标签徽章：`UiTag`（tag / chip / badge，`tone/size/shape` 三维）
  - 弹窗外壳：`ModalDialog`（overlay + 标题 + actions 插槽）
  - 表单字段布局：`FormField`（label/hint/wide 的 grid 布局，管结构、不管外观）
  - 下拉：`CustomSelect` / `CustomMultiSelect` / `ModelSelect`
  - 开关：`ToggleSwitch` / `LabeledSwitch`
  - 提示：`InfoTooltip` / `HoverTooltip`
  - 其它：`MarkdownContent`（markdown 渲染）、`ConfirmDialog` / `TokenDialog`（专用弹窗）
- 若现有组件缺某种能力，**优先扩展该组件**（加 prop / slot），而不是另起炉灶，避免再次形成重复。
- 结构性复用（布局、外壳）优先抽**组件**；控件外观的复用优先用全局 `gu-*` 类（见下节）。
- 新增/改造通用组件后，同步维护 `docs/tech/11_refactor/frontend_ui_duplication.md` 的去重 backlog。

## 2. 全局 CSS 规范（`gu-*`）

全局公共样式类统一定义在 `src/style.css`，统一使用 **`gu-`** 前缀（global-ui）。

**命名不变量（核心）：**

- **全局公共类 = `gu-*`，只允许出现在 `src/style.css` 中。**
- 组件私有类 = 组件名 / 语义前缀，只能定义在各自的 `<style scoped>` 中。
- **组件内部永远不允许出现 `gu-*` 的定义**（否则别人在组件里看到 `gu-input`，会分不清是全局的还是本组件的）。

**现有全局类（示例）：**

| 类 | 用途 |
|---|---|
| `.gu-input` / `.gu-select` | 基础输入控件皮肤（height 40px / 圆角 12px / border+bg） |
| `.gu-input--flex` | 可伸缩输入（`flex:1; min-width:0`） |
| `.gu-textarea` / `.gu-textarea--code` | 文本域皮肤 / 代码样式文本域 |
| `.gu-form-grid` | 双列表单栅格（窄屏自动单列） |

（除 `gu-*` 外，`style.css` 中仍保留 `panel` / `ghost-button` / `scrollbar-thin` 等既有裸全局类。）

**控件内部如何覆盖全局 `gu-*`：**

不要在组件里重定义 `gu-*`。正确做法是：**元素保留全局基类 `gu-*`，再叠加一个组件自己的局部修饰类**，用 scoped 定义该修饰类、只写差异：

```html
<!-- 基类 gu-input 打底，number-input 是组件局部修饰 -->
<input class="gu-input number-input" type="number" />
```

```css
/* 组件 <style scoped>：只覆盖差异，不重复整套皮肤 */
.number-input {
  height: auto;
  padding: 8px 12px;
}
```

这样 `gu-*` 只存在于全局 css，全局基类永远不会被组件隐藏（组件从不定义 `gu-*`），也就不存在「改了全局、漏了私有」的问题。

**结构 vs 外观的分工：**

- **结构 / 布局**（label、hint、栅格 span）→ 抽组件（如 `FormField`），组件内 scoped。
- **控件外观 / 皮肤**（border、radius、bg、padding 等）→ 全局 `gu-*` 类。

**其它约束：**

- 不要新增 `svc-*` 前缀（历史遗留，已在去重中废弃清理）。
- 不要保留「定义了但没用到」的孤儿类；类名要名副其实，一个类名只承担一种含义。

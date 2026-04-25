# TUI 消息气泡布局方案总结 (Layout Strategy)

本文档总结了为解决 Textual 框架下 CJK/中西文混合场景中消息气泡换行、对齐及背景收缩问题的最终技术方案。

## 核心挑战 (The Challenges)

1.  **提前换行 (Premature Breaking)**：Rich 渲染引擎将普通空格视为“优选换行点”，导致中英混合时，如果中文排不下，会在前方的英文空格处提前断开，造成行尾大量空白。
2.  **背景冗余 (The "Gap")**：当气泡发生换行后，由于布局引擎无法动态计算每一行的物理宽度并收缩背景，导致气泡右侧会出现大片背景色空白。
3.  **文本截断与死锁 (Truncation & Layout Deadlock)**：在 `width: auto` 容器中嵌套复杂的宽度约束时，布局引擎往往会计算失败，将长文本截断为单行。
4.  **右对齐失效 (Right-side Alignment)**：在垂直容器中，如果发送者名字比消息气泡长，气泡无法自动贴紧右侧边缘。

## 最终解决方案 (Final Solutions - Simplified)

### 1. 字符级换行控制 (NBSP Replacement)
- **方法**：将消息文本中的所有普通空格替换为**不换行空格 (NBSP, `\u00A0`)**。
- **原理**：强制渲染引擎将整个短语视为一个“单词”，使其失去在空格处折行的能力。这样文本只有在触碰到物理边界时，才会根据 CJK/Rich 的强制换行规则进行折行，实现紧凑排版。

### 2. 气泡背景自适应 (Shrink-to-fit via Label)
- **方法**：使用 `Label` 组件替代 `Static`。
- **原理**：`Label` 在 Textual 中天生具备 `shrink` 特性。配合 CSS `width: auto`，`Label` 的背景色会自动包裹住其内部最长的一行文字，彻底解决 Gap 问题。

### 3. 纯 CSS 响应式宽度 (Pure CSS max-width)
- **方法**：直接在 TCSS 中为 `.bubble` 类设置 **`max-width: 80%;`**。
- **改进**：完全移除了 Python 侧的 `on_resize` 动态计算逻辑。现代 Textual 引擎能够很好地处理 CSS 百分比宽度与 `width: auto` 的结合，前提是消除了不必要的嵌套容器死锁。

### 4. 名字与气泡行分离 (Decoupled Row Alignment)
- **方法**：将发送者信息与消息气泡彻底解耦，分别放在独立的水平行（`.msg-line`）中。
- **对齐逻辑**：
    - 给全宽的水平行（`Horizontal`）设置 `align-horizontal: right` (右侧消息) 或 `left` (左侧消息)。
    - 对于右侧消息，交换时间与名字的顺序，改为 **`[时间] [名字]`**。
- **原理**：通过消除嵌套的 `width: auto` 垂直容器，让行内的 Label 组件能够独立、完美地贴合边缘，互不干扰。

## 最终组件树 (Component Hierarchy)

```text
MessageBubble (Vertical, 100% 宽)
├── Horizontal (msg-line, width: 100%, align-horizontal: left/right)
│   ├── Label (sender-name)
│   └── Label (time)
└── Horizontal (msg-line, width: 100%, align-horizontal: left/right)
    └── Label (bubble, width: auto, max_width: 80%)
```

## 验证结论
该方案通过了从 60 cols 到 120 cols 多种终端宽度的视觉测试，实现了背景紧贴文字、长文本自然换行、右侧气泡绝对贴边的极简且稳健的效果。

## 调试与验证方法 (Debugging & Verification)

由于 TUI 环境缺乏类似 Web 开发的实时“审查元素”功能，布局调试需遵循“隔离、可视化、边界化”的思路：

### 1. 最小化沙盒复现 (Sandbox Isolation)
- **思路**：将复杂的 UI 组件剥离出来，编写独立的、无业务逻辑的测试程序。
- **目的**：排除状态管理和网络请求的干扰，专注于验证 CSS 样式与 Textual 布局引擎之间的底层交互行为。

### 2. 无头快照验证 (Headless Snapshots)
- **思路**：利用支持快照导出的模拟器运行程序，自动捕获不同分辨率下的渲染状态。
- **验证点**：
    - **像素级回归**：对比修改前后的 `.png` 或 `.svg` 快照，确保细微对齐未发生偏移。
    - **响应式观察**：通过脚本批量生成不同宽度下的画面，直观验证 `max-width: 80%` 在极窄和极宽屏下的换行表现。

### 3. 半透明背景显影法 (Background Debugging)
- **思路**：为布局链条中的每一层容器（全宽行、自适应气泡等）添加不同颜色的高对比度半透明背景。
- **目的**：这种方式能立刻暴露“不可见”的布局陷阱，例如：
    - 容器是否因为死锁而退化到了最小内容宽度。
    - `align-horizontal` 属性是否真正“推”动了目标元素。
    - 背景色的收缩边界是否确实紧贴文字。

### 4. 边界压力测试 (Edge Case Testing)
- **多维度列宽测试**：
    - **极窄场景**：测试在仅能放下几个字符的宽度下，换行逻辑是否会崩溃或导致文字被截断。
    - **极宽场景**：验证短消息在拥有巨大剩余空间时，是否能正确收缩并维持侧边贴合。
- **文本极端组合**：
    - 使用超长无空格单词、纯中文字符、Emoji 簇以及复杂的特殊符号组合进行测试，验证 NBSP 逻辑在各种 Rich 渲染路径下的健壮性。

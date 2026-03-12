# TUI 消息气泡布局方案总结 (Layout Strategy)

本文档总结了为解决 Textual 框架下 CJK/中西文混合场景中消息气泡换行、对齐及背景收缩问题的最终技术方案。

## 核心挑战 (The Challenges)

1.  **提前换行 (Premature Breaking)**：Rich 渲染引擎将普通空格视为“优选换行点”，导致中英混合时，如果中文排不下，会在前方的英文空格处提前断开，造成行尾大量空白。
2.  **背景冗余 (The "Gap")**：当气泡发生换行后，由于布局引擎无法动态计算每一行的物理宽度并收缩背景，导致气泡右侧会出现大片背景色空白。
3.  **文本截断与死锁 (Truncation & Layout Deadlock)**：在 `width: auto` 容器中嵌套受限宽度的子元素时，布局引擎往往会计算失败，将长文本截断为单行或极其狭窄的“面条”状。
4.  **右对齐失效 (Right-side Alignment)**：在垂直容器中，如果发送者名字比消息气泡长，气泡无法自动贴紧右侧边缘。

## 最终解决方案 (Final Solutions)

### 1. 字符级换行控制 (NBSP Replacement)
- **方法**：将消息文本中的所有普通空格替换为**不换行空格 (NBSP, `\u00A0`)**。
- **原理**：强制渲染引擎将整个短语视为一个“单词”，使其失去在空格处折行的能力。这样文本只有在触碰到 `max_width` 边界时，才会根据 CJK/Rich 的强制换行规则进行折行。

### 2. 气泡背景自适应 (Shrink-to-fit via Label)
- **方法**：使用 `Label` 组件替代 `Static`。
- **原理**：`Label` 在 Textual 中天生具备 `shrink` 特性。配合 CSS `width: auto`，`Label` 的背景色会自动包裹住其内部最长的一行文字，彻底解决 Gap 问题。

### 3. 动态宽度约束 (Dynamic max_width)
- **方法**：在 Python 的 `on_resize` 事件中，根据屏幕宽度动态计算 `max_width`（通常为 80%），并**直接**将其赋值给气泡 `Label` 元素。
- **关键细节**：不再给父容器设置 `max_width`，以避免复杂的嵌套约束导致布局引擎死锁。

### 4. 名字与气泡行分离 (Decoupled Row Alignment)
- **方法**：将发送者信息（名字、时间）与消息气泡彻底解耦，分别放在独立的水平行（`Horizontal`）中。
- **对齐逻辑**：
    - 给全宽的行容器（`.sender-line` 和 `.bubble-line`）设置 `align-horizontal: right` (右侧消息) 或 `left` (左侧消息)。
    - 对于右侧消息，交换时间与名字的显示顺序，改为 **`[时间] [名字]`**，使名字更贴近屏幕边缘。
- **原理**：通过消除嵌套的 `width: auto` 容器，让行内的 Label 组件能够独立、完美地贴合边缘，互不干扰。

## 最终组件树 (Component Hierarchy)

```text
MessageBubble (Vertical, 100% 宽)
├── Horizontal (sender-line, width: 100%, align-horizontal: left/right)
│   ├── Label (sender-name)
│   └── Label (time)
└── Horizontal (bubble-line, width: 100%, align-horizontal: left/right)
    └── Label (bubble, width: auto, max_width: 80%)
```

## 验证结论
该方案通过了从 60 cols 到 120 cols 多种终端宽度的视觉测试（见 `verify_30_swap_time_name.png`），实现了背景紧贴文字、长文本在大宽度下自然换行、右侧气泡绝对贴边的完美效果。

## 调试与验证方法 (Debugging & Verification)

在 TUI 开发中，由于 Textual 缺乏传统浏览器的“审查元素”功能，建议采用以下方法进行布局验证：

### 1. 最小化沙盒复现 (Sandbox Isolation)
- **方法**：建立独立的调试脚本（如 `gemini_tui_test/debug_app.py`），仅保留有问题的 UI 组件。

### 2. 终端模拟器截屏 (Headless Snapshots)
- **工具**：使用自定义的模拟器（如 `go_simu_terminal/simu_terminal_go`）。
- **优势**：允许在不同列宽（`--cols`）下进行像素级的布局回归测试。

### 3. 半透明背景着色法 (The Debugging Background Method)
- **方法**：为参与布局的所有父容器和子元素添加高对比度的半透明背景色（如 `rgba(255,0,0,0.2)`）。
- **目的**：这就像给布局打入“显影剂”，能立刻看清容器真实的收缩和对齐行为。

### 4. 边界压力测试 (Edge Case Testing)
- **极窄模式**：验证换行是否会导致文本截断。
- **极宽模式**：验证短气泡是否依然能贴紧右侧边缘，背景是否出现了不必要的拉伸。

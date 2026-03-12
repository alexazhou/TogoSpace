# TUI 消息气泡布局方案总结 (Layout Strategy)

本文档总结了为解决 Textual 框架下 CJK/中西文混合场景中消息气泡换行、对齐及背景收缩问题的最终技术方案。

## 核心挑战 (The Challenges)

1.  **提前换行 (Premature Breaking)**：Rich 渲染引擎将普通空格视为“优选换行点”，导致中英混合时，如果中文排不下，会在前方的英文空格处提前断开，造成行尾大量空白。
2.  **背景冗余 (The "Gap")**：当气泡发生换行后，由于布局引擎无法动态计算每一行的物理宽度并收缩背景，导致气泡右侧会出现大片背景色空白。
3.  **文本截断 (Truncation Bug)**：在 Textual 中同时使用 `width: auto` 和 `text-wrap: wrap` 时，如果没有显式的宽度约束，布局引擎往往会计算失败，将长文本截断为单行显示。
4.  **右对齐失效 (Right-side Alignment)**：在 `Vertical` 容器中，由于 `width: auto` 子元素默认靠左，当发送者名字比消息气泡长时，气泡无法自动贴紧右侧边缘。

## 最终解决方案 (Final Solutions)

### 1. 字符级换行控制 (NBSP Replacement)
- **方法**：将消息文本中的所有普通空格替换为**不换行空格 (NBSP, `\u00A0`)**。
- **原理**：强制渲染引擎将整个短语视为一个“单词”，使其失去在空格处折行的能力。这样文本只有在触碰到 `max_width` 边界时，才会根据 CJK/Rich 的强制换行规则进行折行，从而实现紧凑的排版。

### 2. 气泡背景自适应 (Shrink-to-fit via Label)
- **方法**：使用 `Label` 组件替代 `Static`。
- **原理**：`Label` 在 Textual 中天生具备 `shrink` 特性。配合 CSS `width: auto`，`Label` 的背景色会自动包裹住其内部最长的一行文字，彻底解决 Gap 问题。

### 3. 双重宽度约束 (Dual max_width Constraint)
- **方法**：在 Python 的 `on_resize` 事件中，动态计算最大宽度（例如屏幕宽度的 80%），并**同时**将其赋值给父容器 (`.bubble-inner`) 和 `Label` 本身的 `max_width`。
- **原理**：这种“显式注入”宽度限制的方式，辅助 Textual 的布局协商过程，确保它在 `width: auto` 模式下仍能正确计算出换行所需的行数，防止文本被截断。

### 4. 绝对右贴边对齐 (Alignment Wrapper)
- **方法**：为右侧气泡引入一个宽度为 100% 的水平包裹层 `.bubble-right-wrap`。
- **结构**：
    ```python
    Vertical(bubble-inner-right): # 宽度由名字撑开 (auto)
        Horizontal(sender-row-right): ... # 名字行
        Horizontal(bubble-right-wrap, width: 100%): # 强制充满父容器宽度
            Label(bubble): # 实际气泡内容 (width: auto)
    ```
- **CSS**：给 `.bubble-right-wrap` 设置 `align-horizontal: right`。
- **原理**：由于 `Vertical` 容器内 `width: auto` 元素无法自动右移，通过一个 100% 宽度的中间层充当“推手”，利用其内部的水平对齐属性将气泡稳稳地推向最右侧。

## 最终组件树 (Component Hierarchy)

```text
MessageBubble (Vertical)
└── Horizontal (bubble-row, width: 100%)
    ├── Static (bubble-spacer, width: 1fr) [仅右侧气泡有]
    └── Vertical (bubble-inner, width: auto)
        ├── Horizontal (sender-row, width: auto)
        │   ├── Label (sender-name)
        │   └── Label (time)
        └── Horizontal (bubble-right-wrap, width: 100%) [关键对齐层]
            └── Label (bubble, width: auto)
    └── Static (bubble-spacer, width: 1fr) [仅左侧气泡有]
```

## 验证结论
该方案通过了从 60 cols 到 120 cols 多种终端宽度的视觉测试（见 `verify_16_clean_final.png`），实现了背景紧贴文字、中英混排自然换行、右侧气泡绝对贴边的预期效果。

## 调试与验证方法 (Debugging & Verification)

在 TUI 开发中，由于 Textual 缺乏传统浏览器的“审查元素”功能，建议采用以下方法进行布局验证：

### 1. 最小化沙盒复现 (Sandbox Isolation)
- **方法**：建立独立的调试脚本（如 `gemini_tui_test/debug_app.py`），仅保留有问题的 UI 组件。
- **目的**：排除复杂业务逻辑和数据流的干扰，专注于布局引擎的行为。

### 2. 终端模拟器截屏 (Headless Snapshots)
- **工具**：使用自定义的模拟器（如 `go_simu_terminal/simu_terminal_go`）。
- **流程**：
    1. 通过模拟器运行 Python 脚本。
    2. 设置不同的 `--cols` 和 `--rows`。
    3. 自动生成 `.png` 或 `.svg` 快照。
- **优势**：允许在无图形界面环境下进行像素级的布局回归测试。

### 3. 半透明背景着色法 (The Debugging Background Method)
- **方法**：为参与布局的所有父容器和子元素添加高对比度的半透明背景色（如 `rgba(255,0,0,0.2)`）。
- **目的**：这就像给布局打入“显影剂”，能立刻看清哪些容器缩成了 `auto`，哪些容器在偷偷靠左，从而快速定位对齐失效的根源。

### 4. 边界压力测试 (Edge Case Testing)
- **极窄模式**：在 60 cols 甚至更窄的宽度下测试，验证 `max_width` 是否生效，长文本是否会被异常截断。
- **极宽模式**：在 120+ cols 下测试，验证短气泡是否依然能贴紧右侧边缘，背景是否出现了不必要的拉伸。
- **混合文本**：测试包含中文字符、英文长单词、带空格短语、Emoji 以及特殊符号的组合，确保换行点符合预期。

# 技术设计文档：前端消息 Markdown 渲染支持

## 1. 目标 (Objectives)
为 Togobot 前端聊天界面提供完整的 Markdown 渲染能力，包括但不限于：
*   **基础排版**：标题、加粗、斜体、列表、引用。
*   **代码支持**：支持多语言代码高亮及代码块展示。
*   **交互增强**：自动识别链接（Linkify）、任务列表支持。
*   **主题对齐**：渲染样式需完美适配现有的深色/浅色模式及 CSS 变量。

## 2. 技术选型 (Tech Stack)
*   **Markdown 解析器**: `markdown-it`
    *   *理由*：插件生态丰富，渲染速度快，配置灵活，安全性高。
*   **任务列表插件**: `markdown-it-task-lists`
    *   *理由*：补齐 `- [ ]` / `- [x]` 语法支持，使目标与实现一致。
*   **代码高亮**: `highlight.js`
    *   *理由*：支持 190+ 种语言，能够按语言高亮；前端侧仅消费其 token class，不直接依赖预置主题。
*   **安全清洗 (可选)**: `dompurify` (如果后续需要支持部分 HTML 标签)。

## 3. 详细设计 (Detailed Design)

### 3.1 核心组件 `MarkdownContent.vue`
创建一个通用组件 `src/components/ui/MarkdownContent.vue`。

*   **Props**:
    *   `content: string`: 原始 Markdown 字符串。
    *   `inline: boolean`: 是否使用紧凑样式渲染；仅用于短文本场景，不作为复杂块级 Markdown 的通用预览方案。
*   **逻辑实现**:
    *   在独立模块中初始化并导出一个共享 `markdown-it` 实例，例如 `src/utils/markdown.ts`，避免每个组件实例重复创建 parser。
    *   配置 `highlight` 函数，对接 `highlight.js`。
    *   注册 `markdown-it-task-lists` 插件。
    *   使用 `v-html` 渲染解析后的 HTML。
    *   **安全性**：强制关闭 `html: true`，防止 XSS。

### 3.2 渲染配置策略
```typescript
import MarkdownIt from 'markdown-it';
import taskLists from 'markdown-it-task-lists';

const md = new MarkdownIt({
  html: false,        // 禁用 HTML 标签以防 XSS
  linkify: true,      // 自动转换 URL
  typographer: true,  // 启用语义字符替换
  highlight: (str, lang) => {
    if (lang && hljs.getLanguage(lang)) {
      try {
        return hljs.highlight(str, { language: lang }).value;
      } catch (__) {}
    }
    return ''; // 使用默认转义
  }
});

md.use(taskLists, { enabled: true, label: true, labelAfter: true });

// 插件扩展：确保链接在新窗口打开
md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
  tokens[idx].attrSet('target', '_blank');
  tokens[idx].attrSet('rel', 'noopener noreferrer');
  return self.renderToken(tokens, idx, options);
};
```

### 3.3 样式适配 (CSS Variables Integration)
在 `src/style.css` 或组件 Scoped CSS 中增加 Markdown 专属样式类，使用项目的 CSS 变量：

*   **基础元素**:
    *   `p`: 移除首尾 margin，或根据上下文调整间距。
    *   `blockquote`: `border-left: 4px solid var(--border-strong); color: var(--text-secondary); background: var(--surface-panel-muted);`
*   **代码块**:
    *   `pre`: 背景色 `var(--surface-panel-deep)`，内边距 `12px`，圆角 `8px`，`overflow-x: auto`。
    *   `code`: 字体采用等宽字体系列。
    *   `.hljs`: 不直接引入 `highlight.js` 自带主题；统一使用项目 CSS 变量自行定义前景色、关键字色、注释色，确保深浅色模式一致。
*   **列表**:
    *   `ul, ol`: 设置适当的 `padding-left` (约 1.5rem)，确保符号可见。
    *   `input[type='checkbox']`: 仅做展示，不允许交互修改，避免造成“前端可勾选但不持久化”的误解。

### 3.4 预览区策略
当前 `MessageStream.vue` 同时存在主消息气泡与浮动消息条两个展示区，两者不应复用完全一致的 Markdown 呈现策略。

*   **主消息气泡**:
    *   使用完整 `MarkdownContent` 渲染。
    *   移除当前 `.bubble` 的 `white-space: pre-wrap`，交由 Markdown 语义标签控制换行与段落。
*   **浮动消息预览条**:
    *   不直接渲染完整 Markdown。
    *   继续使用纯文本摘要/截断形式，避免标题、列表、代码块在窄容器中破坏布局。
    *   如需统一入口，可新增 `renderMarkdownPreviewText(content)`，将 Markdown 预处理为单行纯文本。

## 4. 影响范围 (Impact & Changes)

### 4.1 组件重构
*   **`MessageStream.vue`**:
    *   将 `.bubble` 内部的 `{{ message.content }}` 替换为 `<MarkdownContent :content="message.content" />`。
    *   **关键变更**：移除 `.bubble` 样式中的 `white-space: pre-wrap`，因为 Markdown 渲染出的 HTML 标签会自带换行语义。
*   **`MessageStream.vue` (浮动消息预览)**:
    *   不直接复用完整 Markdown 渲染。
    *   维持纯文本预览，必要时增加一层 Markdown -> plain text 的摘要转换。

### 4.2 依赖管理
*   新增依赖项将记录在 `frontend/package.json` 中。
*   依赖项建议为：`markdown-it`、`markdown-it-task-lists`、`highlight.js`。
*   不直接引入 `highlight.js` 预置主题 CSS，避免与现有深浅色主题冲突。

### 4.3 测试建议
*   为 `MarkdownContent.vue` 增加单测，覆盖：
    *   基础段落、列表、引用渲染。
    *   代码块高亮 class 输出。
    *   任务列表渲染。
    *   原始 HTML 被禁用与转义。
*   为 `MessageStream.vue` 增加集成测试，覆盖：
    *   主消息气泡 Markdown 渲染。
    *   浮动消息区仍保持纯文本摘要。

## 5. 实施路线图 (Implementation Roadmap)
1.  **准备阶段**:
    *   安装依赖：`npm install markdown-it markdown-it-task-lists highlight.js`。
    *   新建共享工具模块 `src/utils/markdown.ts`。
2.  **开发阶段**:
    *   实现 `MarkdownContent` 组件。
    *   在全局或组件内定义渲染样式，特别是 `.hljs` 与代码块样式。
    *   实现消息预览摘要转换函数（如需要）。
3.  **集成阶段**:
    *   更新 `MessageStream.vue` 主气泡引用。
    *   保持浮动消息条走纯文本预览路径。
    *   测试各种 Markdown 语法在不同气泡宽度下的显示效果。
    *   验证深色/浅色主题下的代码块与链接样式。

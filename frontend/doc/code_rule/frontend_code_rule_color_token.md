# Frontend Code Rule: Color Tokens

前端颜色统一使用 `src/theme/tokens.css` 里的 token，不要在组件样式里直接硬编码颜色。

规则：

- 优先使用已有 token，如 `var(--text-primary)`、`var(--text-secondary)`、`var(--surface-input)`
- 如果现有 token 不够，先补到 `tokens.css`，再在组件里引用
- 禁止直接写 `#fff`、`#000`、`rgba(...)` 这类颜色值

示例：

```css
/* bad */
color: #fff;

/* good */
color: var(--text-primary);
```

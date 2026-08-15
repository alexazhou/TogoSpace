import MarkdownIt from 'markdown-it';
import taskLists from 'markdown-it-task-lists';
import hljs from 'highlight.js/lib/core';
import bash from 'highlight.js/lib/languages/bash';
import css from 'highlight.js/lib/languages/css';
import go from 'highlight.js/lib/languages/go';
import java from 'highlight.js/lib/languages/java';
import javascript from 'highlight.js/lib/languages/javascript';
import json from 'highlight.js/lib/languages/json';
import markdownLanguage from 'highlight.js/lib/languages/markdown';
import python from 'highlight.js/lib/languages/python';
import rust from 'highlight.js/lib/languages/rust';
import sql from 'highlight.js/lib/languages/sql';
import typescript from 'highlight.js/lib/languages/typescript';
import xml from 'highlight.js/lib/languages/xml';
import yaml from 'highlight.js/lib/languages/yaml';

hljs.registerLanguage('bash', bash);
hljs.registerLanguage('sh', bash);
hljs.registerLanguage('shell', bash);
hljs.registerLanguage('css', css);
hljs.registerLanguage('go', go);
hljs.registerLanguage('java', java);
hljs.registerLanguage('javascript', javascript);
hljs.registerLanguage('js', javascript);
hljs.registerLanguage('json', json);
hljs.registerLanguage('markdown', markdownLanguage);
hljs.registerLanguage('md', markdownLanguage);
hljs.registerLanguage('python', python);
hljs.registerLanguage('py', python);
hljs.registerLanguage('rust', rust);
hljs.registerLanguage('rs', rust);
hljs.registerLanguage('sql', sql);
hljs.registerLanguage('typescript', typescript);
hljs.registerLanguage('ts', typescript);
hljs.registerLanguage('html', xml);
hljs.registerLanguage('xml', xml);
hljs.registerLanguage('yaml', yaml);
hljs.registerLanguage('yml', yaml);

function normalizeFenceLanguage(language: string): string {
  return language.trim().split(/\s+/)[0]?.toLowerCase() ?? '';
}

function escapeHtmlAttribute(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function trimFenceBlankLines(content: string): string {
  return content
    .replace(/^(?:[ \t]*\r?\n)+/, '')
    .replace(/(?:\r?\n[ \t]*)+$/, '');
}

const markdown = new MarkdownIt({
  html: false,
  linkify: true,
  typographer: true,
  breaks: true,
  highlight: (content: string, language: string) => {
    const normalizedContent = trimFenceBlankLines(content);
    const normalizedLanguage = normalizeFenceLanguage(language);
    const languageAttribute = normalizedLanguage
      ? ` data-language="${escapeHtmlAttribute(normalizedLanguage)}"`
      : '';

    if (normalizedLanguage && hljs.getLanguage(normalizedLanguage)) {
      try {
        const highlighted = hljs.highlight(normalizedContent, { language: normalizedLanguage, ignoreIllegals: true }).value;
        return `<pre class="hljs"${languageAttribute}><code>${highlighted}</code></pre>`;
      } catch {
        // Fall through to escaped fallback below.
      }
    }

    const escaped = markdown.utils.escapeHtml(normalizedContent);
    return `<pre class="hljs"${languageAttribute}><code>${escaped}</code></pre>`;
  },
});

markdown.use(taskLists, {
  enabled: true,
  label: true,
  labelAfter: true,
});

const defaultLinkOpenRule = markdown.renderer.rules.link_open
  ?? ((tokens: any[], idx: number, options: any, _env: any, self: any) => self.renderToken(tokens, idx, options));

markdown.renderer.rules.link_open = (tokens: any[], idx: number, options: any, env: any, self: any) => {
  tokens[idx].attrSet('target', '_blank');
  tokens[idx].attrSet('rel', 'noopener noreferrer');
  return defaultLinkOpenRule(tokens, idx, options, env, self);
};

function stripSingleParagraphWrapper(html: string): string {
  const trimmed = html.trim();
  if (!trimmed.startsWith('<p>') || !trimmed.endsWith('</p>')) {
    return trimmed;
  }

  const inner = trimmed.slice(3, -4);
  if (inner.includes('<p>') || inner.includes('</p>')) {
    return trimmed;
  }
  return inner;
}

function htmlToPlainText(html: string): string {
  if (typeof document === 'undefined') {
    return html
      .replace(/<[^>]+>/g, ' ')
      .replace(/&nbsp;/g, ' ')
      .replace(/&amp;/g, '&')
      .replace(/&lt;/g, '<')
      .replace(/&gt;/g, '>')
      .replace(/\s+/g, ' ')
      .trim();
  }

  const template = document.createElement('template');
  template.innerHTML = html;
  return (template.content.textContent ?? '')
    .replace(/\s+/g, ' ')
    .trim();
}

export function renderMarkdown(content: string, options?: { inline?: boolean }): string {
  const source = content ?? '';
  if (!source.trim()) {
    return '';
  }

  const rendered = markdown.render(source);
  return options?.inline ? stripSingleParagraphWrapper(rendered) : rendered;
}

export function renderMarkdownPreviewText(content: string): string {
  const source = content ?? '';
  if (!source.trim()) {
    return '';
  }
  return htmlToPlainText(markdown.render(source));
}

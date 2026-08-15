import { beforeEach, describe, expect, it, vi } from 'vitest';
import { flushPromises, mount } from '@vue/test-utils';
import MarkdownContent from '../MarkdownContent.vue';
import i18n from '../../../i18n';

const { showGlobalSuccessToastMock } = vi.hoisted(() => ({
  showGlobalSuccessToastMock: vi.fn(),
}));

vi.mock('../../../appUiState', () => ({
  showGlobalSuccessToast: showGlobalSuccessToastMock,
}));

describe('MarkdownContent', () => {
  beforeEach(() => {
    showGlobalSuccessToastMock.mockReset();
    vi.stubGlobal('navigator', {
      clipboard: {
        writeText: vi.fn().mockResolvedValue(undefined),
      },
    });
  });

  it('renders common markdown structures', () => {
    const wrapper = mount(MarkdownContent, {
      props: {
        content: '# Title\n\n- item 1\n- item 2\n\n> quote',
      },
      global: {
        plugins: [i18n],
      },
    });

    expect(wrapper.find('h1').text()).toBe('Title');
    expect(wrapper.findAll('li')).toHaveLength(2);
    expect(wrapper.find('blockquote').text()).toContain('quote');
  });

  it('renders code fences and task lists', async () => {
    const wrapper = mount(MarkdownContent, {
      props: {
        content: '```ts\n\nconst answer = 42;\n\n```\n\n- [x] shipped',
      },
      global: {
        plugins: [i18n],
      },
    });
    await flushPromises();

    expect(wrapper.find('pre.hljs code').text()).toContain('const answer = 42;');
    expect(wrapper.find('pre.hljs code').text()).toBe('const answer = 42;');
    expect(wrapper.find('input[type="checkbox"]').element).toHaveProperty('checked', true);
    expect(wrapper.find('.markdown-code-language').text()).toBe('ts');
    expect(wrapper.find('.markdown-code-wrap').attributes('aria-label')).toBe('开启自动换行');
    expect(wrapper.find('.markdown-code-wrap').attributes('aria-pressed')).toBe('false');
    expect(wrapper.find('.markdown-code-copy').attributes('aria-label')).toBe('复制');
    expect(wrapper.find('.markdown-code-copy .fa-copy').exists()).toBe(true);
  });

  it('escapes raw html and hardens links', () => {
    const wrapper = mount(MarkdownContent, {
      props: {
        content: '<script>alert(1)</script> https://example.com',
      },
      global: {
        plugins: [i18n],
      },
    });

    expect(wrapper.html()).not.toContain('<script>');
    const link = wrapper.find('a');
    expect(link.attributes('target')).toBe('_blank');
    expect(link.attributes('rel')).toBe('noopener noreferrer');
  });

  it('copies code block text from the top-right action', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', {
      clipboard: { writeText },
    });

    const wrapper = mount(MarkdownContent, {
      props: {
        content: '```ts\nconst answer = 42;\n```',
      },
      global: {
        plugins: [i18n],
      },
    });
    await flushPromises();

    await wrapper.find('.markdown-code-copy').trigger('click');

    expect(writeText).toHaveBeenCalledWith('const answer = 42;');
    expect(showGlobalSuccessToastMock).toHaveBeenCalledWith('已复制');
  });

  it('toggles wrapping for an individual code block', async () => {
    const wrapper = mount(MarkdownContent, {
      props: {
        content: '```bash\nreally-long-command --with-many-arguments --and-a-long-path\n```',
      },
      global: {
        plugins: [i18n],
      },
    });
    await flushPromises();

    const codeBlock = wrapper.find('.markdown-code-block');
    const wrapButton = wrapper.find('.markdown-code-wrap');

    expect(codeBlock.classes()).not.toContain('markdown-code-block--wrapped');
    expect(wrapButton.attributes('aria-pressed')).toBe('false');

    await wrapButton.trigger('click');

    expect(codeBlock.classes()).toContain('markdown-code-block--wrapped');
    expect(wrapButton.attributes('aria-pressed')).toBe('true');
    expect(wrapButton.attributes('aria-label')).toBe('关闭自动换行');

    await wrapButton.trigger('click');

    expect(codeBlock.classes()).not.toContain('markdown-code-block--wrapped');
    expect(wrapButton.attributes('aria-pressed')).toBe('false');
    expect(wrapButton.attributes('aria-label')).toBe('开启自动换行');
  });
});

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mount } from '@vue/test-utils';
import AgentActivityItem from '../AgentActivityItem.vue';
import i18n from '../../../i18n';
import type { AgentActivity } from '../../../types';

/**
 * #361：卡片级接线 —— read_image 活动在**真实卡片**里出图，
 * 且 base64 不再以文本预览的形式出现在卡片里。
 *
 * 这里不复用 `ActivityImageResult` 的替身 IO 细节，只要求「节点出现」：
 * 卡片层只负责把 `activityView.image` 传下去。
 */
class ImmediateIntersectionObserver implements IntersectionObserver {
  readonly root: Element | Document | null = null;

  readonly rootMargin: string = '';

  readonly thresholds: ReadonlyArray<number> = [];

  private readonly callback: IntersectionObserverCallback;

  constructor(callback: IntersectionObserverCallback) {
    this.callback = callback;
    // 同步模拟「一挂上就可见」，让卡片层断言拿到 `<img>`
    queueMicrotask(() => {
      this.callback([{ isIntersecting: true } as IntersectionObserverEntry], this);
    });
  }

  observe(): void {}

  unobserve(): void {}

  disconnect(): void {}

  takeRecords(): IntersectionObserverEntry[] {
    return [];
  }
}

const BASE64 = 'iVBORw0KGgoAAAANSUhEUg==';

function activity(overrides: Partial<AgentActivity> = {}): AgentActivity {
  return {
    id: 7,
    agent_id: 27,
    team_id: 2,
    activity_type: 'tool_call',
    status: 'succeeded',
    title: '调用工具',
    detail: 'read_image',
    started_at: '2026-11-25 10:00:00.000000',
    duration_ms: 900,
    metadata: {
      tool_name: 'read_image',
      tool_arguments: { file_path: '/tmp/shots360/old_edge.png' },
      tool_result: {
        file_path: '/tmp/shots360/old_edge.png',
        format: 'png',
        width: 171,
        height: 1000,
        mime_type: 'image/png',
        size_bytes: 25474,
        base64: BASE64,
      },
    },
    ...overrides,
  } as AgentActivity;
}

function mountItem(item: AgentActivity) {
  return mount(AgentActivityItem, {
    props: { activity: item },
    global: { plugins: [i18n] },
  });
}

beforeEach(() => {
  vi.stubGlobal('IntersectionObserver', ImmediateIntersectionObserver);
});

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = '';
});

describe('#361 活动卡片接入图片块', () => {
  it('read_image 卡片渲染图片块，src 为 data URL', async () => {
    const wrapper = mountItem(activity());
    await new Promise((resolve) => queueMicrotask(() => resolve(null)));

    const image = wrapper.find('[data-testid="activity-image"]');
    expect(image.exists()).toBe(true);
    expect(wrapper.find('[data-testid="activity-image-img"]').attributes('src'))
      .toBe(`data:image/png;base64,${BASE64}`);
  });

  it('卡片里不再出现 base64 文本预览（图片走图片块，不走 JSON 文本）', () => {
    const wrapper = mountItem(activity());
    expect(wrapper.html()).not.toContain(BASE64);
    expect(wrapper.find('.agent-activity-item__tool-result').exists()).toBe(false);
  });

  it('非图片工具照旧渲染文本结果块，且没有图片块', () => {
    const wrapper = mountItem(activity({
      detail: 'read_file',
      metadata: {
        tool_name: 'read_file',
        tool_arguments: { file_path: '/tmp/a.txt' },
        tool_result: { content: 'hello world' },
      },
    }));
    expect(wrapper.find('[data-testid="activity-image"]').exists()).toBe(false);
    expect(wrapper.find('.agent-activity-item__tool-result').text()).toContain('hello world');
  });

  it('read_image 失败（无 base64）→ 不出图片块，错误信息可见', () => {
    const wrapper = mountItem(activity({
      status: 'failed',
      error_message: '文件不存在',
      metadata: {
        tool_name: 'read_image',
        tool_arguments: { file_path: '/tmp/gone.png' },
        tool_result: { success: false, message: '文件不存在' },
      },
    }));
    expect(wrapper.find('[data-testid="activity-image"]').exists()).toBe(false);
    expect(wrapper.text()).toContain('文件不存在');
  });
});

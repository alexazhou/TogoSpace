import { beforeEach, describe, expect, it, vi } from 'vitest';
import { flushPromises, mount } from '@vue/test-utils';
import PendingMessageBar from '../PendingMessageBar.vue';
import i18n from '../../../../i18n';
import type { MessageInfo } from '../../../../types';
import type { PendingMessageItem } from '../types';

/**
 * `ResizeObserver` 在 happy-dom 里没有实现，真实浏览器里才存在。
 * 这里用一个可手动触发回调的假实现：截断判定靠 `scrollWidth > clientWidth`，
 * 而 happy-dom 的布局恒为 0，所以必须由测试显式构造「被截断」的几何。
 */
class FakeResizeObserver {
  static instances: FakeResizeObserver[] = [];

  callback: ResizeObserverCallback;

  constructor(callback: ResizeObserverCallback) {
    this.callback = callback;
    FakeResizeObserver.instances.push(this);
  }

  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
  trigger(): void {
    this.callback([], this as unknown as ResizeObserver);
  }
}

function createMessage(overrides: Partial<MessageInfo> = {}): MessageInfo {
  return {
    db_id: 21,
    sender_id: -1,
    sender_display_name: 'OPERATOR',
    content: 'hello',
    time: '2026-05-08T00:00:00Z',
    seq: 5,
    insert_immediately: false,
    ...overrides,
  };
}

function createItem(overrides: Partial<PendingMessageItem> = {}): PendingMessageItem {
  return {
    key: '21',
    message: createMessage(),
    status: 'queued',
    sender: { displayName: 'OPERATOR', avatarUrl: '/avatar.png', clickable: false },
    ...overrides,
  };
}

/**
 * 让预览元素报告「已截断」：`scrollWidth > clientWidth`。
 * happy-dom 不跑真实布局，只能覆写这两个只读属性的取值。
 */
function markTruncated(wrapper: ReturnType<typeof mount>): void {
  const el = wrapper.find('.pending-message-content').element as HTMLElement;
  Object.defineProperty(el, 'scrollWidth', { configurable: true, value: 400 });
  Object.defineProperty(el, 'clientWidth', { configurable: true, value: 100 });
  // 触发一次 ResizeObserver 回调，模拟真实浏览器里的测量时机
  FakeResizeObserver.instances[FakeResizeObserver.instances.length - 1]?.trigger();
}

function mountBar(props: Partial<{ item: PendingMessageItem; expanded: boolean; canEscalate: boolean; escalating: boolean }> = {}) {
  return mount(PendingMessageBar, {
    props: {
      item: props.item ?? createItem(),
      expanded: props.expanded ?? false,
      canEscalate: props.canEscalate ?? true,
      escalating: props.escalating ?? false,
    },
    global: { plugins: [i18n] },
  });
}

/**
 * 让预览元素报告「未截断」（默认几何在 happy-dom 里就是 0/0，但显式写出来更清楚）。
 * 用于模拟「展开后窗口变宽」：预览行不再省略，但卡片已经展开。
 */
function markNotTruncated(wrapper: ReturnType<typeof mount>): void {
  const el = wrapper.find('.pending-message-content').element as HTMLElement;
  Object.defineProperty(el, 'scrollWidth', { configurable: true, value: 100 });
  Object.defineProperty(el, 'clientWidth', { configurable: true, value: 100 });
  FakeResizeObserver.instances[FakeResizeObserver.instances.length - 1]?.trigger();
}

beforeEach(() => {
  FakeResizeObserver.instances = [];
  vi.stubGlobal('ResizeObserver', FakeResizeObserver);
});

describe('PendingMessageBar 单行截断与展开', () => {
  it('长文本被截断时出现 ▸，点击后 emit toggle', async () => {
    const wrapper = mountBar();
    markTruncated(wrapper);
    await flushPromises();

    expect(wrapper.find('.pending-message-chevron').text()).toBe('▸');
    expect(wrapper.classes()).toContain('pending-message-bar--expandable');

    await wrapper.find('.pending-message-bar').trigger('click');
    expect(wrapper.emitted('toggle')).toHaveLength(1);
  });

  it('短文本未被截断时不可点击，点击不 emit', async () => {
    const wrapper = mountBar();
    await flushPromises();

    expect(wrapper.find('.pending-message-chevron').exists()).toBe(false);
    expect(wrapper.classes()).not.toContain('pending-message-bar--expandable');
    expect(wrapper.attributes('role')).toBeUndefined();

    await wrapper.find('.pending-message-bar').trigger('click');
    expect(wrapper.emitted('toggle')).toBeUndefined();
  });

  it('多行文本在收起态里换行被合并成一个空格', async () => {
    const item = createItem({
      message: createMessage({ content: 'first line\n\nsecond   line\nthird' }),
    });
    const wrapper = mountBar({ item });
    await flushPromises();

    const text = wrapper.find('.pending-message-content').text();
    expect(text).toBe('first line second line third');
    expect(text).not.toContain('\n');
  });

  it('展开态渲染 Markdown 全文，预览行仍保留作为标题', async () => {
    const item = createItem({
      message: createMessage({ content: '**bold** body' }),
    });
    const wrapper = mountBar({ item, expanded: true });
    await flushPromises();

    expect(wrapper.find('.pending-message-full').exists()).toBe(true);
    expect(wrapper.find('.pending-message-full strong').text()).toBe('bold');
    // 预览行不消失：头像、徽标、按钮的位置不该因为展开而跳动
    expect(wrapper.find('.pending-message-content').exists()).toBe(true);
  });

  it('Esc 收起（仅在展开态）', async () => {
    const wrapper = mountBar({ expanded: true });
    markTruncated(wrapper);
    await flushPromises();

    await wrapper.find('.pending-message-bar').trigger('keydown', { key: 'Escape' });
    expect(wrapper.emitted('toggle')).toHaveLength(1);
  });

  it('Enter / Space 切换展开（可访问性）', async () => {
    const wrapper = mountBar();
    markTruncated(wrapper);
    await flushPromises();

    const bar = wrapper.find('.pending-message-bar');
    await bar.trigger('keydown', { key: 'Enter' });
    await bar.trigger('keydown', { key: ' ' });

    expect(wrapper.emitted('toggle')).toHaveLength(2);
    expect(bar.attributes('role')).toBe('button');
    expect(bar.attributes('tabindex')).toBe('0');
  });

  it('展开后窗口变宽不再截断，点击 / Esc 仍能收起（回归 P1）', async () => {
    // 场景：500 宽时截断 → 展开 → 拉宽到 2000，预览行不再省略。
    // 收起通路若 gate 在 truncated 上，这里点击和 Esc 都会失效、卡片再也收不起来。
    const wrapper = mountBar({ expanded: true });
    markNotTruncated(wrapper);
    await flushPromises();

    const bar = wrapper.find('.pending-message-bar');
    expect(bar.attributes('role')).toBe('button');
    expect(bar.attributes('tabindex')).toBe('0');
    expect(bar.attributes('aria-expanded')).toBe('true');

    await bar.trigger('click');
    await bar.trigger('keydown', { key: 'Escape' });

    expect(wrapper.emitted('toggle')).toHaveLength(2);
  });

  it('全文区内的点击不会误收起卡片（回归 P2）', async () => {
    const wrapper = mountBar({
      item: createItem({ message: createMessage({ content: '**bold** body' }) }),
      expanded: true,
    });
    markTruncated(wrapper);
    await flushPromises();

    await wrapper.find('.pending-message-full').trigger('click');

    // 在全文里选字 / 点链接 / 拖滚动条都不该把卡片收起来
    expect(wrapper.emitted('toggle')).toBeUndefined();
  });
});

describe('PendingMessageBar 徽标 tooltip 不被浮层裁剪（回归 P1）', () => {
  it('hover 徽标时 tooltip Teleport 到 body 并以 fixed 定位渲染', async () => {
    const wrapper = mountBar(
      { item: createItem({ status: 'queued' }) },
    );
    await flushPromises();

    // tooltip 挂在组件根之外（Teleport 到 body），所以不从 wrapper 内部找
    const tooltipBefore = document.body.querySelector('.pending-message-tooltip');
    expect(tooltipBefore).toBeNull();

    await wrapper.find('.pending-message-status').trigger('mouseenter');

    const tooltip = document.body.querySelector('.pending-message-tooltip') as HTMLElement | null;
    expect(tooltip).not.toBeNull();
    // 关键：fixed 定位 + 不在滚动容器内 → 不会被 .pending-message-dock__list 的
    // overflow-y:auto 裁掉（原先 ::after 方案在真渲染里只剩 7px 可见）
    expect(tooltip!.style.position).toBe('fixed');
    // tooltip 里放的是「说明」文案（queuedMessageTip），不是徽标上的短标签
    expect(tooltip!.textContent).toContain('将在 Agent 结束当前行动后发送');
    expect(wrapper.find('.pending-message-bar').element.contains(tooltip)).toBe(false);

    await wrapper.find('.pending-message-status').trigger('mouseleave');
    expect(document.body.querySelector('.pending-message-tooltip')).toBeNull();
  });

  it('加急徽标 hover 显示「将尽快注入」的提示文案', async () => {
    const wrapper = mountBar({ item: createItem({ status: 'pending-immediate' }) });
    await flushPromises();

    await wrapper.find('.pending-message-status').trigger('mouseenter');

    const tooltip = document.body.querySelector('.pending-message-tooltip') as HTMLElement | null;
    expect(tooltip).not.toBeNull();
    // 加急用 pendingImmediateTip，与排队消息的文案不同
    expect(tooltip!.textContent).toContain('下一次工具调用结束后注入');

    await wrapper.find('.pending-message-status').trigger('mouseleave');
  });
});

describe('PendingMessageBar 徽标与操作', () => {
  it('排队消息显示 ⏳ 与「排队中」', async () => {
    const wrapper = mountBar({ item: createItem({ status: 'queued' }) });
    await flushPromises();

    const badge = wrapper.find('.pending-message-status');
    expect(badge.text()).toContain('排队中');
    expect(badge.classes()).toContain('pending-message-status--queued');
  });

  it('加急消息显示 ⚡ 与「将尽快注入」，且不显示「立即引导」', async () => {
    const wrapper = mountBar({ item: createItem({ status: 'pending-immediate' }) });
    await flushPromises();

    const badge = wrapper.find('.pending-message-status');
    expect(badge.text()).toContain('将尽快注入');
    expect(badge.classes()).toContain('pending-message-status--pending-immediate');
    expect(wrapper.find('.pending-message-action').exists()).toBe(false);
  });

  it('点击「立即引导」不会触发展开', async () => {
    const wrapper = mountBar();
    markTruncated(wrapper);
    await flushPromises();

    await wrapper.find('.pending-message-action').trigger('click');

    // Bar 层只报「要引导这一条」，dbId 由 Dock 层从 item 里取（见 Dock 的 handleEscalate）
    expect(wrapper.emitted('escalate')).toHaveLength(1);
    expect(wrapper.emitted('toggle')).toBeUndefined();
  });

  it('引导中时按钮禁用且文案切换', async () => {
    const wrapper = mountBar({ escalating: true });
    await flushPromises();

    const button = wrapper.find('.pending-message-action');
    expect(button.attributes('disabled')).toBeDefined();
    expect(button.text()).toBe('引导中...');
  });

  it('canEscalate=false 时不显示「立即引导」', async () => {
    const wrapper = mountBar({ canEscalate: false });
    await flushPromises();

    expect(wrapper.find('.pending-message-action').exists()).toBe(false);
  });

  it('点击头像 emit clickSender，且不会触发展开', async () => {
    const item = createItem({ sender: { displayName: 'Alice', avatarUrl: '/a.png', clickable: true } });
    const wrapper = mountBar({ item });
    markTruncated(wrapper);
    await flushPromises();

    await wrapper.find('.pending-message-avatar').trigger('click');

    expect(wrapper.emitted('clickSender')).toHaveLength(1);
    expect(wrapper.emitted('toggle')).toBeUndefined();
  });
});

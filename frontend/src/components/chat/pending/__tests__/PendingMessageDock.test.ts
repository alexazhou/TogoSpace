import { beforeEach, describe, expect, it } from 'vitest';
import { flushPromises, mount } from '@vue/test-utils';
import PendingMessageDock from '../PendingMessageDock.vue';
import i18n from '../../../../i18n';
import type { MessageInfo } from '../../../../types';
import type { PendingMessageItem } from '../types';
// 假 ResizeObserver 已收敛到 src/__tests__/testMocks.ts（原先两个测试文件各写一份）
import { installFakeResizeObserver, triggerAllResizeObservers } from '../../../../__tests__/testMocks';

/** 把所有卡片的预览行都标记为「已截断」，使它们可点击展开。 */
function markAllTruncated(wrapper: ReturnType<typeof mount>): void {
  wrapper.findAll('.pending-message-content').forEach((node) => {
    const el = node.element as HTMLElement;
    Object.defineProperty(el, 'scrollWidth', { configurable: true, value: 400 });
    Object.defineProperty(el, 'clientWidth', { configurable: true, value: 100 });
  });
  triggerAllResizeObservers();
}

function createItem(index: number, overrides: Partial<PendingMessageItem> = {}): PendingMessageItem {
  const message: MessageInfo = {
    db_id: 100 + index,
    sender_id: -1,
    sender_display_name: 'OPERATOR',
    content: `message ${index}`,
    time: '2026-05-08T00:00:00Z',
    seq: index,
    insert_immediately: false,
  };
  return {
    key: String(100 + index),
    message,
    status: 'queued',
    sender: { displayName: 'OPERATOR', avatarUrl: '/avatar.png', clickable: false },
    ...overrides,
  };
}

function createItems(count: number): PendingMessageItem[] {
  return Array.from({ length: count }, (_, i) => createItem(i + 1));
}

function mountDock(items: PendingMessageItem[], extra: Record<string, unknown> = {}) {
  return mount(PendingMessageDock, {
    props: { items, ...extra },
    global: { plugins: [i18n] },
  });
}

beforeEach(() => {
  installFakeResizeObserver();
});

describe('PendingMessageDock 折叠', () => {
  it('1–3 条不折叠，全部直接显示，无折叠栏', async () => {
    for (const count of [1, 2, 3]) {
      const wrapper = mountDock(createItems(count));
      await flushPromises();

      expect(wrapper.findAll('.pending-message-bar')).toHaveLength(count);
      expect(wrapper.find('.pending-message-dock__collapsed-bar').exists()).toBe(false);
      wrapper.unmount();
    }
  });

  it('4 条时折叠：只显示最新 2 条 + 「还有 2 条待读消息」', async () => {
    const wrapper = mountDock(createItems(4));
    await flushPromises();

    const bars = wrapper.findAll('.pending-message-bar');
    expect(bars).toHaveLength(2);
    // 取最新两条（message 3 / 4），最旧的被折叠
    expect(bars[0]?.text()).toContain('message 3');
    expect(bars[1]?.text()).toContain('message 4');

    const collapseBar = wrapper.find('.pending-message-dock__collapsed-bar');
    expect(collapseBar.text()).toContain('还有 2 条待读消息');
  });

  it('点击折叠栏展开全部，再点收起', async () => {
    const wrapper = mountDock(createItems(5));
    await flushPromises();

    expect(wrapper.findAll('.pending-message-bar')).toHaveLength(2);
    expect(wrapper.find('.pending-message-dock__collapsed-bar').text()).toContain('还有 3 条待读消息');

    await wrapper.find('.pending-message-dock__collapsed-bar').trigger('click');
    expect(wrapper.findAll('.pending-message-bar')).toHaveLength(5);
    expect(wrapper.find('.pending-message-dock__collapsed-bar').text()).toContain('收起');

    await wrapper.find('.pending-message-dock__collapsed-bar').trigger('click');
    expect(wrapper.findAll('.pending-message-bar')).toHaveLength(2);
  });

  it('条数降到阈值内时折叠状态自动复位', async () => {
    const wrapper = mountDock(createItems(5));
    await flushPromises();
    await wrapper.find('.pending-message-dock__collapsed-bar').trigger('click');
    expect(wrapper.findAll('.pending-message-bar')).toHaveLength(5);

    await wrapper.setProps({ items: createItems(2) });
    await flushPromises();

    expect(wrapper.findAll('.pending-message-bar')).toHaveLength(2);
    expect(wrapper.find('.pending-message-dock__collapsed-bar').exists()).toBe(false);
  });

  it('自定义 collapseThreshold / visibleWhenCollapsed 生效', async () => {
    const wrapper = mountDock(createItems(4), {
      collapseThreshold: 1,
      visibleWhenCollapsed: 1,
    });
    await flushPromises();

    expect(wrapper.findAll('.pending-message-bar')).toHaveLength(1);
    expect(wrapper.find('.pending-message-dock__collapsed-bar').text()).toContain('还有 3 条待读消息');
  });
});

describe('PendingMessageDock 展开互斥', () => {
  it('展开 A 后再展开 B，A 自动收起（同一时间只有一条展开）', async () => {
    const wrapper = mountDock(createItems(2));
    await flushPromises();
    markAllTruncated(wrapper);
    await flushPromises();

    await wrapper.findAll('.pending-message-bar')[0]?.trigger('click');
    await flushPromises();
    expect(wrapper.findAll('.pending-message-full')).toHaveLength(1);
    expect(wrapper.findAll('.pending-message-bar')[0]?.classes()).toContain('pending-message-bar--expanded');

    await wrapper.findAll('.pending-message-bar')[1]?.trigger('click');
    await flushPromises();
    // 关键：A 必须收起，不能两条同时展开
    expect(wrapper.findAll('.pending-message-full')).toHaveLength(1);
    expect(wrapper.findAll('.pending-message-bar')[0]?.classes()).not.toContain('pending-message-bar--expanded');
    expect(wrapper.findAll('.pending-message-bar')[1]?.classes()).toContain('pending-message-bar--expanded');
  });

  it('再次点击同一条可收起', async () => {
    const wrapper = mountDock(createItems(2));
    await flushPromises();
    markAllTruncated(wrapper);
    await flushPromises();

    const bar = () => wrapper.findAll('.pending-message-bar')[0];
    await bar()?.trigger('click');
    await flushPromises();
    expect(wrapper.findAll('.pending-message-full')).toHaveLength(1);

    await bar()?.trigger('click');
    await flushPromises();
    expect(wrapper.findAll('.pending-message-full')).toHaveLength(0);
  });

  it('已展开的那条从 items 中消失后，展开状态被清空', async () => {
    const items = createItems(2);
    const wrapper = mountDock(items, { canEscalate: false });
    await flushPromises();
    markAllTruncated(wrapper);
    await flushPromises();

    await wrapper.findAll('.pending-message-bar')[0]?.trigger('click');
    await flushPromises();
    expect(wrapper.findAll('.pending-message-full')).toHaveLength(1);

    // 被展开的那条（key 101）被 Agent 读走
    await wrapper.setProps({ items: [items[1]!] });
    await flushPromises();

    // 剩下那条不能被「继承」展开态
    expect(wrapper.findAll('.pending-message-full')).toHaveLength(0);
    expect(wrapper.findAll('.pending-message-bar')[0]?.classes()).not.toContain('pending-message-bar--expanded');

    // 真正的风险点：expandedKey 若没被清空，同 key 的消息稍后回来（key 复用 / 重连重拉）
    // 会被自动展开。这里把 key=101 放回去，必须保持收起。
    await wrapper.setProps({ items: [items[0]!] });
    await flushPromises();
    expect(wrapper.findAll('.pending-message-full')).toHaveLength(0);
    expect(wrapper.findAll('.pending-message-bar')[0]?.classes()).not.toContain('pending-message-bar--expanded');
  });

  it('items 全部读走后折叠与展开状态复位', async () => {
    const wrapper = mountDock(createItems(5));
    await flushPromises();
    await wrapper.find('.pending-message-dock__collapsed-bar').trigger('click');
    expect(wrapper.findAll('.pending-message-bar')).toHaveLength(5);

    await wrapper.setProps({ items: [] });
    await flushPromises();
    expect(wrapper.find('.pending-message-dock').exists()).toBe(false);

    // 新一批消息进来时，不应沿用上一批的「展开全部」
    await wrapper.setProps({ items: createItems(5) });
    await flushPromises();
    expect(wrapper.findAll('.pending-message-bar')).toHaveLength(2);
  });
});

describe('PendingMessageDock 事件透传', () => {
  it('escalate 带上该条的 db_id', async () => {
    const wrapper = mountDock(createItems(1));
    await flushPromises();

    await wrapper.find('.pending-message-action').trigger('click');
    expect(wrapper.emitted('escalate')).toEqual([[101]]);
  });

  it('clickSender 带上发送者 id', async () => {
    const items = [createItem(1, { sender: { displayName: 'Alice', avatarUrl: '/a.png', clickable: true } })];
    const wrapper = mountDock(items);
    await flushPromises();

    await wrapper.find('.pending-message-avatar').trigger('click');
    expect(wrapper.emitted('clickSender')).toEqual([[-1]]);
  });

  it('escalatingIds 命中时对应卡片按钮禁用', async () => {
    const wrapper = mountDock(createItems(1), { escalatingIds: [101] });
    await flushPromises();

    expect(wrapper.find('.pending-message-action').attributes('disabled')).toBeDefined();
  });

  it('sticky 默认开启，可显式关闭', async () => {
    const stickyDock = mountDock(createItems(1));
    await flushPromises();
    expect(stickyDock.find('.pending-message-dock').classes()).toContain('pending-message-dock--sticky');

    const plainDock = mountDock(createItems(1), { sticky: false });
    await flushPromises();
    expect(plainDock.find('.pending-message-dock').classes()).not.toContain('pending-message-dock--sticky');
  });
});

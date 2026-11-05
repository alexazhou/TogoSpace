import { describe, expect, it } from 'vitest';
import { flushPromises, mount } from '@vue/test-utils';
import AgentActivityItem from '../AgentActivityItem.vue';
import i18n from '../../../i18n';
import type { AgentActivity } from '../../../types';

/**
 * 发言 / 思考内容的默认折叠状态。
 *
 * 契约：
 * - 发言内容（`send_chat_msg` 的 msg、`chat_reply` 的整段）**默认展开**，长内容仍可点「收起」；
 * - 思考内容（`reasoning`）**默认只露三行**，超出部分可点「展开」看全文。
 */
function activity(overrides: Partial<AgentActivity> = {}): AgentActivity {
  return {
    id: 1,
    agent_id: 8,
    team_id: 2,
    activity_type: 'reasoning',
    status: 'succeeded',
    title: '',
    detail: '',
    started_at: '2026-10-27 10:00:00.000000',
    duration_ms: 1200,
    metadata: { exec_env: 'host' },
    ...overrides,
  };
}

function mountItem(item: AgentActivity) {
  return mount(AgentActivityItem, {
    props: { activity: item },
    global: { plugins: [i18n] },
  });
}

const LONG_TEXT = Array.from({ length: 8 }, (_, i) => `第 ${i + 1} 行内容`).join('\n');

describe('AgentActivityItem 思考内容默认三行', () => {
  it('思考内容渲染成独立文本块，默认带三行折叠态', async () => {
    const wrapper = mountItem(activity({ detail: LONG_TEXT }));
    await flushPromises();

    const block = wrapper.find('.agent-activity-item__reasoning');
    expect(block.exists()).toBe(true);
    expect(block.classes()).toContain('agent-activity-item__reasoning--collapsed');
    expect(block.text()).toContain('第 1 行内容');
  });

  it('思考内容不再挤进单行摘要，而是用开头一个 💭 标记', async () => {
    const wrapper = mountItem(activity({ detail: '先看后端排序口径' }));
    await flushPromises();

    // 摘要行不再承载思考正文（原来会被截成 `💭 ...` 的一行）
    expect(wrapper.find('.agent-activity-item__summary').exists()).toBe(false);
    const block = wrapper.find('.agent-activity-item__reasoning');
    expect(block.text().startsWith('💭')).toBe(true);
    // 💭 在第一个字符位置，标记与正文之间只有标记自身的间距，没有额外缩进
    expect(block.find('.agent-activity-item__reasoning-mark').exists()).toBe(true);
  });

  it('思考条目不渲染「思考」标题行，正文块直接就是内容', async () => {
    const wrapper = mountItem(activity({ detail: LONG_TEXT }));
    await flushPromises();

    expect(wrapper.find('.agent-activity-item__row').exists()).toBe(false);
    expect(wrapper.text()).not.toContain('思考');
    expect(wrapper.find('.agent-activity-item__reasoning').exists()).toBe(true);
  });

  it('思考内容为空时仍保留标题行，不出现空白卡片', async () => {
    const wrapper = mountItem(activity({
      activity_type: 'reasoning',
      status: 'failed',
      detail: '',
      error_message: '模型返回空',
    }));
    await flushPromises();

    expect(wrapper.find('.agent-activity-item__row').exists()).toBe(true);
    expect(wrapper.text()).toContain('思考');
  });

  it('内容超出三行时可展开看全文，再点可收起', async () => {
    const wrapper = mountItem(activity({ detail: LONG_TEXT }));
    await flushPromises();

    const toggle = wrapper.find('.agent-activity-item__chat-reply-toggle');
    expect(toggle.exists()).toBe(true);
    expect(toggle.text()).toBe('展开');

    await toggle.trigger('click');
    expect(wrapper.find('.agent-activity-item__reasoning').classes())
      .not.toContain('agent-activity-item__reasoning--collapsed');
    expect(wrapper.find('.agent-activity-item__chat-reply-toggle').text()).toBe('收起');

    await wrapper.find('.agent-activity-item__chat-reply-toggle').trigger('click');
    expect(wrapper.find('.agent-activity-item__reasoning').classes())
      .toContain('agent-activity-item__reasoning--collapsed');
  });

  it('短思考内容不出现展开按钮', async () => {
    const wrapper = mountItem(activity({ detail: '两行以内容'.repeat(3) }));
    await flushPromises();

    expect(wrapper.find('.agent-activity-item__reasoning').exists()).toBe(true);
    expect(wrapper.find('.agent-activity-item__chat-reply-toggle').exists()).toBe(false);
  });
});

describe('AgentActivityItem 发言内容默认展开', () => {
  it('chat_reply 长内容默认展开，按钮初始为「收起」', () => {
    const wrapper = mountItem(activity({
      activity_type: 'chat_reply',
      detail: LONG_TEXT,
    }));

    const block = wrapper.find('.agent-activity-item__chat-reply');
    expect(block.exists()).toBe(true);
    expect(block.classes()).not.toContain('agent-activity-item__chat-reply--collapsed');
    expect(wrapper.find('.agent-activity-item__chat-reply-toggle').text()).toBe('收起');
  });

  it('send_chat_msg 长内容默认展开', () => {
    const wrapper = mountItem(activity({
      activity_type: 'tool_call',
      metadata: {
        tool_name: 'send_chat_msg',
        tool_arguments: { room_name: '阿张', msg: LONG_TEXT },
      },
    }));

    const block = wrapper.find('.agent-activity-item__chat-reply');
    expect(block.exists()).toBe(true);
    expect(block.classes()).not.toContain('agent-activity-item__chat-reply--collapsed');
    expect(wrapper.find('.agent-activity-item__chat-reply-toggle').text()).toBe('收起');
  });

  it('发言内容点「收起」后折叠，再点「展开」恢复', async () => {
    const wrapper = mountItem(activity({
      activity_type: 'chat_reply',
      detail: LONG_TEXT,
    }));

    await wrapper.find('.agent-activity-item__chat-reply-toggle').trigger('click');
    expect(wrapper.find('.agent-activity-item__chat-reply').classes())
      .toContain('agent-activity-item__chat-reply--collapsed');

    await wrapper.find('.agent-activity-item__chat-reply-toggle').trigger('click');
    expect(wrapper.find('.agent-activity-item__chat-reply').classes())
      .not.toContain('agent-activity-item__chat-reply--collapsed');
  });
});

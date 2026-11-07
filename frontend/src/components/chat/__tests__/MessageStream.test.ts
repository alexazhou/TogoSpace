import { describe, expect, it } from 'vitest';
import { mount } from '@vue/test-utils';
import MessageStream from '../MessageStream.vue';
import i18n from '../../../i18n';
import type { MessageInfo, RoomMemberProfile } from '../../../types';

const memberProfiles: RoomMemberProfile[] = [
  {
    id: 7,
    name: 'alice',
    i18n: {},
    employee_number: 1,
    role_template_name: 'Engineer',
    is_leader: false,
  },
];

function createMessage(overrides: Partial<MessageInfo> = {}): MessageInfo {
  return {
    db_id: 1,
    sender_id: 7,
    sender_display_name: 'Alice',
    content: 'plain text',
    time: '2026-05-08T00:00:00Z',
    seq: 1,
    insert_immediately: false,
    ...overrides,
  };
}

function createMember(overrides: Partial<RoomMemberProfile> = {}): RoomMemberProfile {
  return {
    id: 8,
    name: 'worker',
    i18n: {},
    employee_number: 2,
    role_template_name: 'Operator',
    is_leader: false,
    ...overrides,
  };
}

describe('MessageStream', () => {
  it('renders published messages with markdown formatting', () => {
    const wrapper = mount(MessageStream, {
      props: {
        messages: [
          createMessage({
            content: '**bold**\n\n```ts\nconst answer = 42;\n```',
          }),
        ],
        memberProfiles,
      },
      global: {
        plugins: [i18n],
      },
    });

    expect(wrapper.find('.bubble strong').text()).toBe('bold');
    expect(wrapper.find('.bubble pre code').text()).toContain('const answer = 42;');
  });

  it('keeps floating message previews as plain text summaries', () => {
    const wrapper = mount(MessageStream, {
      props: {
        messages: [
          // 浮动待读区只放「私聊 + Operator 发 + Agent 未读」的消息。
          createMessage({
            db_id: 2,
            sender_id: -1,
            seq: 5,
            content: 'Queue **now** with `code` and [link](https://example.com)',
          }),
        ],
        memberProfiles,
        isPrivate: true,
        agentReadSeq: 5,
      },
      global: {
        plugins: [i18n],
      },
    });

    const preview = wrapper.find('.pending-message-content').text();
    expect(preview).toContain('Queue now with code and link');
    expect(preview).not.toContain('**');
    expect(preview).not.toContain('`');
  });

  it('emits clickAgent when a sender avatar is clicked', async () => {
    const wrapper = mount(MessageStream, {
      props: {
        messages: [createMessage()],
        memberProfiles,
      },
      global: {
        plugins: [i18n],
      },
    });

    await wrapper.find('.sender-avatar').trigger('click');

    expect(wrapper.emitted('clickAgent')).toEqual([[7]]);
  });

  it('双 AI 单聊：Agent 1 发给 Agent 2 的未读消息进浮动待读区，读游标推进后移入主流并显示 ⚡', async () => {
    const wrapper = mount(MessageStream, {
      props: {
        messages: [
          createMessage({
            db_id: 10,
            sender_id: 1,
            seq: 5,
            insert_immediately: true,
            content: 'urgent: stop and re-check',
          }),
        ],
        memberProfiles,
        isPrivate: true,
        roomAgents: [1, 2],
        agentReadIndex: { '2': 5 },
      },
      global: {
        plugins: [i18n],
      },
    });

    // Agent 2 尚未读到 seq=5（游标语义：等于游标也还没读）→ 应挂在浮动待读区
    expect(wrapper.find('.pending-message-dock').exists()).toBe(true);
    expect(wrapper.findAll('.message-row').length).toBe(0);

    // Agent 2 读游标推进到 6 → 该消息转入主流，并保留「已立即注入」标记
    await wrapper.setProps({ agentReadIndex: { '2': 6 } });

    expect(wrapper.find('.pending-message-dock').exists()).toBe(false);
    expect(wrapper.findAll('.message-row').length).toBe(1);
    expect(wrapper.find('.msg-status--immediate').exists()).toBe(true);
  });

  it('Operator + AI 单聊：AI 发给 Operator 的消息始终在主流，不进浮动待读区', () => {
    const wrapper = mount(MessageStream, {
      props: {
        messages: [
          createMessage({ db_id: 11, sender_id: 1, seq: 7, content: 'report to operator' }),
        ],
        memberProfiles,
        isPrivate: true,
        roomAgents: [-1, 1],
        agentReadIndex: { '1': 0 },
      },
      global: {
        plugins: [i18n],
      },
    });

    // AI → Operator 方向没有 AI 接收方，恒视为已读
    expect(wrapper.find('.pending-message-dock').exists()).toBe(false);
    expect(wrapper.findAll('.message-row').length).toBe(1);
  });

  it('Operator + AI 单聊：Operator 发给 AI 的未读消息仍进浮动待读区', () => {
    const wrapper = mount(MessageStream, {
      props: {
        messages: [
          createMessage({ db_id: 12, sender_id: -1, seq: 5, content: 'operator guidance' }),
        ],
        memberProfiles,
        isPrivate: true,
        roomAgents: [-1, 1],
        agentReadIndex: { '1': 5 },
      },
      global: {
        plugins: [i18n],
      },
    });

    expect(wrapper.find('.pending-message-dock').exists()).toBe(true);
    expect(wrapper.findAll('.message-row').length).toBe(0);
  });

  it('Operator + AI 单聊：按接收方读游标判定已读（Operator 消息已读后不进浮动区）', () => {
    const wrapper = mount(MessageStream, {
      props: {
        messages: [
          createMessage({ db_id: 13, sender_id: -1, seq: 5, content: 'already read guidance' }),
        ],
        memberProfiles,
        isPrivate: true,
        roomAgents: [-1, 1],
        agentReadIndex: { '1': 9 },
      },
      global: {
        plugins: [i18n],
      },
    });

    // 接收方 AI(1) 的读游标已是 9 > 5，该消息已读，应进主流
    expect(wrapper.find('.pending-message-dock').exists()).toBe(false);
    expect(wrapper.findAll('.message-row').length).toBe(1);
  });

  it('docks the working indicator to the bottom when there are no floating messages', () => {
    const wrapper = mount(MessageStream, {
      props: {
        messages: [createMessage()],
        memberProfiles,
        workingAgent: createMember({ id: 9 }),
      },
      global: {
        plugins: [i18n],
      },
    });

    expect(wrapper.find('.working-indicator').classes()).toContain('working-indicator--dock-bottom');
  });

  it('does not dock the working indicator when floating messages are shown above the dock', () => {
    const wrapper = mount(MessageStream, {
      props: {
        messages: [
          createMessage(),
          createMessage({
            db_id: 3,
            sender_id: -1,
            seq: 6,
            content: 'queued task',
          }),
        ],
        memberProfiles,
        workingAgent: createMember({ id: 9 }),
        isPrivate: true,
        agentReadSeq: 6,
      },
      global: {
        plugins: [i18n],
      },
    });

    expect(wrapper.find('.working-indicator').classes()).not.toContain('working-indicator--dock-bottom');
  });
});

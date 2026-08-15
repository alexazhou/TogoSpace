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
          createMessage({
            db_id: 2,
            seq: null,
            content: 'Queue **now** with `code` and [link](https://example.com)',
          }),
        ],
        memberProfiles,
      },
      global: {
        plugins: [i18n],
      },
    });

    const preview = wrapper.find('.floating-message-content').text();
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
            seq: null,
            content: 'queued task',
          }),
        ],
        memberProfiles,
        workingAgent: createMember({ id: 9 }),
      },
      global: {
        plugins: [i18n],
      },
    });

    expect(wrapper.find('.working-indicator').classes()).not.toContain('working-indicator--dock-bottom');
  });
});

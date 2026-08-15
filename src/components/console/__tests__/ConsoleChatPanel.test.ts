import { describe, it, expect, vi, beforeEach } from 'vitest';
import { flushPromises, mount } from '@vue/test-utils';
import ConsoleChatPanel from '../ConsoleChatPanel.vue';
import i18n from '../../../i18n';
import type { RoomState } from '../../../types';

const {
  postRoomMessageMock,
  escalateMessageToImmediateMock,
} = vi.hoisted(() => ({
  postRoomMessageMock: vi.fn(),
  escalateMessageToImmediateMock: vi.fn(),
}));

vi.mock('../../../api', () => ({
  postRoomMessage: postRoomMessageMock,
  escalateMessageToImmediate: escalateMessageToImmediateMock,
}));

function createRoom(overrides: Partial<RoomState> = {}): RoomState {
  return {
    room_id: 9,
    room_name: 'Team大群',
    i18n: {},
    room_type: 'group',
    state: 'idle',
    need_scheduling: false,
    agents: [-1, 101, 102, 103],
    tags: [],
    biz_id: null,
    current_turn_agent_id: null,
    preview: '',
    unread: 0,
    ...overrides,
  };
}

describe('ConsoleChatPanel', () => {
  beforeEach(() => {
    postRoomMessageMock.mockReset();
    escalateMessageToImmediateMock.mockReset();
  });

  it('renders composer for group rooms when operator is a member', () => {
    const wrapper = mount(ConsoleChatPanel, {
      props: {
        currentRoom: createRoom(),
        agents: [],
        deptTree: null,
        roleTemplates: [],
        messages: [],
        hasMoreHistory: false,
        loadingOlderMessages: false,
        errorMessage: '',
        reloadingMessages: false,
        teamEnabled: true,
      },
      global: {
        plugins: [i18n],
      },
    });

    expect(wrapper.find('textarea').exists()).toBe(true);
    expect(wrapper.find('.composer-drag-zone').exists()).toBe(true);
    expect(wrapper.text()).not.toContain('当前为观察模式');
  });

  it('shows observe mode when operator is not a member of the room', () => {
    const wrapper = mount(ConsoleChatPanel, {
      props: {
        currentRoom: createRoom({ agents: [101, 102, 103] }),
        agents: [],
        deptTree: null,
        roleTemplates: [],
        messages: [],
        hasMoreHistory: false,
        loadingOlderMessages: false,
        errorMessage: '',
        reloadingMessages: false,
        teamEnabled: true,
      },
      global: {
        plugins: [i18n],
      },
    });

    expect(wrapper.find('textarea').exists()).toBe(false);
    expect(wrapper.find('.composer-drag-zone').exists()).toBe(false);
    expect(wrapper.text()).toContain('当前为观察模式');
  });

  it('submits messages to group rooms when operator is a member', async () => {
    const wrapper = mount(ConsoleChatPanel, {
      props: {
        currentRoom: createRoom(),
        agents: [],
        deptTree: null,
        roleTemplates: [],
        messages: [],
        hasMoreHistory: false,
        loadingOlderMessages: false,
        errorMessage: '',
        reloadingMessages: false,
        teamEnabled: true,
      },
      global: {
        plugins: [i18n],
      },
    });

    await wrapper.find('textarea').setValue('hello group');
    await wrapper.find('form').trigger('submit.prevent');
    await flushPromises();

    expect(postRoomMessageMock).toHaveBeenCalledWith(9, 'hello group');
  });
});

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushPromises, mount } from '@vue/test-utils';
import AgentActivityPanel from '../AgentActivityPanel.vue';
import i18n from '../../../i18n';
import type { AgentInfo, MessageInfo, RoomState } from '../../../types';
import type { RawMessageInfo } from '../../../api';
import {
  applyRealtimeEvent,
  clearRuntimeStore,
  seedRoomMessages,
  seedTeamAgents,
  seedTeamRooms,
} from '../../../realtime/runtimeStore';

const getAgentActivitiesPageMock = vi.fn();
const getRoomMessagesMock = vi.fn();
const escalateMessageToImmediateMock = vi.fn();

vi.mock('../../../api', () => ({
  getAgentActivitiesPage: (...args: unknown[]) => getAgentActivitiesPageMock(...args),
  getRoomMessages: (...args: unknown[]) => getRoomMessagesMock(...args),
  escalateMessageToImmediate: (...args: unknown[]) => escalateMessageToImmediateMock(...args),
}));

const TEAM_ID = 2;
const AGENT_ID = 7;
const CONTROL_ROOM_ID = 42;

function createRoom(overrides: Partial<RoomState> = {}): RoomState {
  return {
    room_id: CONTROL_ROOM_ID,
    room_name: 'operator-control',
    i18n: {},
    room_type: 'private',
    state: 'idle',
    need_scheduling: false,
    agents: [-1, AGENT_ID],
    current_turn_agent_id: null,
    preview: '',
    unread: 0,
    agent_read_index: { [String(AGENT_ID)]: 1 },
    ...overrides,
  };
}

function createAgent(overrides: Partial<AgentInfo> = {}): AgentInfo {
  return {
    id: AGENT_ID,
    name: 'worker',
    i18n: {},
    model: '',
    status: 'idle',
    team_id: TEAM_ID,
    ...overrides,
  };
}

function createMessage(overrides: Partial<MessageInfo> = {}): MessageInfo {
  return {
    db_id: 100,
    sender_id: -1,
    sender_display_name: 'OPERATOR',
    content: '先把索引补上',
    time: '2026-11-07T00:00:00Z',
    seq: 1,
    insert_immediately: false,
    ...overrides,
  };
}

/**
 * 面板首屏会拉一页控制房消息（`loadRoomMessagesState`），这一步会覆盖内存里的消息，
 * 所以控制房消息必须由接口 mock 提供，只在 store 里 seed 是不够的。
 */
function toRawMessage(message: MessageInfo): RawMessageInfo {
  return {
    id: message.db_id ?? 0,
    sender_id: message.sender_id,
    content: message.content,
    send_time: message.time,
    seq: message.seq,
    insert_immediately: message.insert_immediately,
  };
}

function serveControlRoomMessages(messages: MessageInfo[]): void {
  seedRoomMessages(CONTROL_ROOM_ID, messages);
  getRoomMessagesMock.mockResolvedValue({ messages: messages.map(toRawMessage), hasMore: false });
}

function mountPanel() {
  return mount(AgentActivityPanel, {
    props: { open: true, agentId: AGENT_ID, teamId: TEAM_ID },
    global: { plugins: [i18n] },
  });
}

beforeEach(() => {
  clearRuntimeStore();
  getAgentActivitiesPageMock.mockReset();
  getAgentActivitiesPageMock.mockResolvedValue({ activities: [], hasMore: false });
  getRoomMessagesMock.mockReset();
  getRoomMessagesMock.mockResolvedValue({ messages: [], hasMore: false });
  escalateMessageToImmediateMock.mockReset();
  escalateMessageToImmediateMock.mockResolvedValue(undefined);

  seedTeamAgents(TEAM_ID, [createAgent()]);
  seedTeamRooms(TEAM_ID, [createRoom()]);
});

afterEach(() => {
  clearRuntimeStore();
});

describe('AgentActivityPanel 待读区', () => {
  it('控制房里有未读消息（seq >= 读游标）时出现待读区，加急消息带闪电徽标', async () => {
    serveControlRoomMessages([
      createMessage({ db_id: 101, seq: 1, insert_immediately: true, content: '紧急：先停下来复核' }),
    ]);

    const wrapper = mountPanel();
    await flushPromises();

    const dock = wrapper.find('.pending-message-dock');
    expect(dock.exists()).toBe(true);
    expect(dock.text()).toContain('紧急：先停下来复核');
    expect(wrapper.find('.pending-message-status--pending-immediate').exists()).toBe(true);
  });

  it('WS 推 room_status 让读游标越过 seq 后待读区消失，随后 message_received 活动进主列表', async () => {
    serveControlRoomMessages([
      createMessage({ db_id: 102, seq: 1, content: '排队中的督导' }),
    ]);

    const wrapper = mountPanel();
    await flushPromises();
    expect(wrapper.find('.pending-message-dock').exists()).toBe(true);

    // 后端读到消息：先推进游标并广播 room_status
    applyRealtimeEvent({
      type: 'room_status',
      teamId: TEAM_ID,
      roomId: CONTROL_ROOM_ID,
      state: 'running',
      needScheduler: true,
      currentTurnAgentId: AGENT_ID,
      agentReadIndex: { [String(AGENT_ID)]: 2 },
    });
    await flushPromises();

    expect(wrapper.find('.pending-message-dock').exists()).toBe(false);

    // 紧接着写入 MESSAGE_RECEIVED 活动，进入主列表
    applyRealtimeEvent({
      type: 'agent_activity',
      agentId: AGENT_ID,
      activity: {
        id: 900,
        agent_id: AGENT_ID,
        team_id: TEAM_ID,
        activity_type: 'message_received',
        status: 'succeeded',
        title: '收到消息',
        detail: '排队中的督导',
        error_message: null,
        started_at: '2026-11-07T00:00:00',
        finished_at: '2026-11-07T00:00:01',
        duration_ms: 1000,
        metadata: {},
        created_at: null,
        updated_at: null,
      },
    });
    await flushPromises();

    expect(wrapper.findAll('.agent-activity-item').length).toBeGreaterThan(0);
  });

  it('WS 推 message_changed 把排队消息升级为加急后，徽标从「排队中」变成「将尽快注入」', async () => {
    serveControlRoomMessages([
      createMessage({ db_id: 103, seq: 1, content: '先看看这个', insert_immediately: false }),
    ]);

    const wrapper = mountPanel();
    await flushPromises();
    expect(wrapper.find('.pending-message-status--queued').exists()).toBe(true);
    expect(wrapper.find('.pending-message-status--pending-immediate').exists()).toBe(false);

    applyRealtimeEvent({
      type: 'message_changed',
      teamId: TEAM_ID,
      roomId: CONTROL_ROOM_ID,
      roomName: 'operator-control',
      message: createMessage({ db_id: 103, seq: 1, content: '先看看这个', insert_immediately: true }),
    });
    await flushPromises();

    expect(wrapper.find('.pending-message-status--pending-immediate').exists()).toBe(true);
    expect(wrapper.find('.pending-message-status--queued').exists()).toBe(false);
  });

  it('点排队消息的「立即引导」会打到控制房：room_id 是控制房、msg id 是消息 db_id', async () => {
    serveControlRoomMessages([
      createMessage({ db_id: 104, seq: 1, content: '这条要马上处理', insert_immediately: false }),
    ]);

    const wrapper = mountPanel();
    await flushPromises();

    const escalateButton = wrapper.find('.pending-message-action');
    expect(escalateButton.exists()).toBe(true);

    await escalateButton.trigger('click');
    await flushPromises();

    // 两个参数都要断言：room_id 错了会打到别的房间，db_id 错了会引导错消息。
    expect(escalateMessageToImmediateMock).toHaveBeenCalledTimes(1);
    expect(escalateMessageToImmediateMock).toHaveBeenCalledWith(CONTROL_ROOM_ID, 104);
  });
});

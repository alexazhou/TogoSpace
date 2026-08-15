import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { scheduleNotRunningReason, scheduleState, totalMessageCount } from '../../appUiState';
import type { AgentActivity, AgentInfo, MessageInfo, RoomState } from '../../types';
import {
  applyRealtimeEvent,
  clearRuntimeStore,
  getAgentActivities,
  getAgentStatus,
  getRoomMessageHistoryState,
  getRoomMessages,
  getTeamRooms,
  seedRoomMessages,
  seedTeamAgents,
  seedTeamRooms,
  setActiveRealtimeContext,
} from '../runtimeStore';

function createAgent(overrides: Partial<AgentInfo>): AgentInfo {
  return {
    id: 1,
    name: 'Alpha',
    i18n: {},
    model: '',
    status: 'idle',
    ...overrides,
  };
}

function createRoom(overrides: Partial<RoomState>): RoomState {
  return {
    room_id: 3,
    room_name: 'general',
    i18n: {},
    room_type: 'group',
    state: 'idle',
    need_scheduling: false,
    agents: [1],
    current_turn_agent_id: null,
    preview: '旧预览',
    unread: 0,
    ...overrides,
  };
}

function createMessage(overrides: Partial<MessageInfo>): MessageInfo {
  return {
    db_id: 1,
    sender_id: -1,
    sender_display_name: 'OPERATOR',
    content: 'message',
    time: '2026-05-04 22:24:00',
    seq: null,
    insert_immediately: false,
    ...overrides,
  };
}

function createActivity(overrides: Partial<AgentActivity> = {}): AgentActivity {
  return {
    id: 10,
    agent_id: 1,
    team_id: 1,
    activity_type: 'llm_infer',
    status: 'started',
    title: '推理',
    detail: '',
    error_message: null,
    started_at: '2026-05-06T10:00:00',
    finished_at: null,
    duration_ms: null,
    metadata: { model: 'test-model' },
    created_at: null,
    updated_at: null,
    ...overrides,
  };
}

describe('runtimeStore realtime events', () => {
  beforeEach(() => {
    clearRuntimeStore();
  });

  afterEach(() => {
    clearRuntimeStore();
  });

  it('updates preview and unread count for inactive rooms on message events', () => {
    seedTeamAgents(2, [createAgent({ id: 5, name: 'Alice' })]);
    seedTeamRooms(2, [createRoom({ room_id: 9, agents: [5], preview: '旧内容' })]);

    applyRealtimeEvent({
      type: 'message',
      teamId: 2,
      roomId: 9,
      roomName: 'general',
      message: createMessage({
        db_id: 11,
        sender_id: 5,
        sender_display_name: '',
        content: '新的进展',
        time: '2026-05-04 22:30:00',
      }),
    });

    const room = getTeamRooms(2)[0];
    expect(room?.preview).toBe('Alice: 新的进展');
    expect(room?.unread).toBe(1);
    expect(getRoomMessages(9)).toHaveLength(1);
  });

  it('keeps unread at zero and syncs total count for the active room', () => {
    seedTeamRooms(2, [createRoom({ room_id: 4, unread: 3 })]);
    seedRoomMessages(4, [createMessage({ db_id: 21, content: '已有消息' })]);
    setActiveRealtimeContext(2, 4);

    applyRealtimeEvent({
      type: 'message',
      teamId: 2,
      roomId: 4,
      roomName: 'general',
      message: createMessage({
        db_id: 22,
        content: '最新消息',
        time: '2026-05-04 22:35:00',
      }),
    });

    const room = getTeamRooms(2)[0];
    expect(room?.unread).toBe(0);
    expect(totalMessageCount.value).toBe(2);
    expect(getRoomMessages(4).map((message) => message.db_id)).toEqual([21, 22]);
  });

  it('re-sorts messages when a queued message receives seq via message_changed', () => {
    seedRoomMessages(3, [
      createMessage({ db_id: 27, content: '测试27', seq: 0, time: '2026-05-04 22:24:29' }),
      createMessage({ db_id: 28, content: '测试28', seq: null, time: '2026-05-04 22:24:41' }),
    ]);

    applyRealtimeEvent({
      type: 'message_changed',
      teamId: 2,
      roomId: 3,
      roomName: '小马哥',
      message: createMessage({ db_id: 28, content: '测试28', seq: 1, time: '2026-05-04 22:24:41' }),
    });

    const messages = getRoomMessages(3);
    expect(messages.map((message) => message.db_id)).toEqual([27, 28]);
    expect(messages.map((message) => message.seq)).toEqual([0, 1]);
  });

  it('updates agent and room state from realtime status events', () => {
    seedTeamAgents(2, [createAgent({ id: 8, status: 'idle' })]);
    seedTeamRooms(2, [createRoom({ room_id: 6, state: 'idle', need_scheduling: false })]);

    applyRealtimeEvent({
      type: 'agent_status',
      teamId: 2,
      agentId: 8,
      agentName: 'Alpha',
      status: 'active',
    });
    applyRealtimeEvent({
      type: 'room_status',
      teamId: 2,
      roomId: 6,
      state: 'scheduling',
      needScheduler: true,
      currentTurnAgentId: 8,
    });

    expect(getAgentStatus(8)).toBe('active');
    const room = getTeamRooms(2)[0];
    expect(room?.state).toBe('scheduling');
    expect(room?.need_scheduling).toBe(true);
    expect(room?.current_turn_agent_id).toBe(8);
  });

  it('keeps the activity array stable when a realtime activity changes status', () => {
    applyRealtimeEvent({
      type: 'agent_activity',
      agentId: 1,
      activity: createActivity(),
    });

    const beforeUpdate = getAgentActivities(1);
    const beforeActivity = beforeUpdate[0];

    applyRealtimeEvent({
      type: 'agent_activity',
      agentId: 1,
      activity: createActivity({
        status: 'succeeded',
        finished_at: '2026-05-06T10:00:03',
        duration_ms: 3000,
        metadata: {
          model: 'test-model',
          final_total_tokens: 128,
        },
      }),
    });

    const afterUpdate = getAgentActivities(1);

    expect(afterUpdate).toBe(beforeUpdate);
    expect(afterUpdate).toHaveLength(1);
    expect(afterUpdate[0]).toBe(beforeActivity);
    expect(afterUpdate[0]).toMatchObject({
      id: 10,
      status: 'succeeded',
      duration_ms: 3000,
      metadata: {
        final_total_tokens: 128,
      },
    });
  });

  it('adds rooms only once and syncs schedule state events', () => {
    seedTeamRooms(2, [createRoom({ room_id: 6 })]);

    const newRoom = createRoom({
      room_id: 7,
      room_name: 'ops',
      preview: '初始预览',
      unread: 0,
    });

    applyRealtimeEvent({
      type: 'room_added',
      teamId: 2,
      room: newRoom,
    });
    applyRealtimeEvent({
      type: 'room_added',
      teamId: 2,
      room: newRoom,
    });
    applyRealtimeEvent({
      type: 'schedule_state',
      scheduleState: 'blocked',
      notRunningReason: 'manual_pause',
    });

    expect(getTeamRooms(2).map((room) => room.room_id)).toEqual([6, 7]);
    expect(scheduleState.value).toBe('blocked');
    expect(scheduleNotRunningReason.value).toBe('manual_pause');
  });
});

describe('runtimeStore message history state', () => {
  beforeEach(() => {
    clearRuntimeStore();
  });

  afterEach(() => {
    clearRuntimeStore();
  });

  it('returns default history state for unknown room', () => {
    expect(getRoomMessageHistoryState(null)).toEqual({ hasMoreHistory: false, loadingHistory: false });
    expect(getRoomMessageHistoryState(999)).toEqual({ hasMoreHistory: false, loadingHistory: false });
  });

  it('seedRoomMessages stores hasMoreHistory flag', () => {
    seedRoomMessages(1, [createMessage({ db_id: 10 })], { hasMoreHistory: true });
    expect(getRoomMessageHistoryState(1).hasMoreHistory).toBe(true);
    expect(getRoomMessages(1)).toHaveLength(1);
  });

  it('seedRoomMessages without hasMoreHistory defaults to false', () => {
    seedRoomMessages(1, [createMessage({ db_id: 10 })]);
    expect(getRoomMessageHistoryState(1).hasMoreHistory).toBe(false);
  });

  it('seedRoomMessages with preserveExisting merges messages and preserves hasMoreHistory', () => {
    seedRoomMessages(1, [createMessage({ db_id: 20, content: 'newer' })], { hasMoreHistory: true });
    seedRoomMessages(1, [createMessage({ db_id: 10, content: 'older' })], {
      preserveExisting: true,
      hasMoreHistory: false,
    });

    const messages = getRoomMessages(1);
    expect(messages.map((m) => m.db_id)).toEqual([10, 20]);
    expect(getRoomMessageHistoryState(1).hasMoreHistory).toBe(false);
  });

  it('realtime message event preserves hasMoreHistory', () => {
    seedRoomMessages(5, [createMessage({ db_id: 30 })], { hasMoreHistory: true });

    applyRealtimeEvent({
      type: 'message',
      teamId: 1,
      roomId: 5,
      roomName: 'test',
      message: createMessage({ db_id: 31, content: 'new', time: '2026-05-04 22:40:00' }),
    });

    expect(getRoomMessageHistoryState(5).hasMoreHistory).toBe(true);
    expect(getRoomMessages(5)).toHaveLength(2);
  });
});

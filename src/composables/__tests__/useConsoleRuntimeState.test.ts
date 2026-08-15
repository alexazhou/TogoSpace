import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { effectScope, nextTick, ref, type EffectScope, type Ref } from 'vue';
import type { AgentInfo, MessageInfo, RoomState } from '../../types';

const mocks = vi.hoisted(() => ({
  roomsRef: null as Ref<RoomState[]> | null,
  agentsRef: null as Ref<AgentInfo[]> | null,
  messagesRef: null as Ref<MessageInfo[]> | null,
  messageHistoryRef: null as Ref<{ hasMoreHistory: boolean; loadingHistory: boolean }> | null,
  loadOlderRoomMessagesStateMock: vi.fn(),
  loadRoomMessagesStateMock: vi.fn(),
  loadTeamAgentsMock: vi.fn(),
  loadTeamRoomsMock: vi.fn(),
  setActiveRealtimeContextMock: vi.fn(),
}));

vi.mock('../../realtime/selectors', () => ({
  useTeamRooms: () => mocks.roomsRef,
  useTeamAgents: () => mocks.agentsRef,
  useRoomMessages: () => mocks.messagesRef,
  useRoomMessageHistory: () => mocks.messageHistoryRef,
}));

vi.mock('../../realtime/runtimeStore', () => ({
  loadOlderRoomMessagesState: mocks.loadOlderRoomMessagesStateMock,
  loadRoomMessagesState: mocks.loadRoomMessagesStateMock,
  loadTeamAgents: mocks.loadTeamAgentsMock,
  loadTeamRooms: mocks.loadTeamRoomsMock,
  setActiveRealtimeContext: mocks.setActiveRealtimeContextMock,
}));

import { useConsoleRuntimeState } from '../useConsoleRuntimeState';

function createRoom(overrides: Partial<RoomState>): RoomState {
  return {
    room_id: 1,
    room_name: 'general',
    i18n: {},
    room_type: 'group',
    state: 'idle',
    need_scheduling: false,
    agents: [],
    current_turn_agent_id: null,
    preview: '',
    unread: 0,
    ...overrides,
  };
}

describe('useConsoleRuntimeState', () => {
  let scope: EffectScope | null = null;

  beforeEach(() => {
    mocks.roomsRef = ref([]);
    mocks.agentsRef = ref([]);
    mocks.messagesRef = ref([]);
    mocks.messageHistoryRef = ref({ hasMoreHistory: false, loadingHistory: false });
    mocks.loadOlderRoomMessagesStateMock.mockReset();
    mocks.loadRoomMessagesStateMock.mockReset();
    mocks.loadTeamAgentsMock.mockReset();
    mocks.loadTeamRoomsMock.mockReset();
    mocks.setActiveRealtimeContextMock.mockReset();
  });

  afterEach(() => {
    scope?.stop();
    scope = null;
  });

  function mountHarness(options?: { teamId?: number; routeRoomId?: number | null }) {
    const teamId = ref(options?.teamId ?? 2);
    const routeRoomId = ref<number | null>(options?.routeRoomId ?? null);
    const navigateToRoom = vi.fn().mockResolvedValue(undefined);

    scope = effectScope();
    const state = scope.run(() => useConsoleRuntimeState({
      teamId,
      routeRoomId,
      navigateToRoom,
    }));

    if (!state) {
      throw new Error('Failed to create console runtime state');
    }

    return {
      teamId,
      routeRoomId,
      navigateToRoom,
      state,
    };
  }

  it('loads team agents and rooms during refresh', async () => {
    const nextAgents = [{ id: 7, name: 'Alice', i18n: {}, model: '', status: 'idle' }] satisfies AgentInfo[];
    const nextRooms = [createRoom({ room_id: 9 })];
    mocks.loadTeamAgentsMock.mockResolvedValue(nextAgents);
    mocks.loadTeamRoomsMock.mockResolvedValue(nextRooms);

    const { state } = mountHarness({ teamId: 5 });

    const result = await state.refreshRuntimeState();

    expect(mocks.loadTeamAgentsMock).toHaveBeenCalledWith(5, { includeSpecial: true });
    expect(mocks.loadTeamRoomsMock).toHaveBeenCalledWith(5);
    expect(result).toEqual({ agents: nextAgents, rooms: nextRooms });
  });

  it('loads room messages and synchronizes route by default', async () => {
    const { state, navigateToRoom } = mountHarness({ teamId: 3, routeRoomId: null });

    mocks.loadRoomMessagesStateMock.mockResolvedValue([]);
    mocks.setActiveRealtimeContextMock.mockClear();

    await state.loadRoomMessages(12);
    await nextTick();

    expect(mocks.loadRoomMessagesStateMock).toHaveBeenCalledWith(3, 12);
    expect(state.selectedRoomId.value).toBe(12);
    expect(navigateToRoom).toHaveBeenCalledWith(12, false);
    expect(mocks.setActiveRealtimeContextMock).toHaveBeenLastCalledWith(3, 12);
  });

  it('skips reload when selecting the same room without force', async () => {
    const { state, navigateToRoom } = mountHarness({ teamId: 3 });

    mocks.loadRoomMessagesStateMock.mockResolvedValue([]);
    await state.loadRoomMessages(12);
    await nextTick();

    mocks.loadRoomMessagesStateMock.mockClear();
    navigateToRoom.mockClear();

    await state.loadRoomMessages(12);
    await nextTick();

    expect(mocks.loadRoomMessagesStateMock).not.toHaveBeenCalled();
    expect(navigateToRoom).not.toHaveBeenCalled();
  });

  it('supports suppressing route sync and clearing runtime context', async () => {
    const { state, navigateToRoom } = mountHarness({ teamId: 4, routeRoomId: 7 });

    mocks.loadRoomMessagesStateMock.mockResolvedValue([]);
    await state.loadRoomMessages(7, { force: true, syncRoute: false });
    await nextTick();

    expect(navigateToRoom).not.toHaveBeenCalled();

    state.clearSelectedRoom();
    await nextTick();
    expect(state.selectedRoomId.value).toBeNull();
    expect(mocks.setActiveRealtimeContextMock).toHaveBeenLastCalledWith(4, null);

    state.clearRuntimeContext();
    expect(mocks.setActiveRealtimeContextMock).toHaveBeenLastCalledWith(null, null);
  });

  it('loads older messages for the selected room', async () => {
    const { state } = mountHarness({ teamId: 8 });

    mocks.loadRoomMessagesStateMock.mockResolvedValue([]);
    mocks.loadOlderRoomMessagesStateMock.mockResolvedValue([]);

    await state.loadRoomMessages(18);
    await state.loadOlderMessages();

    expect(mocks.loadOlderRoomMessagesStateMock).toHaveBeenCalledWith(8, 18);
  });
});

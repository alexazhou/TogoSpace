import { computed, ref, toValue, watch, type MaybeRefOrGetter } from 'vue';
import {
  loadOlderRoomMessagesState,
  loadRoomMessagesState,
  loadTeamAgents,
  loadTeamRooms,
  setActiveRealtimeContext,
} from '../realtime/runtimeStore';
import { useRoomMessageHistory, useRoomMessages, useTeamAgents, useTeamRooms } from '../realtime/selectors';
import type { AgentInfo, RoomState } from '../types';

type LoadRoomMessagesOptions = {
  force?: boolean;
  replaceRoute?: boolean;
  syncRoute?: boolean;
};

type UseConsoleRuntimeStateOptions = {
  teamId: MaybeRefOrGetter<number>;
  routeRoomId: MaybeRefOrGetter<number | null>;
  navigateToRoom: (roomId: number, replace?: boolean) => Promise<void>;
};

export function useConsoleRuntimeState(options: UseConsoleRuntimeStateOptions) {
  const selectedRoomId = ref<number | null>(null);

  const rooms = useTeamRooms(() => toValue(options.teamId));
  const agents = useTeamAgents(() => toValue(options.teamId));
  const messages = useRoomMessages(selectedRoomId);
  const messageHistory = useRoomMessageHistory(selectedRoomId);
  const currentRoom = computed(
    () => rooms.value.find((room) => room.room_id === selectedRoomId.value) ?? null,
  );

  async function refreshRuntimeState(): Promise<{ agents: AgentInfo[]; rooms: RoomState[] }> {
    const teamId = toValue(options.teamId);
    const [nextAgents, nextRooms] = await Promise.all([
      loadTeamAgents(teamId, { includeSpecial: true }),
      loadTeamRooms(teamId),
    ]);

    return {
      agents: nextAgents,
      rooms: nextRooms,
    };
  }

  async function loadRoomMessages(
    roomId: number,
    loadOptions?: LoadRoomMessagesOptions,
  ): Promise<void> {
    if (!loadOptions?.force && selectedRoomId.value === roomId) {
      return;
    }

    await loadRoomMessagesState(toValue(options.teamId), roomId);
    selectedRoomId.value = roomId;
    setActiveRealtimeContext(toValue(options.teamId), roomId);

    if (
      loadOptions?.syncRoute !== false
      && toValue(options.routeRoomId) !== roomId
    ) {
      await options.navigateToRoom(roomId, loadOptions?.replaceRoute ?? false);
    }
  }

  async function loadOlderMessages(): Promise<void> {
    const roomId = selectedRoomId.value;
    if (roomId === null) {
      return;
    }
    await loadOlderRoomMessagesState(toValue(options.teamId), roomId);
  }

  function clearSelectedRoom(): void {
    selectedRoomId.value = null;
    setActiveRealtimeContext(toValue(options.teamId), null);
  }

  function clearRuntimeContext(): void {
    setActiveRealtimeContext(null, null);
  }

  watch(
    () => [toValue(options.teamId), selectedRoomId.value] as const,
    ([teamId, roomId]) => {
      setActiveRealtimeContext(teamId, roomId);
    },
    { immediate: true },
  );

  return {
    agents,
    currentRoom,
    messages,
    hasMoreHistory: computed(() => messageHistory.value.hasMoreHistory),
    loadingOlderMessages: computed(() => messageHistory.value.loadingHistory),
    rooms,
    selectedRoomId,
    clearSelectedRoom,
    refreshRuntimeState,
    loadRoomMessages,
    loadOlderMessages,
    clearRuntimeContext,
  };
}

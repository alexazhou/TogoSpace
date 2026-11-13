import type { RawMessageInfo, RawRoomInfo, RawRoomLastMessageInfo } from './types/raw';
import { makeWsUrl, requestJson, withSearch } from './client';
import { normalizeRoom } from './normalizers/room';
import type { RoomInfo } from '../types';

export async function getRooms(teamId?: number): Promise<RoomInfo[]> {
  const data = await requestJson<{ rooms: RawRoomInfo[] }>(
    withSearch('/rooms/list.json', { team_id: teamId }),
  );
  return data.rooms.map(normalizeRoom);
}

export async function createTeamRoom(teamId: number, payload: {
  name: string;
  agent_ids: number[];
  initial_topic?: string | null;
  max_turns?: number;
}): Promise<{ status: string; room_name: string }> {
  const { max_turns, ...rest } = payload;
  return requestJson(`/teams/${teamId}/rooms/create.json`, {
    method: 'POST',
    body: JSON.stringify({
      initial_topic: null,
      ...rest,
      ...(max_turns !== undefined ? { max_rounds: max_turns } : {}),
    }),
  });
}

export async function updateTeamRoom(teamId: number, roomId: number, payload: {
  name?: string;
}): Promise<{ status: string; room_name: string }> {
  return requestJson(`/teams/${teamId}/rooms/${roomId}/modify.json`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export type GetRoomMessagesOptions = {
  limit?: number;
  beforeId?: number | null;
};

export type RoomMessagesPage = {
  messages: RawMessageInfo[];
  hasMore: boolean;
};

export async function getRoomMessages(
  roomId: number,
  options?: GetRoomMessagesOptions,
): Promise<RoomMessagesPage> {
  const data = await requestJson<{
    messages: RawMessageInfo[];
    pagination?: {
      has_more?: boolean;
    };
  }>(
    withSearch(`/rooms/${roomId}/messages/list.json`, {
      limit: options?.limit,
      before_id: options?.beforeId,
    }),
  );
  return {
    messages: data.messages ?? [],
    hasMore: Boolean(data.pagination?.has_more),
  };
}

export async function getRoomLastMessages(roomIds: number[]): Promise<RawRoomLastMessageInfo[]> {
  const data = await requestJson<{
    messages: RawRoomLastMessageInfo[];
  }>('/rooms/last_messages.json', {
    method: 'POST',
    body: JSON.stringify({ room_ids: roomIds }),
  });
  return data.messages ?? [];
}

export async function postRoomMessage(roomId: number, content: string, insertImmediately = false): Promise<void> {
  await requestJson(`/rooms/${roomId}/messages/send.json`, {
    method: 'POST',
    body: JSON.stringify({ content, insert_immediately: insertImmediately }),
  });
}

export async function escalateMessageToImmediate(roomId: number, msgId: number): Promise<void> {
  await requestJson(`/rooms/${roomId}/messages/${msgId}/escalate_to_immediate.json`, {
    method: 'POST',
  });
}

export function createEventsSocket(): WebSocket {
  return new WebSocket(makeWsUrl('/ws/events.json'));
}

// ── System Status & Quick Init (V13) ──

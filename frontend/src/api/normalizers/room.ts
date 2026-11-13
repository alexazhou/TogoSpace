import type { RawRoomInfo } from '../types/raw';
import { normalizeAgentReadIndex, normalizeEntityI18n } from './common';
import type { RoomInfo } from '../../types';

export function normalizeRoom(room: RawRoomInfo): RoomInfo {
  const gtRoom = room.gt_room;
  const roomName = String(gtRoom?.name ?? '');
  const roomType = String(gtRoom?.type ?? 'group').toLowerCase();
  const currentTurnAgentId = Number(room.current_turn_agent_id ?? 0);

  return {
    room_id: Number(gtRoom?.id ?? 0),
    room_name: roomName,
    i18n: normalizeEntityI18n(gtRoom?.i18n),
    room_type: roomType === 'private' ? 'private' : 'group',
    state: (room.state ?? 'idle').toLowerCase(),
    need_scheduling: Boolean(room.need_scheduling),
    agents: Array.isArray(room.agents)
      ? room.agents
        .filter((agent) => agent !== null && agent !== undefined)
        .map((agent) => typeof agent === 'number' ? agent : Number(agent))
        .filter((id): id is number => Number.isFinite(id) && id !== -2)
      : [],
    tags: Array.isArray(gtRoom?.tags)
      ? gtRoom.tags.filter((tag): tag is string => typeof tag === 'string')
      : [],
    biz_id: typeof gtRoom?.biz_id === 'string' && gtRoom.biz_id.trim() ? gtRoom.biz_id : null,
    current_turn_agent_id: Number.isFinite(currentTurnAgentId) && currentTurnAgentId > 0
      ? currentTurnAgentId
      : null,
    agent_read_index: normalizeAgentReadIndex(room.agent_read_index),
  };
}

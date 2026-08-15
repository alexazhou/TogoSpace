import type {
  AgentActivity,
  AgentActivityStatus,
  AgentActivityType,
  AgentStatus,
  AgentTask,
  MessageInfo,
  RoomState,
} from '../types';

export type FrontendRealtimeEvent =
  | {
    type: 'message';
    teamId: number;
    roomId: number;
    roomName: string;
    message: MessageInfo;
  }
  | {
    type: 'message_changed';
    teamId: number;
    roomId: number;
    roomName: string;
    message: MessageInfo;
  }
  | {
    type: 'agent_status';
    teamId: number;
    agentId: number;
    agentName: string;
    status: AgentStatus;
  }
  | {
    type: 'agent_activity';
    agentId: number;
    activity: AgentActivity;
  }
  | {
    type: 'room_status';
    teamId: number;
    roomId: number;
    state: string;
    needScheduler: boolean;
    currentTurnAgentId: number | null;
  }
  | {
    type: 'schedule_state';
    scheduleState: 'stopped' | 'blocked' | 'running';
    notRunningReason: string;
  }
  | {
    type: 'room_added';
    teamId: number;
    room: RoomState;
  }
  | {
    type: 'team_reloaded';
    teamId: number;
  }
  | {
    type: 'task_created';
    teamId: number;
    task: AgentTask;
  }
  | {
    type: 'task_changed';
    teamId: number;
    task: AgentTask;
    oldStatus?: string;
  };

type RawRecord = Record<string, unknown>;

function normalizeAgentStatus(value: unknown): AgentStatus {
  const normalized = String(value ?? '').trim().toLowerCase();
  if (normalized === 'active' || normalized === 'failed') {
    return normalized;
  }
  return 'idle';
}

function normalizeActivityType(value: unknown): AgentActivityType {
  return (String(value ?? '').trim().toLowerCase() || 'unknown') as AgentActivityType;
}

function normalizeActivityStatus(value: unknown): AgentActivityStatus {
  const normalized = String(value ?? '').trim().toLowerCase();
  if (normalized === 'started' || normalized === 'succeeded' || normalized === 'failed') {
    return normalized;
  }
  return 'cancelled';
}

function normalizeAgentActivity(value: unknown): AgentActivity | null {
  if (!value || typeof value !== 'object') {
    return null;
  }

  const raw = value as RawRecord;
  const agentId = Number(raw.agent_id ?? 0);
  if (!Number.isFinite(agentId) || agentId <= 0) {
    return null;
  }

  return {
    id: Number(raw.id ?? 0),
    agent_id: agentId,
    team_id: Number(raw.team_id ?? 0),
    activity_type: normalizeActivityType(raw.activity_type),
    status: normalizeActivityStatus(raw.status),
    title: String(raw.title ?? ''),
    detail: typeof raw.detail === 'string' ? raw.detail : '',
    error_message: typeof raw.error_message === 'string' ? raw.error_message : null,
    started_at: typeof raw.started_at === 'string' ? raw.started_at : null,
    finished_at: typeof raw.finished_at === 'string' ? raw.finished_at : null,
    duration_ms: typeof raw.duration_ms === 'number' ? raw.duration_ms : null,
    metadata: typeof raw.metadata === 'object' && raw.metadata !== null
      ? raw.metadata as Record<string, unknown>
      : {},
    created_at: typeof raw.created_at === 'string' ? raw.created_at : null,
    updated_at: typeof raw.updated_at === 'string' ? raw.updated_at : null,
  };
}

function normalizeAgentTask(value: unknown): AgentTask | null {
  if (!value || typeof value !== 'object') {
    return null;
  }

  const raw = value as RawRecord;
  return {
    id: Number(raw.id ?? 0),
    team_id: Number(raw.team_id ?? 0),
    title: String(raw.title ?? ''),
    description: typeof raw.description === 'string' ? raw.description : '',
    assignee_id: Number(raw.assignee_id ?? 0),
    creator_id: Number(raw.creator_id ?? 0),
    manager_id: typeof raw.manager_id === 'number' ? raw.manager_id : null,
    status: (String(raw.status ?? '').trim().toUpperCase() || 'TODO') as any,
    priority: (String(raw.priority ?? '').trim().toUpperCase() || 'NORMAL') as any,
    parent_id: typeof raw.parent_id === 'number' ? raw.parent_id : null,
    depends_on: Array.isArray(raw.depends_on)
      ? raw.depends_on
        .map((item) => Number(item))
        .filter((item) => Number.isFinite(item) && item > 0)
      : [],
    room_id: typeof raw.room_id === 'number' ? raw.room_id : null,
    result: typeof raw.result === 'string' ? raw.result : '',
    block_reason: typeof raw.block_reason === 'string' ? raw.block_reason : '',
    created_at: typeof raw.created_at === 'string' ? raw.created_at : null,
    updated_at: typeof raw.updated_at === 'string' ? raw.updated_at : null,
  };
}

export function normalizeWsEventPayload(payload: unknown): FrontendRealtimeEvent | null {
  if (!payload || typeof payload !== 'object') {
    return null;
  }

  const raw = payload as RawRecord;
  const eventType = String(raw.event ?? '').trim().toLowerCase();

  if (eventType === 'message' || eventType === 'message_changed') {
    const gtRoom = raw.gt_room as RawRecord | undefined;
    const teamId = Number(gtRoom?.team_id ?? 0);
    const roomId = Number(gtRoom?.id ?? 0);
    if (!Number.isFinite(teamId) || !Number.isFinite(roomId) || teamId <= 0 || roomId <= 0) {
      return null;
    }

    const gtMessage = raw.gt_message as RawRecord | undefined;
    if (!gtMessage) {
      return null;
    }

    const dbId = Number(gtMessage.db_id ?? gtMessage.id ?? NaN);

    const message: MessageInfo = {
      db_id: Number.isFinite(dbId) && dbId > 0 ? dbId : null,
      sender_id: Number(gtMessage.sender_id ?? 0),
      sender_display_name: String(gtMessage.sender_display_name ?? ''),
      content: String(gtMessage.content ?? ''),
      time: String(gtMessage.send_time ?? ''),
      seq: typeof gtMessage.seq === 'number' ? gtMessage.seq : null,
      insert_immediately: Boolean(gtMessage.insert_immediately),
    };

    return {
      type: eventType === 'message' ? 'message' : 'message_changed',
      teamId,
      roomId,
      roomName: String(gtRoom?.name ?? ''),
      message,
    };
  }

  if (eventType === 'agent_status') {
    const gtAgent = raw.gt_agent as RawRecord | undefined;
    const teamId = Number(gtAgent?.team_id ?? 0);
    const agentId = Number(gtAgent?.id ?? 0);
    if (!Number.isFinite(teamId) || !Number.isFinite(agentId) || teamId <= 0 || agentId <= 0) {
      return null;
    }

    return {
      type: 'agent_status',
      teamId,
      agentId,
      agentName: String(gtAgent?.name ?? ''),
      status: normalizeAgentStatus(raw.status),
    };
  }

  if (eventType === 'agent_activity') {
    const activity = normalizeAgentActivity(raw.activity ?? raw.data);
    if (!activity) {
      return null;
    }

    return {
      type: 'agent_activity',
      agentId: activity.agent_id,
      activity,
    };
  }

  if (eventType === 'room_status') {
    const gtRoom = raw.gt_room as RawRecord | undefined;
    const teamId = Number(gtRoom?.team_id ?? 0);
    const roomId = Number(gtRoom?.id ?? 0);
    const currentTurnAgentId = Number(raw.current_turn_agent_id ?? 0);
    if (!Number.isFinite(teamId) || !Number.isFinite(roomId) || teamId <= 0 || roomId <= 0) {
      return null;
    }

    return {
      type: 'room_status',
      teamId,
      roomId,
      state: String(raw.state ?? '').trim().toLowerCase(),
      needScheduler: Boolean(raw.need_scheduling),
      currentTurnAgentId: Number.isFinite(currentTurnAgentId) && currentTurnAgentId > 0
        ? currentTurnAgentId
        : null,
    };
  }

  if (eventType === 'schedule_state') {
    const scheduleState = String(raw.schedule_state ?? '').trim().toLowerCase();
    if (scheduleState !== 'stopped' && scheduleState !== 'blocked' && scheduleState !== 'running') {
      return null;
    }
    return {
      type: 'schedule_state',
      scheduleState,
      notRunningReason: String(raw.not_running_reason ?? ''),
    };
  }

  if (eventType === 'room_added') {
    const gtRoom = raw.gt_room as RawRecord | undefined;
    const teamId = Number(raw.team_id ?? gtRoom?.team_id ?? 0);
    const roomId = Number(gtRoom?.id ?? 0);
    if (!Number.isFinite(teamId) || !Number.isFinite(roomId) || teamId <= 0 || roomId <= 0) {
      return null;
    }
    const roomType = String(gtRoom?.type ?? 'group').toLowerCase();
    const agentIds = Array.isArray(gtRoom?.agent_ids)
      ? (gtRoom.agent_ids as unknown[])
        .filter((id) => id !== null && id !== undefined)
        .map((id) => Number(id))
        .filter((id): id is number => Number.isFinite(id) && id !== -2)
      : [];
    const room: RoomState = {
      room_id: roomId,
      room_name: String(gtRoom?.name ?? ''),
      i18n: {},
      room_type: roomType === 'private' ? 'private' : 'group',
      state: 'idle',
      need_scheduling: false,
      agents: agentIds,
      tags: Array.isArray(gtRoom?.tags)
        ? (gtRoom.tags as unknown[]).filter((t): t is string => typeof t === 'string')
        : [],
      biz_id: typeof gtRoom?.biz_id === 'string' && (gtRoom.biz_id as string).trim()
        ? (gtRoom.biz_id as string)
        : null,
      current_turn_agent_id: null,
      preview: '',
      unread: 0,
    };
    return { type: 'room_added', teamId, room };
  }

  if (eventType === 'team_reloaded') {
    const teamId = Number(raw.team_id ?? 0);
    if (!Number.isFinite(teamId) || teamId <= 0) {
      return null;
    }
    return { type: 'team_reloaded', teamId };
  }

  if (eventType === 'task_created' || eventType === 'task_changed') {
    const task = normalizeAgentTask(raw.task);
    if (!task) {
      return null;
    }

    return {
      type: eventType,
      teamId: task.team_id,
      task,
      oldStatus: typeof raw.old_status === 'string' ? raw.old_status : undefined,
    };
  }

  return null;
}

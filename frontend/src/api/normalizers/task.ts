import type { RawAgentTask, RawAgentTaskEvent } from '../types/raw';
import type {
  AgentTask,
  AgentTaskDependencySummary,
  AgentTaskEvent,
  AgentTaskPriority,
  AgentTaskStage,
  AgentTaskStageNodeType,
  AgentTaskStageStatus,
  AgentTaskStatus,
} from '../../types';

function normalizeAgentTaskStatus(value?: unknown): AgentTaskStatus {
  const normalized = String(value ?? '').trim().toUpperCase();
  if (
    normalized === 'TODO'
    || normalized === 'PENDING'
    || normalized === 'IN_PROGRESS'
    || normalized === 'REVIEWING'
    || normalized === 'ON_HOLD'
    || normalized === 'DONE'
    || normalized === 'CANCELLED'
  ) {
    return normalized;
  }
  return 'TODO';
}

function normalizeAgentTaskPriority(value?: unknown): AgentTaskPriority {
  const normalized = String(value ?? '').trim().toUpperCase();
  if (normalized === 'HIGH' || normalized === 'LOW') {
    return normalized;
  }
  return 'NORMAL';
}

/** 事件状态字段：非枚举值一律降级为 null，避免后端新增状态导致整条事件被丢弃。 */
function normalizeEventStatus(value: unknown): AgentTaskStatus | null {
  const normalized = String(value ?? '').trim().toUpperCase();
  if (
    normalized === 'TODO'
    || normalized === 'PENDING'
    || normalized === 'IN_PROGRESS'
    || normalized === 'REVIEWING'
    || normalized === 'ON_HOLD'
    || normalized === 'DONE'
    || normalized === 'CANCELLED'
  ) {
    return normalized;
  }
  return null;
}

export function normalizeAgentTaskEvent(raw: RawAgentTaskEvent): AgentTaskEvent | null {
  const id = Number(raw.id ?? 0);
  if (!Number.isFinite(id) || id <= 0) {
    return null;
  }
  return {
    id,
    task_id: Number(raw.task_id ?? 0),
    actor_id: Number(raw.actor_id ?? 0),
    from_status: normalizeEventStatus(raw.from_status),
    to_status: normalizeEventStatus(raw.to_status),
    content: typeof raw.content === 'string' ? raw.content : '',
    action_type: typeof raw.action_type === 'string' && raw.action_type.trim()
      ? raw.action_type.trim().toUpperCase()
      : null,
    from_stage: typeof raw.from_stage === 'number' && Number.isFinite(raw.from_stage)
      ? raw.from_stage
      : null,
    to_stage: typeof raw.to_stage === 'number' && Number.isFinite(raw.to_stage)
      ? raw.to_stage
      : null,
    created_at: typeof raw.created_at === 'string' ? raw.created_at : null,
    updated_at: typeof raw.updated_at === 'string' ? raw.updated_at : null,
  };
}

export function normalizeTaskEventList(value: unknown): AgentTaskEvent[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((item) => normalizeAgentTaskEvent((item ?? {}) as RawAgentTaskEvent))
    .filter((event): event is AgentTaskEvent => event !== null)
    .sort((left, right) => left.id - right.id);
}

function normalizeDependencySummaries(value: unknown): AgentTaskDependencySummary[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => {
    const raw = (item ?? {}) as Record<string, unknown>;
    const status = String(raw.status ?? '').trim().toUpperCase();
    return {
      task_id: Number(raw.task_id ?? 0),
      title: typeof raw.title === 'string' ? raw.title : '',
      status: (status === 'UNKNOWN' ? 'UNKNOWN' : normalizeAgentTaskStatus(status)) as
        AgentTaskDependencySummary['status'],
    };
  });
}

function normalizeStageNodeType(value: unknown): AgentTaskStageNodeType {
  return String(value ?? '').trim().toUpperCase() === 'REVIEW' ? 'REVIEW' : 'EXECUTION';
}

function normalizeStageStatus(value: unknown): AgentTaskStageStatus {
  const normalized = String(value ?? '').trim().toUpperCase();
  if (normalized === 'APPROVED' || normalized === 'ACTIVE' || normalized === 'WAITING') {
    return normalized;
  }
  return 'WAITING';
}

export function normalizeTaskStages(value: unknown): AgentTaskStage[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((item, index) => {
      const raw = (item ?? {}) as Record<string, unknown>;
      const stageIndex = Number(raw.stage_index ?? index);
      const ownerId = Number(raw.owner_id ?? 0);
      const ownerName = typeof raw.owner_name === 'string' && raw.owner_name.trim()
        ? raw.owner_name.trim()
        : null;
      return {
        stage_index: Number.isFinite(stageIndex) ? stageIndex : index,
        node_type: normalizeStageNodeType(raw.node_type),
        name: typeof raw.name === 'string' ? raw.name.trim() : '',
        owner_id: Number.isFinite(ownerId) ? ownerId : 0,
        owner_name: ownerName,
        stage_status: normalizeStageStatus(raw.stage_status),
      };
    })
    .sort((left, right) => left.stage_index - right.stage_index);
}

export function normalizeAgentTask(task: RawAgentTask): AgentTask {
  const normalized: AgentTask = {
    id: Number(task.id ?? 0),
    team_id: Number(task.team_id ?? 0),
    title: String(task.title ?? ''),
    description: typeof task.description === 'string' ? task.description : '',
    assignee_id: Number(task.assignee_id ?? 0),
    creator_id: Number(task.creator_id ?? 0),
    reviewer_id: typeof task.reviewer_id === 'number' ? task.reviewer_id : null,
    status: normalizeAgentTaskStatus(task.status),
    priority: normalizeAgentTaskPriority(task.priority),
    parent_id: typeof task.parent_id === 'number' ? task.parent_id : null,
    depends_on: Array.isArray(task.depends_on)
      ? task.depends_on
        .map((item) => Number(item))
        .filter((item) => Number.isFinite(item) && item > 0)
      : [],
    room_id: typeof task.room_id === 'number' ? task.room_id : null,
    created_at: typeof task.created_at === 'string' ? task.created_at : null,
    updated_at: typeof task.updated_at === 'string' ? task.updated_at : null,
  };

  // 列表接口不带这些字段，详情接口才带——缺省时保持 undefined，不要塞空数组冒充「没有事件」。
  if (Array.isArray(task.events)) {
    normalized.events = normalizeTaskEventList(task.events);
  }
  if (typeof task.events_total === 'number') {
    normalized.events_total = task.events_total;
  }
  if (typeof task.last_note === 'string') {
    normalized.last_note = task.last_note;
  }
  if (typeof task.current_block_reason === 'string' || task.current_block_reason === null) {
    normalized.current_block_reason =
      typeof task.current_block_reason === 'string' ? task.current_block_reason : null;
  }
  if (Array.isArray(task.depends_on_details)) {
    normalized.depends_on_details = normalizeDependencySummaries(task.depends_on_details);
  }
  if (typeof task.current_stage === 'number' && Number.isFinite(task.current_stage)) {
    normalized.current_stage = task.current_stage;
  }
  if (typeof task.current_owner_id === 'number' || task.current_owner_id === null) {
    normalized.current_owner_id =
      typeof task.current_owner_id === 'number' && Number.isFinite(task.current_owner_id)
        ? task.current_owner_id
        : null;
  }
  if (typeof task.current_owner_name === 'string' || task.current_owner_name === null) {
    normalized.current_owner_name =
      typeof task.current_owner_name === 'string' ? task.current_owner_name : null;
  }
  if (Array.isArray(task.stages)) {
    normalized.stages = normalizeTaskStages(task.stages);
  }

  return normalized;
}

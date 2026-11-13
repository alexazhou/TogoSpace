import type { RawAgentActivity, RawAgentEntry, RawAgentInfo, RawAgentRuntimeInfo } from '../types/raw';
import { normalizeEntityI18n } from './common';
import { normalizeContextUsage } from './contextUsage';
import type { AgentActivity, AgentActivityStatus, AgentActivityType, AgentContextUsage, AgentDetail, AgentInfo, AgentStatus, ContainerRuntimeInfo } from '../../types';

function unwrapAgentEntry(entry: RawAgentEntry): {
  agent: RawAgentInfo;
  runtime: RawAgentRuntimeInfo | null;
} {
  if ('agent' in entry && entry.agent && typeof entry.agent === 'object') {
    return { agent: entry.agent, runtime: entry };
  }
  return { agent: entry as RawAgentInfo, runtime: null };
}

function normalizeAgentStatus(status?: string): AgentStatus {
  const normalized = status?.toLowerCase();
  if (normalized === 'active' || normalized === 'failed') {
    return normalized;
  }
  return 'idle';
}

function normalizeActivityType(value?: unknown): AgentActivityType {
  return (String(value ?? '').trim().toLowerCase() || 'unknown') as AgentActivityType;
}

function normalizeActivityStatus(value?: unknown): AgentActivityStatus {
  const normalized = String(value ?? '').trim().toLowerCase();
  if (normalized === 'started' || normalized === 'succeeded' || normalized === 'failed') {
    return normalized;
  }
  return 'cancelled';
}

function normalizeDriverTypeValue(value?: string | null): string {
  const normalized = String(value ?? '').trim().toLowerCase();
  if (normalized === 'native' || normalized === 'claude_sdk' || normalized === 'tsp') {
    return normalized;
  }
  return '';
}

/**
 * 特殊 Agent 标识。后端列表不再下发 `special` 字段，身份由固定 Agent ID 表达：
 * `OPERATOR = -1`、`SYSTEM = -2`（`SpecialAgent` 的枚举值）。
 */

function normalizeAgentSpecial(agent: RawAgentInfo): AgentInfo['special'] {
  if (agent.special === 'operator' || agent.special === 'system') {
    return agent.special;
  }
  const agentId = typeof agent.id === 'number' ? agent.id : Number(agent.id);
  if (agentId === -1) {
    return 'operator';
  }
  if (agentId === -2) {
    return 'system';
  }
  return null;
}

export function normalizeAgent(entry: RawAgentEntry): AgentInfo {
  const { agent, runtime } = unwrapAgentEntry(entry);
  const normalizedSpecial = normalizeAgentSpecial(agent);

  // 运行时字段优先取外壳，兼容仍返回扁平结构的老接口（如 agents/save.json）
  const execEnv = runtime?.exec_env_state?.exec_env ?? agent.exec_env;
  const currentContainerId = runtime?.exec_env_state?.current_container_id ?? agent.current_container_id;
  const currentContainerName = runtime?.exec_env_state?.container_name ?? agent.current_container_name;
  const containerCount = runtime?.container_count ?? agent.container_count;

  return {
    id: typeof agent.id === 'number' ? agent.id : null,
    name: String(agent.name ?? ''),
    i18n: normalizeEntityI18n(agent.i18n),
    employee_number: typeof agent.employee_number === 'number' ? agent.employee_number : null,
    role_template_id: typeof agent.role_template_id === 'number' ? agent.role_template_id : null,
    model: String(agent.model ?? ''),
    team_id: typeof agent.team_id === 'number' ? agent.team_id : null,
    status: normalizeAgentStatus(runtime?.status ?? agent.status),
    employ_status: agent.employ_status ?? null,
    driver: normalizeDriverTypeValue(typeof agent.driver === 'string' ? agent.driver : ''),
    special: normalizedSpecial,
    allow_tools: Array.isArray(agent.allow_tools) ? agent.allow_tools : null,
    allow_skills: Array.isArray(agent.allow_skills) ? agent.allow_skills : null,
    exec_env: execEnv === 'container' ? 'container' : 'host',
    current_container_id: typeof currentContainerId === 'string' && currentContainerId
      ? currentContainerId
      : null,
    current_container_name: typeof currentContainerName === 'string' && currentContainerName.trim()
      ? currentContainerName
      : null,
    container_count: typeof containerCount === 'number' ? containerCount : null,
  };
}

export function normalizeContainerRuntimeInfo(raw: unknown): ContainerRuntimeInfo | null {
  if (!raw || typeof raw !== 'object') {
    return null;
  }
  const item = raw as Record<string, unknown>;
  const recordRaw = item.record;
  if (!recordRaw || typeof recordRaw !== 'object') {
    return null;
  }
  const record = recordRaw as Record<string, unknown>;
  const recordId = Number(record.id ?? 0);
  // 预记录也必须有 record.id（它是删除入口），没有就无法操作，直接丢弃。
  if (!Number.isFinite(recordId) || recordId <= 0) {
    return null;
  }
  const containerId = typeof record.container_id === 'string' && record.container_id ? record.container_id : null;
  return {
    record_id: recordId,
    agent_id: Number(record.agent_id ?? 0),
    container_id: containerId,
    container_name: String(record.container_name ?? ''),
    image: String(record.image ?? ''),
    state: String(item.state ?? 'unknown'),
    is_entered: item.is_entered === true,
    created_at: typeof record.created_at === 'string' && record.created_at ? record.created_at : null,
  };
}

export function normalizeAgentDetail(entry: RawAgentEntry): AgentDetail {
  const { runtime } = unwrapAgentEntry(entry);
  return {
    ...normalizeAgent(entry),
    // 顶层 `context_usage`（与 `agent` / `status` 平级）是首屏快照；缺省 / 判脏为 null。
    context_usage: normalizeContextUsage(runtime?.context_usage),
  };
}

/**
 * 上下文用量取值收敛：任一必需字段不是有限数就整体判脏返回 null。
 *
 * 为什么「一个字段脏就整体丢」而不是逐字段补默认值：这条链路的数值全部参与
 * 「距压缩线还有多远」的判断，补 0 会让面板显示一个看起来正常、实际错误的
 * 百分比。丢整帧的后果只是「这一帧没更新」，下一帧会补上。
 *
 * `ratio` 允许任意有限数（含 >1，见 `AgentContextUsage` 注释），
 * 负数则视为脏——占比不可能是负的。
 *
 * 注意：REST（详情接口）与 WS（`agent_context_usage` 广播）共用这一份口径，
 * 所以放在 `api.ts` 供 `eventNormalizer` 复用，避免两处各写一遍后分叉。
 */

export function normalizeAgentActivity(activity: RawAgentActivity): AgentActivity {
  return {
    id: Number(activity.id ?? 0),
    agent_id: Number(activity.agent_id ?? 0),
    team_id: Number(activity.team_id ?? 0),
    activity_type: normalizeActivityType(activity.activity_type),
    status: normalizeActivityStatus(activity.status),
    title: String(activity.title ?? ''),
    detail: typeof activity.detail === 'string' ? activity.detail : '',
    error_message: typeof activity.error_message === 'string' ? activity.error_message : null,
    started_at: typeof activity.started_at === 'string' ? activity.started_at : null,
    finished_at: typeof activity.finished_at === 'string' ? activity.finished_at : null,
    duration_ms: typeof activity.duration_ms === 'number' ? activity.duration_ms : null,
    metadata: typeof activity.metadata === 'object' && activity.metadata !== null
      ? activity.metadata as Record<string, unknown>
      : {},
    created_at: typeof activity.created_at === 'string' ? activity.created_at : null,
    updated_at: typeof activity.updated_at === 'string' ? activity.updated_at : null,
  };
}

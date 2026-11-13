import type { RawAgentActivity, RawAgentEntry, RawAgentTask } from './types/raw';
import { requestJson, withSearch } from './client';
import { normalizeAgent, normalizeAgentActivity, normalizeAgentDetail, normalizeContainerRuntimeInfo } from './normalizers/agent';
import { normalizeAgentMemory } from './normalizers/agentMemory';
import { normalizeAgentTask } from './normalizers/task';
import type { AgentActivity, AgentDetail, AgentInfo, AgentMemory, AgentTask, ContainerRuntimeInfo } from '../types';

export async function getAgents(): Promise<AgentInfo[]> {
  const data = await requestJson<{ agents: RawAgentEntry[] }>('/agents/list.json');
  return data.agents.map(normalizeAgent);
}

export async function getAgentsByTeamId(teamId: number, options?: { includeSpecial?: boolean }): Promise<AgentInfo[]> {
  const data = await requestJson<{ agents: RawAgentEntry[] }>(
    withSearch('/agents/list.json', {
      team_id: teamId,
      include_special: options?.includeSpecial ? 1 : undefined,
    }),
  );
  return data.agents.map(normalizeAgent);
}

export async function clearAgentData(agentId: number): Promise<{
  status: string;
  agent_id: number;
  deleted: { histories: number };
}> {
  return requestJson(`/agents/${agentId}/clear_data.json`, {
    method: 'POST',
  });
}

async function getAgentContainers(agentId: number): Promise<ContainerRuntimeInfo[]> {
  const data = await requestJson<unknown>(`/agents/${agentId}/containers.json`);
  if (!Array.isArray(data)) {
    return [];
  }
  return data.map(normalizeContainerRuntimeInfo).filter((item): item is ContainerRuntimeInfo => item !== null);
}

export async function getAgentMemories(agentId: number, includeArchived = true): Promise<AgentMemory[]> {
  const data = await requestJson<{ memories?: unknown }>(
    withSearch(`/agents/${agentId}/memories.json`, { include_archived: includeArchived ? 1 : 0 }),
  );
  const raw = Array.isArray(data.memories) ? data.memories : [];
  return raw
    .filter((item): item is Record<string, unknown> => typeof item === 'object' && item !== null)
    .map(normalizeAgentMemory);
}

async function getAgentErrorMessage(agentId: number): Promise<string | null> {
  const data = await requestJson<{ error_message?: unknown }>(`/agents/${agentId}/error_message.json`);
  return typeof data.error_message === 'string' ? data.error_message : null;
}

export async function getAgentDetail(agentId: number): Promise<AgentDetail> {
  const [data, containers, errorMessage] = await Promise.all([
    requestJson<RawAgentEntry>(`/agents/${agentId}.json`),
    getAgentContainers(agentId),
    getAgentErrorMessage(agentId),
  ]);
  return {
    ...normalizeAgentDetail(data),
    containers,
    error_message: errorMessage,
  };
}

export function getAgentControlRoom(agentId: number): Promise<{ room_id: number; created: boolean }> {
  return requestJson<{ room_id: number; created: boolean }>(`/agents/${agentId}/control_room.json`);
}

export type GetAgentActivitiesOptions = {
  limit?: number;
  beforeId?: number | null;
};

export type AgentActivitiesPage = {
  activities: AgentActivity[];
  hasMore: boolean;
};

export async function getAgentActivitiesPage(
  agentId: number,
  options?: GetAgentActivitiesOptions,
): Promise<AgentActivitiesPage> {
  const data = await requestJson<{
    activities: RawAgentActivity[];
    pagination?: {
      has_more?: boolean;
    };
  }>(
    withSearch(`/agents/${agentId}/activities.json`, {
      exclude: 'AGENT_STATE',
      limit: options?.limit,
      before_id: options?.beforeId,
    }),
  );
  return {
    activities: (data.activities ?? []).map(normalizeAgentActivity),
    hasMore: Boolean(data.pagination?.has_more),
  };
}

export async function getAgentActivities(agentId: number): Promise<AgentActivity[]> {
  const page = await getAgentActivitiesPage(agentId);
  return page.activities;
}

export async function getAgentTasks(agentId: number, includeClosed = false): Promise<AgentTask[]> {
  const data = await requestJson<{ tasks: RawAgentTask[] }>(
    withSearch(`/agents/${agentId}/tasks.json`, { include_closed: includeClosed ? 1 : 0, limit: 30 }),
  );
  return (data.tasks ?? []).map(normalizeAgentTask);
}

export async function resumeAgent(agentId: number): Promise<{ status: string; agent_id: number; room_id: number }> {
  return requestJson(`/agents/${agentId}/resume.json`, {
    method: 'POST',
  });
}

export async function stopAgent(agentId: number): Promise<{ status: string; agent_id: number }> {
  return requestJson(`/agents/${agentId}/stop.json`, {
    method: 'POST',
  });
}

/**
 * 手动触发上下文压缩（Compact）。
 *
 * 响应里的 `context_usage` **不用来写本地缓存**：压缩后的新数值由后端
 * `AGENT_CONTEXT_USAGE_CHANGED` 广播推进。两处都写会让「推送先到、响应后到」
 * 的时序把新值覆盖成旧值。这里只等它成功（或抛出）好收 loading。
 *
 * 失败时 `requestJson` 已经弹出后端的 `error_desc`（如「当前无足够可压缩的
 * 历史消息」），调用方不需要再翻译一遍。
 */

export async function compactAgentContext(agentId: number): Promise<{ success: boolean; agent_id: number }> {
  return requestJson(`/agents/${agentId}/compact.json`, {
    method: 'POST',
  });
}

export async function updateAgentProperties(
  agentId: number,
  payload: { model?: string; allow_tools?: string[] | null; allow_skills?: string[] | null }
): Promise<AgentDetail> {
  const data = await requestJson<RawAgentEntry>(`/agents/${agentId}/modify_properties.json`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
  // 保存只改配置，容器与失败信息不由本接口负责；调用方保留已有详情即可
  return normalizeAgentDetail(data);
}

export type SuperviseResponse = {
  room_id: number;
  created: boolean;
};

export async function superviseAgent(
  agentId: number,
  content: string,
  insertImmediately = true,
): Promise<SuperviseResponse> {
  return requestJson(`/agents/${agentId}/supervise.json`, {
    method: 'POST',
    body: JSON.stringify({ content, insert_immediately: insertImmediately }),
  });
}

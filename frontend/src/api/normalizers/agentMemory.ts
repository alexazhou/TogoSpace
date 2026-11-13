import type { AgentMemory, AgentMemoryStarLevel, AgentMemoryStatus } from '../../types';

/** 记忆星标级别归一化：后端给的是小写 value；也容忍大写 name（DB 口径）与异常值。 */
function normalizeMemoryStarLevel(value?: unknown): AgentMemoryStarLevel {
  const normalized = String(value ?? '').trim().toLowerCase();
  if (normalized === 'summary' || normalized === 'title') {
    return normalized;
  }
  return 'none';
}

function normalizeMemoryStatus(value?: unknown): AgentMemoryStatus {
  return String(value ?? '').trim().toLowerCase() === 'archived' ? 'archived' : 'active';
}

export function normalizeAgentMemory(raw: Record<string, unknown>): AgentMemory {
  return {
    id: Number(raw.id ?? 0),
    team_id: Number(raw.team_id ?? 0),
    agent_id: Number(raw.agent_id ?? 0),
    title: String(raw.title ?? ''),
    summary: String(raw.summary ?? ''),
    content: String(raw.content ?? ''),
    star_level: normalizeMemoryStarLevel(raw.star_level),
    status: normalizeMemoryStatus(raw.status),
    tags: Array.isArray(raw.tags) ? raw.tags.map((tag) => String(tag)) : [],
    created_at: typeof raw.created_at === 'string' ? raw.created_at : null,
    updated_at: typeof raw.updated_at === 'string' ? raw.updated_at : null,
  };
}

/**
 * 拉取某个 Agent 的长期记忆（只读）。
 *
 * `includeArchived` 默认 `true`：面板要同时支持「活跃 / 已归档 / 全部」三个筛选项，
 * 拉全量一次在前端筛，比每次切 tab 再请求一轮更省事、也不会出现切换时空窗。
 */

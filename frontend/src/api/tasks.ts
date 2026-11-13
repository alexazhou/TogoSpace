import type { RawAgentTask, RawAgentTaskEvent } from './types/raw';
import { requestJson, withSearch } from './client';
import { normalizeAgentTask, normalizeTaskEventList } from './normalizers/task';
import type { AgentTask, AgentTaskEvent } from '../types';

export async function getTeamTasks(teamId: number, includeClosed = true, limit = 500): Promise<AgentTask[]> {
  const data = await requestJson<{ tasks: RawAgentTask[] }>(
    withSearch(`/teams/${teamId}/tasks.json`, { include_closed: includeClosed ? 1 : 0, limit }),
  );
  return (data.tasks ?? []).map(normalizeAgentTask);
}

/** 获取单个任务详情（含依赖摘要、时间线首屏与派生字段）。 */
export async function getTaskDetail(taskId: number): Promise<AgentTask> {
  const data = await requestJson<{ task: RawAgentTask }>(`/tasks/${taskId}.json`);
  return normalizeAgentTask(data.task ?? {});
}

export interface AgentTaskEventsPage {
  /** 本页事件，按 id **升序**（最新在末尾），便于直接渲染时间线。 */
  events: AgentTaskEvent[];
  /** 该任务事件**总数**（跨页），用于判断是否还有更早事件。 */
  total: number;
}

/** 按游标分页读取任务时间线；`beforeId` 省略时取最新一页。 */
export async function getTaskEventsPage(
  taskId: number,
  options?: { limit?: number; beforeId?: number },
): Promise<AgentTaskEventsPage> {
  const data = await requestJson<{ events?: RawAgentTaskEvent[]; total?: number }>(
    withSearch(`/tasks/${taskId}/events.json`, {
      limit: options?.limit,
      before_id: options?.beforeId,
    }),
  );
  return {
    events: normalizeTaskEventList(data.events),
    total: typeof data.total === 'number' ? data.total : 0,
  };
}

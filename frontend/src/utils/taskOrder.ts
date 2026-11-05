import type { AgentTask } from '../types';

/**
 * 任务时间戳 → 可比较数值。
 *
 * 后端返回形如 `2026-11-05 03:05:01.276154`（空格分隔、微秒 6 位），不是标准
 * ISO 串；把空格换成 `T` 后再交给 `Date.parse`，解析不出来时按 0 处理
 * （缺失时间戳的任务排在最后，而不是让比较函数返回 NaN 导致顺序随机）。
 */
function taskTimeValue(value: string | null | undefined): number {
  if (!value) {
    return 0;
  }
  const parsed = Date.parse(value.replace(' ', 'T'));
  return Number.isFinite(parsed) ? parsed : 0;
}

/**
 * 「最近修改在前」排序：`updated_at` 倒序，同一时间戳用 id 倒序兜底。
 *
 * 排序口径的权威在后端（`order=updated_desc` 决定取哪些数据），这里是前端
 * 的显示保障：任务列表是本地数组，实时推送只做原地合并、不重取接口，
 * 不在这里重排的话，刚被改过的任务会留在原位。
 */
export function sortTasksByLatestUpdate(tasks: AgentTask[]): AgentTask[] {
  return [...tasks].sort((left, right) => {
    const diff = taskTimeValue(right.updated_at) - taskTimeValue(left.updated_at);
    return diff !== 0 ? diff : right.id - left.id;
  });
}

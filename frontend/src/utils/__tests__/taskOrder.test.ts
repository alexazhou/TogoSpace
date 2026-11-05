import { describe, expect, it } from 'vitest';
import { sortTasksByLatestUpdate } from '../taskOrder';
import type { AgentTask } from '../../types';

function createTask(overrides: Partial<AgentTask> = {}): AgentTask {
  return {
    id: 1,
    team_id: 1,
    title: 'task',
    description: '',
    assignee_id: 7,
    creator_id: 7,
    reviewer_id: null,
    status: 'TODO',
    priority: 'NORMAL',
    parent_id: null,
    depends_on: [],
    room_id: null,
    created_at: '2026-11-01 00:00:00.000000',
    updated_at: '2026-11-01 00:00:00.000000',
    ...overrides,
  };
}

describe('sortTasksByLatestUpdate', () => {
  it('按 updated_at 倒序：最近修改的排最前', () => {
    const tasks = [
      createTask({ id: 1, updated_at: '2026-11-01 10:00:00.000000' }),
      createTask({ id: 2, updated_at: '2026-11-03 09:00:00.000000' }),
      createTask({ id: 3, updated_at: '2026-11-02 23:59:59.999999' }),
    ];

    expect(sortTasksByLatestUpdate(tasks).map((task) => task.id)).toEqual([2, 3, 1]);
  });

  it('同一时间戳用 id 倒序兜底，顺序稳定', () => {
    const same = '2026-11-01 10:00:00.000000';
    const tasks = [
      createTask({ id: 5, updated_at: same }),
      createTask({ id: 9, updated_at: same }),
      createTask({ id: 7, updated_at: same }),
    ];

    expect(sortTasksByLatestUpdate(tasks).map((task) => task.id)).toEqual([9, 7, 5]);
  });

  it('缺少 updated_at 的任务排最后，不影响其他项', () => {
    const tasks = [
      createTask({ id: 1, updated_at: null }),
      createTask({ id: 2, updated_at: '2026-11-02 00:00:00.000000' }),
    ];

    expect(sortTasksByLatestUpdate(tasks).map((task) => task.id)).toEqual([2, 1]);
  });

  it('不修改入参数组（返回新数组）', () => {
    const tasks = [
      createTask({ id: 1, updated_at: '2026-11-01 00:00:00.000000' }),
      createTask({ id: 2, updated_at: '2026-11-02 00:00:00.000000' }),
    ];

    sortTasksByLatestUpdate(tasks);

    expect(tasks.map((task) => task.id)).toEqual([1, 2]);
  });
});

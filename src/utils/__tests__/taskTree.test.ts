import { describe, expect, it } from 'vitest';
import type { AgentTask } from '../../types';
import { buildTaskForest, filterTaskForest } from '../taskTree';

function buildTask(overrides: Partial<AgentTask> & Pick<AgentTask, 'id' | 'title'>): AgentTask {
  return {
    id: overrides.id,
    team_id: overrides.team_id ?? 1,
    title: overrides.title,
    description: overrides.description ?? '',
    assignee_id: overrides.assignee_id ?? 1,
    creator_id: overrides.creator_id ?? 1,
    manager_id: overrides.manager_id ?? null,
    status: overrides.status ?? 'TODO',
    priority: overrides.priority ?? 'NORMAL',
    parent_id: overrides.parent_id ?? null,
    depends_on: overrides.depends_on ?? [],
    room_id: overrides.room_id ?? null,
    result: overrides.result ?? '',
    block_reason: overrides.block_reason ?? '',
    created_at: overrides.created_at ?? null,
    updated_at: overrides.updated_at ?? null,
  };
}

describe('buildTaskForest', () => {
  it('builds parent-child trees and keeps orphan tasks as roots', () => {
    const forest = buildTaskForest([
      buildTask({ id: 3, title: 'Child A-1', parent_id: 1 }),
      buildTask({ id: 1, title: 'Root A' }),
      buildTask({ id: 5, title: 'Orphan child', parent_id: 99 }),
      buildTask({ id: 2, title: 'Root B' }),
      buildTask({ id: 4, title: 'Child A-2', parent_id: 1 }),
    ]);

    expect(forest.map((node) => node.task.id)).toEqual([5, 2, 1]);
    expect(forest[2]?.children.map((node) => node.task.id)).toEqual([4, 3]);
    expect(forest[2]?.depth).toBe(0);
    expect(forest[2]?.children[0]?.depth).toBe(1);
  });

  it('breaks self-parent cycles by promoting the task to a root', () => {
    const forest = buildTaskForest([
      buildTask({ id: 7, title: 'Loop', parent_id: 7 }),
    ]);

    expect(forest).toHaveLength(1);
    expect(forest[0]?.task.id).toBe(7);
    expect(forest[0]?.children).toEqual([]);
  });

  it('keeps the full subtree when a parent matches the filter', () => {
    const forest = buildTaskForest([
      buildTask({ id: 14, title: 'Parent', status: 'IN_PROGRESS' }),
      buildTask({ id: 15, title: 'Child A', parent_id: 14, status: 'DONE' }),
      buildTask({ id: 16, title: 'Child B', parent_id: 14, status: 'CANCELLED' }),
    ]);

    const filtered = filterTaskForest(forest, (task) => task.status === 'IN_PROGRESS');

    expect(filtered).toHaveLength(1);
    expect(filtered[0]?.task.id).toBe(14);
    expect(filtered[0]?.children.map((node) => node.task.id)).toEqual([16, 15]);
  });

  it('keeps ancestor context when only descendants match the filter', () => {
    const forest = buildTaskForest([
      buildTask({ id: 14, title: 'Parent', status: 'DONE' }),
      buildTask({ id: 15, title: 'Child A', parent_id: 14, status: 'DONE' }),
      buildTask({ id: 16, title: 'Child B', parent_id: 14, status: 'IN_PROGRESS' }),
    ]);

    const filtered = filterTaskForest(forest, (task) => task.status === 'IN_PROGRESS');

    expect(filtered).toHaveLength(1);
    expect(filtered[0]?.task.id).toBe(14);
    expect(filtered[0]?.children).toHaveLength(1);
    expect(filtered[0]?.children[0]?.task.id).toBe(16);
  });
});

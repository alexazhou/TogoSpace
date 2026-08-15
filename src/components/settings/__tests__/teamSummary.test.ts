import { describe, expect, it } from 'vitest';
import { countDeptHierarchyLevels, countDeptNodes } from '../teamSummary';
import type { DeptTreeNode } from '../../../types';

function createDeptNode(
  name: string,
  children: DeptTreeNode[] = [],
): DeptTreeNode {
  return {
    id: null,
    name,
    responsibility: '',
    manager_id: null,
    agent_ids: [],
    children,
  };
}

describe('teamSummary', () => {
  it('counts leaf departments', () => {
    const tree = createDeptNode('root', [
      createDeptNode('child-a'),
      createDeptNode('child-b'),
    ]);

    expect(countDeptNodes(tree)).toBe(3);
  });

  it('counts nested hierarchy levels', () => {
    const tree = createDeptNode('root', [
      createDeptNode('child', [
        createDeptNode('grand-child'),
      ]),
    ]);

    expect(countDeptHierarchyLevels(tree)).toBe(3);
  });

  it('returns zero for empty tree', () => {
    expect(countDeptNodes(null)).toBe(0);
    expect(countDeptHierarchyLevels(null)).toBe(0);
  });
});

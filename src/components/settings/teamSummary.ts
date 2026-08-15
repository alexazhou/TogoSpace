import type { DeptTreeNode } from '../../types';

export function countDeptNodes(node: DeptTreeNode | null): number {
  if (!node) {
    return 0;
  }

  return 1 + node.children.reduce((total, child) => total + countDeptNodes(child), 0);
}

export function countDeptHierarchyLevels(node: DeptTreeNode | null): number {
  if (!node) {
    return 0;
  }

  if (!node.children.length) {
    return 1;
  }

  return 1 + Math.max(...node.children.map((child) => countDeptHierarchyLevels(child)));
}

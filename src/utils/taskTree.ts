import type { AgentTask } from '../types';

export interface TaskTreeNode {
  task: AgentTask;
  children: TaskTreeNode[];
  depth: number;
}

function cloneTaskNode(node: TaskTreeNode): TaskTreeNode {
  return {
    task: node.task,
    depth: node.depth,
    children: node.children.map(cloneTaskNode),
  };
}

function sortTasksById(tasks: AgentTask[]): AgentTask[] {
  return [...tasks].sort((left, right) => right.id - left.id);
}

export function buildTaskForest(tasks: AgentTask[]): TaskTreeNode[] {
  const sortedTasks = sortTasksById(tasks);
  const nodeMap = new Map<number, TaskTreeNode>(
    sortedTasks.map((task) => [task.id, { task, children: [], depth: 0 }]),
  );
  const roots: TaskTreeNode[] = [];

  for (const task of sortedTasks) {
    const node = nodeMap.get(task.id);
    if (!node) {
      continue;
    }

    const parent = task.parent_id !== null ? nodeMap.get(task.parent_id) : null;
    if (!parent || parent === node) {
      roots.push(node);
      continue;
    }

    parent.children.push(node);
  }

  const visited = new Set<number>();

  function assignDepth(node: TaskTreeNode, depth: number, path: Set<number>): void {
    if (visited.has(node.task.id)) {
      return;
    }

    visited.add(node.task.id);
    node.depth = depth;

    const nextPath = new Set(path);
    nextPath.add(node.task.id);
    node.children = node.children.filter((child) => !nextPath.has(child.task.id));
    for (const child of node.children) {
      assignDepth(child, depth + 1, nextPath);
    }
  }

  for (const root of roots) {
    assignDepth(root, 0, new Set());
  }

  for (const task of sortedTasks) {
    if (visited.has(task.id)) {
      continue;
    }

    const node = nodeMap.get(task.id);
    if (!node) {
      continue;
    }

    roots.push(node);
    assignDepth(node, 0, new Set());
  }

  return roots;
}

export function filterTaskForest(
  forest: TaskTreeNode[],
  predicate: (task: AgentTask) => boolean,
): TaskTreeNode[] {
  function filterNode(node: TaskTreeNode): TaskTreeNode | null {
    if (predicate(node.task)) {
      return cloneTaskNode(node);
    }

    const filteredChildren = node.children
      .map(filterNode)
      .filter((child): child is TaskTreeNode => child !== null);

    if (!filteredChildren.length) {
      return null;
    }

    return {
      task: node.task,
      depth: node.depth,
      children: filteredChildren,
    };
  }

  return forest
    .map(filterNode)
    .filter((node): node is TaskTreeNode => node !== null);
}

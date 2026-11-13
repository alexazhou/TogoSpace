import type { AgentInfo, DeptTreeNode, FrontendConfig } from '../types';
import { MODEL_SLOT_LABELS } from '../types';
import type { MemberDriverOption, MemberModelOption } from '../composables/useMemberEditorDialog';

/**
 * 组织架构树（`DraftOrgNode`）的**纯模型层**：构造 / 遍历 / 前后端映射 / 差异基线。
 *
 * 从 `TeamTreeEditor.vue` 抽出来的第一批（#213 P3）：这些函数原本夹在 1196 行的 SFC
 * `<script setup>` 里，没有状态、只吃参数，抽出来后可以直接单测，也让组件只剩状态与交互。
 *
 * 约定：本文件**不依赖 Vue 响应式、不读组件状态、不调 i18n**。原本读 `t()` 的两处
 * （部门名自动编号前缀、驱动下拉的「自动」项）改成显式传参，避免把 i18n 依赖带进来。
 */

export type DraftOrgNode = {
  id: string;
  kind: 'member' | 'pending';
  agentId: number | null;
  deptId: number | null;
  hasAssignedDepartment: boolean;
  memberName: string;
  roleTemplateId: number | null;
  model: string;
  driver: string;
  employeeNumber: string;
  deptName: string;
  deptResponsibility: string;
  children: DraftOrgNode[];
};

export function createDraftNodeId(prefix = 'node'): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export function cloneDraftOrgNode(node: DraftOrgNode | null): DraftOrgNode | null {
  if (!node) {
    return null;
  }

  return {
    ...node,
    children: node.children.map((child) => cloneDraftOrgNode(child)!),
  };
}

export function createPendingNode(hasAssignedDepartment = false): DraftOrgNode {
  return {
    id: createDraftNodeId('pending'),
    kind: 'pending',
    agentId: null,
    deptId: null,
    hasAssignedDepartment,
    memberName: '',
    roleTemplateId: null,
    model: '',
    driver: '',
    employeeNumber: '',
    deptName: '',
    deptResponsibility: '',
    children: [],
  };
}


export function createDepartmentNameAllocator(
  newDeptPrefix: string,
  initialDepartmentNames: string[] = [],
): () => string {
  const usedDepartmentNames = new Set<string>();
  let maxDepartmentIndex = 0;

  initialDepartmentNames.forEach((departmentName) => {
    const trimmedDepartmentName = departmentName.trim();
    if (!trimmedDepartmentName) {
      return;
    }

    usedDepartmentNames.add(trimmedDepartmentName);
    const matched = trimmedDepartmentName.match(new RegExp(`^${newDeptPrefix}(\\d+)$`));
    if (!matched) {
      return;
    }

    maxDepartmentIndex = Math.max(maxDepartmentIndex, Number(matched[1]));
  });

  return () => {
    let nextDepartmentIndex = maxDepartmentIndex + 1;
    while (usedDepartmentNames.has(`${newDeptPrefix}${nextDepartmentIndex}`)) {
      nextDepartmentIndex += 1;
    }

    const nextDepartmentName = `${newDeptPrefix}${nextDepartmentIndex}`;
    usedDepartmentNames.add(nextDepartmentName);
    maxDepartmentIndex = nextDepartmentIndex;
    return nextDepartmentName;
  };
}

export function parseDriverTypeValue(driver: string): string {
  const normalized = driver.trim().toUpperCase();
  if (normalized === 'NATIVE' || normalized === 'CLAUDE_SDK' || normalized === 'TSP') {
    return normalized;
  }
  return '';
}

export function isOnBoardAgent(agent: AgentInfo): boolean {
  return agent.employ_status === 'ON_BOARD' || !agent.employ_status;
}

export function buildModelCatalog(config: FrontendConfig | null): MemberModelOption[] {
  const slotOptions: MemberModelOption[] = (config?.model_slots ?? []).map((slot) => ({
    value: `${slot.key}@system`,
    label: MODEL_SLOT_LABELS[slot.key] ?? slot.key,
  }));

  return [
    { value: '', label: '自动' },
    ...slotOptions,
  ];
}

export function buildDriverCatalog(
  config: FrontendConfig | null,
  autoLabel: string,
): MemberDriverOption[] {
  return [
    { value: '', label: autoLabel },
    ...((config?.driver_types ?? []).map((item) => ({
      value: item.name,
      label: item.description ? `${item.name} · ${item.description}` : item.name,
    }))),
  ];
}

export function createDraftNodeIdFromAgentId(agentId: number): string {
  return `agent-${agentId}`;
}

export function createMemberNode(
  memberName: string,
  agent: AgentInfo | undefined,
  options?: {
    deptId?: number | null;
    hasAssignedDepartment?: boolean;
    deptName?: string;
    deptResponsibility?: string;
    children?: DraftOrgNode[];
  },
): DraftOrgNode {
  return {
    id: typeof agent?.id === 'number' ? createDraftNodeIdFromAgentId(agent.id) : createDraftNodeId('member'),
    kind: 'member',
    agentId: typeof agent?.id === 'number' ? agent.id : null,
    deptId: typeof options?.deptId === 'number' ? options.deptId : null,
    hasAssignedDepartment: options?.hasAssignedDepartment ?? false,
    memberName,
    roleTemplateId: agent?.role_template_id ?? null,
    model: agent?.model || '',
    driver: parseDriverTypeValue(agent?.driver || ''),
    employeeNumber: typeof agent?.employee_number === 'number' ? String(agent.employee_number) : '',
    deptName: options?.deptName?.trim() || '',
    deptResponsibility: options?.deptResponsibility || '',
    children: options?.children ?? [],
  };
}

export function buildFallbackOrgTree(agents: AgentInfo[]): DraftOrgNode | null {
  const leader = agents[0];
  if (!leader) {
    return null;
  }

  return createMemberNode(leader.name, leader, {
    hasAssignedDepartment: false,
    children: agents.slice(1).map((agent) => createMemberNode(agent.name, agent)),
  });
}

export function buildDraftOrgNodeFromDeptTree(
  node: DeptTreeNode,
  agentsById: Map<number, AgentInfo>,
  visitedIds: Set<number>,
): DraftOrgNode | null {
  const managerId = node.manager_id;
  if (managerId === null || !agentsById.has(managerId)) {
    return null;
  }

  visitedIds.add(managerId);
  const childManagerIds = new Set(
    node.children
      .map((child) => child.manager_id)
      .filter((id): id is number => id !== null),
  );

  const extraMemberNodes = node.agent_ids
    .filter((agentId) => agentId !== managerId && !childManagerIds.has(agentId) && agentsById.has(agentId))
    .map((agentId) => {
      visitedIds.add(agentId);
      return createMemberNode(
        agentsById.get(agentId)!.name,
        agentsById.get(agentId),
        {
          hasAssignedDepartment: true,
        },
      );
    });

  const childNodes = node.children
    .map((child) => buildDraftOrgNodeFromDeptTree(child, agentsById, visitedIds))
    .filter((child): child is DraftOrgNode => child !== null);

  const managerAgent = agentsById.get(managerId);
  return createMemberNode(
    managerAgent!.name,
    managerAgent,
    {
      deptId: node.id ?? null,
      hasAssignedDepartment: true,
      deptName: node.name,
      deptResponsibility: node.responsibility,
      children: [...extraMemberNodes, ...childNodes],
    },
  );
}

export function buildDraftOrgTree(tree: DeptTreeNode | null, agents: AgentInfo[]): DraftOrgNode | null {
  const agentsById = new Map<number, AgentInfo>();
  agents.forEach((agent) => {
    if (typeof agent.id === 'number') {
      agentsById.set(agent.id, agent);
    }
  });

  if (!tree) {
    return buildFallbackOrgTree(agents);
  }

  const visitedIds = new Set<number>();
  const root = buildDraftOrgNodeFromDeptTree(tree, agentsById, visitedIds);
  if (!root) {
    return buildFallbackOrgTree(agents);
  }

  const extraAgents = agents.filter((agent) => typeof agent.id === 'number' && !visitedIds.has(agent.id));
  root.children.push(...extraAgents.map((agent) => createMemberNode(agent.name, agent, {
    hasAssignedDepartment: false,
  })));
  return root;
}

export function collectMemberNodes(root: DraftOrgNode | null): DraftOrgNode[] {
  if (!root) {
    return [];
  }

  const result: DraftOrgNode[] = [];
  const stack = [root];
  while (stack.length) {
    const current = stack.pop()!;
    if (current.kind === 'member' && current.memberName) {
      result.push(current);
    }
    for (let index = current.children.length - 1; index >= 0; index -= 1) {
      stack.push(current.children[index]);
    }
  }

  return result;
}

export function findNodeById(root: DraftOrgNode | null, nodeId: string): DraftOrgNode | null {
  if (!root) {
    return null;
  }

  const stack = [root];
  while (stack.length) {
    const current = stack.pop()!;
    if (current.id === nodeId) {
      return current;
    }
    for (let index = current.children.length - 1; index >= 0; index -= 1) {
      stack.push(current.children[index]);
    }
  }

  return null;
}

export function countManagedChildren(node: DraftOrgNode): number {
  return node.children.filter((child) => child.kind === 'member').length;
}

export function buildMembersSavePayload(root: DraftOrgNode | null): Array<{
  id: number | null;
  name: string;
  role_template_id: number;
  model: string;
  driver: string;
}> {
  return collectMemberNodes(root)
    .map((node) => ({
      id: node.agentId,
      name: node.memberName,
      role_template_id: node.roleTemplateId ?? 0,
      model: node.model,
      driver: node.driver || 'native',
    }))
    .sort((left, right) => {
      const leftKey = left.id ?? Number.MAX_SAFE_INTEGER;
      const rightKey = right.id ?? Number.MAX_SAFE_INTEGER;
      if (leftKey !== rightKey) {
        return leftKey - rightKey;
      }
      return left.name.localeCompare(right.name);
    });
}

export function normalizeSavedAgentForDraft(agent: AgentInfo): AgentInfo {
  return {
    ...agent,
    model: agent.model || '',
    driver: parseDriverTypeValue(agent.driver || ''),
  };
}

export function hydrateDraftTreeAgentIds(root: DraftOrgNode | null, savedAgents: AgentInfo[]): DraftOrgNode | null {
  const nextRoot = cloneDraftOrgNode(root);
  if (!nextRoot) {
    return null;
  }

  const savedById = new Map<number, AgentInfo>();
  const savedByName = new Map<string, AgentInfo[]>();
  savedAgents.forEach((agent) => {
    if (typeof agent.id !== 'number') {
      return;
    }

    const normalizedAgent = normalizeSavedAgentForDraft(agent);
    savedById.set(agent.id, normalizedAgent);
    const nameAgents = savedByName.get(agent.name) ?? [];
    nameAgents.push(normalizedAgent);
    savedByName.set(agent.name, nameAgents);
  });

  const existingAgentIds = new Set(
    collectMemberNodes(root)
      .map((node) => node.agentId)
      .filter((agentId): agentId is number => agentId !== null),
  );
  const consumedAgentIds = new Set<number>();

  collectMemberNodes(nextRoot).forEach((node) => {
    let matchedAgent: AgentInfo | undefined;
    if (node.agentId !== null) {
      matchedAgent = savedById.get(node.agentId);
    } else {
      matchedAgent = (savedByName.get(node.memberName) ?? []).find((agent) => (
        typeof agent.id === 'number'
        && !existingAgentIds.has(agent.id)
        && !consumedAgentIds.has(agent.id)
      ));
    }

    if (!matchedAgent || typeof matchedAgent.id !== 'number') {
      return;
    }

    consumedAgentIds.add(matchedAgent.id);
    node.id = createDraftNodeIdFromAgentId(matchedAgent.id);
    node.agentId = matchedAgent.id;
    node.memberName = matchedAgent.name;
    node.roleTemplateId = matchedAgent.role_template_id ?? node.roleTemplateId;
    node.model = matchedAgent.model || node.model;
    node.driver = parseDriverTypeValue(matchedAgent.driver || '') || node.driver;
    node.employeeNumber = typeof matchedAgent.employee_number === 'number'
      ? String(matchedAgent.employee_number)
      : node.employeeNumber;
  });

  return nextRoot;
}

export function assertDraftTreeAgentIdsReady(root: DraftOrgNode | null): void {
  const missingAgentNode = collectMemberNodes(root).find((node) => node.agentId === null);
  if (missingAgentNode) {
    throw new Error(`Missing agent id for member: ${missingAgentNode.memberName}`);
  }
}

export function buildCommittedMembersSaveBaseline(agents: AgentInfo[]): Array<{
  id: number | null;
  name: string;
  role_template_id: number;
  model: string;
  driver: string;
}> {
  return agents
    .map((agent) => ({
      id: typeof agent.id === 'number' ? agent.id : null,
      name: agent.name,
      role_template_id: agent.role_template_id ?? 0,
      model: agent.model || '',
      driver: parseDriverTypeValue(agent.driver || '') || 'native',
    }))
    .sort((left, right) => {
      const leftKey = left.id ?? Number.MAX_SAFE_INTEGER;
      const rightKey = right.id ?? Number.MAX_SAFE_INTEGER;
      if (leftKey !== rightKey) {
        return leftKey - rightKey;
      }
      return left.name.localeCompare(right.name);
    });
}

export function buildDeptTreePayload(root: DraftOrgNode | null): DeptTreeNode | null {
  if (!root || root.kind !== 'member' || !root.memberName || root.agentId === null) {
    return null;
  }

  const buildNode = (node: DraftOrgNode, isRoot = false): DeptTreeNode => {
    const childMembers = node.children.filter((child) => child.kind === 'member');
    const agentIds: number[] = node.agentId !== null ? [node.agentId] : [];
    const children: DeptTreeNode[] = [];

    childMembers.forEach((child) => {
      if (child.agentId !== null) {
        agentIds.push(child.agentId);
      }

      if (countManagedChildren(child) > 0) {
        children.push(buildNode(child));
        return;
      }
    });

    return {
      id: isRoot || childMembers.length > 0 ? node.deptId : null,
      name: isRoot || childMembers.length > 0 ? node.deptName : '',
      responsibility: isRoot || childMembers.length > 0 ? node.deptResponsibility : '',
      manager_id: node.agentId,
      agent_ids: agentIds,
      children,
    };
  };

  return buildNode(root, true);
}

export function buildCommittedTreeBaseline(root: DraftOrgNode | null): DeptTreeNode | null {
  return buildDeptTreePayload(root);
}

export function treeHasPendingNode(root: DraftOrgNode | null): boolean {
  if (!root) {
    return false;
  }

  const stack = [root];
  while (stack.length) {
    const current = stack.pop()!;
    if (current.kind === 'pending') {
      return true;
    }
    for (let index = current.children.length - 1; index >= 0; index -= 1) {
      stack.push(current.children[index]);
    }
  }

  return false;
}


export function replacePendingNode(root: DraftOrgNode, pendingId: string, nextNode: DraftOrgNode): boolean {
  for (let index = 0; index < root.children.length; index += 1) {
    const child = root.children[index];
    if (child.id === pendingId) {
      root.children.splice(index, 1, nextNode);
      return true;
    }
    if (replacePendingNode(child, pendingId, nextNode)) {
      return true;
    }
  }

  return false;
}

export function removePendingNode(root: DraftOrgNode, pendingId: string): boolean {
  for (let index = 0; index < root.children.length; index += 1) {
    const child = root.children[index];
    if (child.id === pendingId) {
      root.children.splice(index, 1);
      return true;
    }
    if (removePendingNode(child, pendingId)) {
      return true;
    }
  }

  return false;
}

export function removeNodeById(root: DraftOrgNode, nodeId: string): boolean {
  for (let index = 0; index < root.children.length; index += 1) {
    const child = root.children[index];
    if (child.id === nodeId) {
      root.children.splice(index, 1);
      return true;
    }
    if (removeNodeById(child, nodeId)) {
      return true;
    }
  }

  return false;
}


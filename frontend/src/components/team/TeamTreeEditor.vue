<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import { getAgentsByTeamId, getDeptTree, getFrontendConfig, getRoleTemplates, saveMembersByTeamId, setDeptTree } from '../../api';
import { showGlobalSuccessToast } from '../../appUiState';
import {
  useMemberEditorDialog,
  type MemberDriverOption,
  type MemberModelOption,
  type MemberTemplateOption,
} from '../../composables/useMemberEditorDialog';
import { displayName } from '../../utils';
import DepartmentEditorDialog from './DepartmentEditorDialog.vue';
import TeamMembersCard from './TeamMembersCard.vue';
import MemberEditorDialog from './MemberEditorDialog.vue';
import ConfirmDialog from '../ui/ConfirmDialog.vue';
import type { DeptTreeNode, FrontendConfig } from '../../types';
import { MODEL_SLOT_LABELS } from '../../types';
import type { AgentInfo } from '../../types';
import type { RoleTemplateSummary } from '../../types';
import type { TeamGraphNode } from './teamGraphTypes';

type DraftOrgNode = {
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

const props = defineProps<{
  teamId: number;
  teamName: string;
  teamEnabled: boolean;
}>();

const emit = defineEmits<{
  saved: [];
  disableTeam: [teamId: number];
}>();

const { t } = useI18n();
const driverCatalog = ref<MemberDriverOption[]>([]);
const modelCatalog = ref<MemberModelOption[]>([]);
const roleTemplateCatalog = ref<RoleTemplateSummary[]>([]);
const frontendConfig = ref<FrontendConfig | null>(null);
const isLoading = ref(false);
const isSavingTeamMembers = ref(false);
const isReadonly = ref(true);
const teamMemberStatus = ref('');
const committedAgents = ref<AgentInfo[]>([]);
const committedOrgTree = ref<DraftOrgNode | null>(null);
const draftOrgTree = ref<DraftOrgNode | null>(null);
const editingPendingSlotId = ref<string | null>(null);
const editingDepartmentNodeId = ref('');
const memberEditorStatus = ref('');
const departmentEditorName = ref('');
const departmentEditorResponsibility = ref('');
const departmentEditorEditable = ref(true);
const pendingEditAfterDisable = ref(false);

const confirmState = ref<{
  title: string;
  message: string;
  confirmLabel: string;
  danger: boolean;
  action: null | { type: 'remove-member'; nodeId: string; memberName: string } | { type: 'disable-team' } | { type: 'cancel-edit' };
}>({
  title: '',
  message: '',
  confirmLabel: t('common.confirm'),
  danger: true,
  action: null,
});

function createDraftNodeId(prefix = 'node'): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function cloneDraftOrgNode(node: DraftOrgNode | null): DraftOrgNode | null {
  if (!node) {
    return null;
  }

  return {
    ...node,
    children: node.children.map((child) => cloneDraftOrgNode(child)!),
  };
}

function createPendingNode(hasAssignedDepartment = false): DraftOrgNode {
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

function createDepartmentNameAllocator(initialDepartmentNames: string[] = []): () => string {
  const newDeptPrefix = t('teamTree.newDeptPrefix');
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

function parseDriverTypeValue(driver: string): string {
  const normalized = driver.trim().toUpperCase();
  if (normalized === 'NATIVE' || normalized === 'CLAUDE_SDK' || normalized === 'TSP') {
    return normalized;
  }
  return '';
}

function isOnBoardAgent(agent: AgentInfo): boolean {
  return agent.employ_status === 'ON_BOARD' || !agent.employ_status;
}

function buildModelCatalog(config: FrontendConfig | null): MemberModelOption[] {
  const slotOptions: MemberModelOption[] = (config?.model_slots ?? []).map((slot) => ({
    value: `${slot.key}@system`,
    label: MODEL_SLOT_LABELS[slot.key] ?? slot.key,
  }));

  return [
    { value: '', label: '自动' },
    ...slotOptions,
  ];
}

function buildDriverCatalog(config: FrontendConfig | null): MemberDriverOption[] {
  return [
    { value: '', label: t('common.auto') },
    ...((config?.driver_types ?? []).map((item) => ({
      value: item.name,
      label: item.description ? `${item.name} · ${item.description}` : item.name,
    }))),
  ];
}

function createDraftNodeIdFromAgentId(agentId: number): string {
  return `agent-${agentId}`;
}

function createMemberNode(
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

function buildFallbackOrgTree(agents: AgentInfo[]): DraftOrgNode | null {
  const leader = agents[0];
  if (!leader) {
    return null;
  }

  return createMemberNode(leader.name, leader, {
    hasAssignedDepartment: false,
    children: agents.slice(1).map((agent) => createMemberNode(agent.name, agent)),
  });
}

function buildDraftOrgNodeFromDeptTree(
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

function buildDraftOrgTree(tree: DeptTreeNode | null, agents: AgentInfo[]): DraftOrgNode | null {
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

function collectMemberNodes(root: DraftOrgNode | null): DraftOrgNode[] {
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

function findNodeById(root: DraftOrgNode | null, nodeId: string): DraftOrgNode | null {
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

function countManagedChildren(node: DraftOrgNode): number {
  return node.children.filter((child) => child.kind === 'member').length;
}

function buildNextAutoDepartmentName(): string {
  const currentDepartmentNames = collectMemberNodes(draftOrgTree.value)
    .map((node) => node.deptName)
    .filter(Boolean);
  return createDepartmentNameAllocator(currentDepartmentNames)();
}

function resolveRoleTemplateNameById(templateId: number | null | undefined): string {
  if (typeof templateId !== 'number' || templateId <= 0) {
    return t('teamTree.noTemplate');
  }
  const template = roleTemplateCatalog.value.find((item) => item.id === templateId);
  return template ? displayName(template) : t('agent.templateFallback', { id: templateId });
}

function buildMembersSavePayload(root: DraftOrgNode | null = draftOrgTree.value): Array<{
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

function normalizeSavedAgentForDraft(agent: AgentInfo): AgentInfo {
  return {
    ...agent,
    model: agent.model || '',
    driver: parseDriverTypeValue(agent.driver || ''),
  };
}

function hydrateDraftTreeAgentIds(root: DraftOrgNode | null, savedAgents: AgentInfo[]): DraftOrgNode | null {
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

function assertDraftTreeAgentIdsReady(root: DraftOrgNode | null): void {
  const missingAgentNode = collectMemberNodes(root).find((node) => node.agentId === null);
  if (missingAgentNode) {
    throw new Error(`Missing agent id for member: ${missingAgentNode.memberName}`);
  }
}

function buildCommittedMembersSaveBaseline(): Array<{
  id: number | null;
  name: string;
  role_template_id: number;
  model: string;
  driver: string;
}> {
  return committedAgents.value
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

function buildDeptTreePayload(root: DraftOrgNode | null = draftOrgTree.value): DeptTreeNode | null {
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

function buildCommittedTreeBaseline(): DeptTreeNode | null {
  return buildDeptTreePayload(committedOrgTree.value);
}

function syncCommittedState(tree: DeptTreeNode | null, agents: AgentInfo[]): void {
  const nextAgents = agents;
  committedAgents.value = nextAgents.map((agent) => ({ ...agent }));
  committedOrgTree.value = buildDraftOrgTree(tree, committedAgents.value);
  draftOrgTree.value = cloneDraftOrgNode(committedOrgTree.value);
}

const selectedTeamMembers = computed(() => (
  collectMemberNodes(draftOrgTree.value).map((node) => node.memberName)
));

const selectedTeamMemberTemplates = computed<Record<string, string>>(() => (
  Object.fromEntries(
    collectMemberNodes(draftOrgTree.value).map((node) => [
      node.memberName,
      resolveRoleTemplateNameById(node.roleTemplateId),
    ]),
  )
));

const memberPanelStatus = computed(() => {
  if (isLoading.value) {
    return t('teamTree.loading');
  }

  if (teamMemberStatus.value === t('teamTree.loadFailed')) {
    return teamMemberStatus.value;
  }

  return '';
});

const inlineTeamMemberStatus = computed(() => (
  memberPanelStatus.value ? '' : teamMemberStatus.value
));

const currentEditingMemberEmployeeNumber = computed(() => (
  findNodeById(draftOrgTree.value, editingNodeId.value)?.employeeNumber || ''
));

const memberTemplateOptions = computed(() => {
  const definitions = new Map<number, MemberTemplateOption>();

  roleTemplateCatalog.value.forEach((template) => {
    if (!definitions.has(template.id)) {
      definitions.set(template.id, {
        id: template.id,
        name: template.name,
        displayName: displayName(template),
        soul: template.soul || '',
      });
    }
  });

  collectMemberNodes(draftOrgTree.value).forEach((node) => {
    if (typeof node.roleTemplateId === 'number' && node.roleTemplateId > 0 && !definitions.has(node.roleTemplateId)) {
      definitions.set(node.roleTemplateId, {
        id: node.roleTemplateId,
        name: t('agent.templateFallback', { id: node.roleTemplateId }),
        displayName: t('agent.templateFallback', { id: node.roleTemplateId }),
        soul: '',
      });
    }
  });

  return Array.from(definitions.values()).sort((left, right) => left.displayName.localeCompare(right.displayName));
});

const {
  editingMemberName: editingNodeId,
  memberEditorName,
  memberEditorKeyword,
  memberEditorTemplateId,
  memberEditorModel,
  memberEditorDriver,
  memberEditorOpen,
  memberEditorEditable,
  memberEditorAgentId,
  currentMemberTemplateOption,
  memberModelOptions,
  filteredMemberTemplateOptions,
  memberDriverOptions,
  openMemberEditor,
  openMemberViewer,
  openPendingMemberEditor,
  closeMemberEditor,
  replaceSelectedTemplate,
  resetDialogState,
} = useMemberEditorDialog({
  templateOptions: memberTemplateOptions,
  driverCatalog,
  modelCatalog,
  resolveId: (nodeId: string) => findNodeById(draftOrgTree.value, nodeId)?.agentId ?? null,
  resolveName: (nodeId: string) => findNodeById(draftOrgTree.value, nodeId)?.memberName || nodeId,
  resolveModel: (nodeId: string) => findNodeById(draftOrgTree.value, nodeId)?.model || '',
  resolveDriver: (nodeId: string) => findNodeById(draftOrgTree.value, nodeId)?.driver || '',
  resolveTemplateId: (nodeId: string) => findNodeById(draftOrgTree.value, nodeId)?.roleTemplateId ?? null,
  canLoadMemberDetail: (nodeId: string) => Boolean(findNodeById(draftOrgTree.value, nodeId)?.agentId),
});

const currentTemplateModelLabel = computed(() => '自动');

const currentTemplateName = computed(() => currentMemberTemplateOption.value?.displayName || '');
const currentTemplateSoul = computed(() => currentMemberTemplateOption.value?.soul || '');

watch(
  [memberEditorName, memberEditorKeyword, memberEditorTemplateId, memberEditorModel, memberEditorDriver],
  () => {
    if (memberEditorStatus.value) {
      memberEditorStatus.value = '';
    }
  },
);

function toGraphNode(node: DraftOrgNode, teamName: string): TeamGraphNode {
  if (node.kind === 'pending') {
    return {
      id: node.id,
      kind: 'pending',
      name: '',
      departmentName: '',
      subtitle: t('teamTree.subtitle'),
      avatarName: '',
      children: [],
    };
  }

  return {
    id: node.id,
    kind: 'member',
    agentId: node.agentId,
    name: node.memberName,
    departmentName: node.hasAssignedDepartment ? node.deptName : t('teamTree.unassignedDepartment'),
    hasDepartment: node.hasAssignedDepartment,
    subtitle: resolveRoleTemplateNameById(node.roleTemplateId),
    employeeNumber: node.employeeNumber,
    avatarName: node.memberName,
    avatarSeed: `${teamName}::${node.memberName}`,
    children: node.children.map((child) => toGraphNode(child, teamName)),
  };
}

const graphRootNode = computed<TeamGraphNode | null>(() => (
  draftOrgTree.value ? toGraphNode(draftOrgTree.value, props.teamName) : null
));

function treeHasPendingNode(root: DraftOrgNode | null): boolean {
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

const hasMemberConfigChanges = computed(() =>
  JSON.stringify(buildMembersSavePayload()) !== JSON.stringify(buildCommittedMembersSaveBaseline()),
);

const hasDeptTreeChanges = computed(() =>
  JSON.stringify(buildDeptTreePayload()) !== JSON.stringify(buildCommittedTreeBaseline()),
);

const hasTeamMemberChanges = computed(() =>
  hasMemberConfigChanges.value || hasDeptTreeChanges.value,
);

const departmentEditorOpen = computed(() => !!editingDepartmentNodeId.value);
const currentDepartmentMemberName = computed(() => (
  findNodeById(draftOrgTree.value, editingDepartmentNodeId.value)?.memberName || ''
));

const memberPanelActions = computed(() => {
  if (isReadonly.value) {
    return [
      {
        key: 'edit',
        label: t('teamTree.editTeamOrg'),
        primary: true,
        disabled: isLoading.value,
      },
    ];
  }

  return [
    { key: 'cancel', label: t('common.cancel'), disabled: isSavingTeamMembers.value },
    {
      key: 'save',
      label: isSavingTeamMembers.value ? t('teamTree.saving') : t('common.save'),
      disabled: !hasTeamMemberChanges.value || isSavingTeamMembers.value || treeHasPendingNode(draftOrgTree.value),
      primary: true,
      showBadge: hasTeamMemberChanges.value,
    },
  ];
});

watch(
  () => props.teamId,
  async () => {
    const requestTeamId = props.teamId;
    isLoading.value = true;
    teamMemberStatus.value = '';
    try {
      const [deptTree, teamAgents, roleTemplates, nextFrontendConfig] = await Promise.all([
        getDeptTree(requestTeamId),
        getAgentsByTeamId(requestTeamId),
        getRoleTemplates(),
        getFrontendConfig(),
      ]);
      if (requestTeamId !== props.teamId) {
        return;
      }

      frontendConfig.value = nextFrontendConfig;
      modelCatalog.value = buildModelCatalog(nextFrontendConfig);
      driverCatalog.value = buildDriverCatalog(nextFrontendConfig);
      roleTemplateCatalog.value = roleTemplates.map((template) => ({
        ...template,
      }));
      const nextMembers = teamAgents
        .filter(isOnBoardAgent)
        .map((agent) => ({
          ...agent,
        }));

      syncCommittedState(deptTree, nextMembers);
      editingPendingSlotId.value = null;
      memberEditorStatus.value = '';
      editingDepartmentNodeId.value = '';
      departmentEditorName.value = '';
      departmentEditorResponsibility.value = '';
      teamMemberStatus.value = '';
      isReadonly.value = true;
      resetDialogState();
    } catch (error) {
      console.error(error);
      if (requestTeamId !== props.teamId) {
        return;
      }
      frontendConfig.value = null;
      modelCatalog.value = [];
      driverCatalog.value = [];
      roleTemplateCatalog.value = [];
      committedAgents.value = [];
      committedOrgTree.value = null;
      draftOrgTree.value = null;
      editingPendingSlotId.value = null;
      memberEditorStatus.value = '';
      editingDepartmentNodeId.value = '';
      departmentEditorName.value = '';
      departmentEditorResponsibility.value = '';
      teamMemberStatus.value = t('teamTree.loadFailed');
      isReadonly.value = true;
      resetDialogState();
    } finally {
      if (requestTeamId === props.teamId) {
        isLoading.value = false;
      }
    }
  },
  { immediate: true },
);

function cancelTeamMemberEdit(): void {
  draftOrgTree.value = cloneDraftOrgNode(committedOrgTree.value);
  editingPendingSlotId.value = null;
  memberEditorStatus.value = '';
  editingDepartmentNodeId.value = '';
  departmentEditorName.value = '';
  departmentEditorResponsibility.value = '';
  teamMemberStatus.value = '';
  isReadonly.value = true;
  closeMemberEditor();
}

async function saveTeamMembers(): Promise<void> {
  if (isSavingTeamMembers.value || !hasTeamMemberChanges.value) {
    return;
  }

  isSavingTeamMembers.value = true;
  teamMemberStatus.value = '';

  try {
    const draftBeforeSave = cloneDraftOrgNode(draftOrgTree.value);
    const nextMembers = buildMembersSavePayload(draftBeforeSave);
    const baselineDeptTree = buildCommittedTreeBaseline();

    const savedMembers = await saveMembersByTeamId(props.teamId, nextMembers);
    const activeSavedMembers = savedMembers.filter(isOnBoardAgent);
    const draftWithAgentIds = hydrateDraftTreeAgentIds(draftBeforeSave, activeSavedMembers);
    assertDraftTreeAgentIdsReady(draftWithAgentIds);
    const nextDeptTree = buildDeptTreePayload(draftWithAgentIds);

    if (nextDeptTree && JSON.stringify(nextDeptTree) !== JSON.stringify(baselineDeptTree)) {
      await setDeptTree(props.teamId, nextDeptTree);
    }

    const nextAgents = activeSavedMembers.map((agent) => ({ ...agent }));

    syncCommittedState(nextDeptTree, nextAgents);
    editingPendingSlotId.value = null;
    memberEditorStatus.value = '';
    editingDepartmentNodeId.value = '';
    departmentEditorName.value = '';
    departmentEditorResponsibility.value = '';
    teamMemberStatus.value = t('teamTree.saved');
    isReadonly.value = true;
    showGlobalSuccessToast(t('teamTree.saveSuccess'));
    closeMemberEditor();
    emit('saved');
  } catch (error) {
    console.error(error);
    teamMemberStatus.value = t('teamTree.saveFailed');
  } finally {
    isSavingTeamMembers.value = false;
  }
}

function enterEditMode(): void {
  isReadonly.value = false;
  if (!draftOrgTree.value) {
    draftOrgTree.value = createPendingNode();
    teamMemberStatus.value = '';
  }
}

watch(
  () => props.teamEnabled,
  (enabled) => {
    if (!enabled && pendingEditAfterDisable.value) {
      pendingEditAfterDisable.value = false;
      enterEditMode();
    }
  },
);

function handleMemberPanelAction(actionKey: string): void {
  if (actionKey === 'edit') {
    if (props.teamEnabled) {
      confirmState.value = {
        title: t('teamTree.disableToEditTitle'),
        message: t('teamTree.disableToEditMsg', { name: props.teamName }),
        confirmLabel: t('teamTree.disableToEditBtn'),
        danger: false,
        action: { type: 'disable-team' },
      };
      return;
    }
    enterEditMode();
    return;
  }

  if (actionKey === 'cancel') {
    if (hasTeamMemberChanges.value) {
      confirmState.value = {
        title: t('teamTree.cancelEditTitle'),
        message: t('teamTree.cancelEditMsg'),
        confirmLabel: t('teamTree.cancelEditConfirmBtn'),
        danger: true,
        action: { type: 'cancel-edit' },
      };
    } else {
      cancelTeamMemberEdit();
    }
    return;
  }

  if (actionKey === 'save') {
    void saveTeamMembers();
  }
}

function toggleTeamMember(nodeId: string): void {
  if (findNodeById(draftOrgTree.value, nodeId)) {
    requestRemoveMember(nodeId);
  }
}

function replacePendingNode(root: DraftOrgNode, pendingId: string, nextNode: DraftOrgNode): boolean {
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

function removePendingNode(root: DraftOrgNode, pendingId: string): boolean {
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

function removeNodeById(root: DraftOrgNode, nodeId: string): boolean {
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

function saveMemberEditor(): void {
  if (memberEditorTemplateId.value === null) {
    return;
  }

  const nextMemberName = memberEditorName.value.trim();
  if (!nextMemberName) {
    memberEditorStatus.value = t('teamTree.emptyNameError');
    return;
  }

  const currentNodeId = editingPendingSlotId.value || editingNodeId.value;
  const hasDuplicateName = collectMemberNodes(draftOrgTree.value).some((node) => (
    node.memberName === nextMemberName && node.id !== currentNodeId
  ));
  if (hasDuplicateName) {
    memberEditorStatus.value = t('teamTree.duplicateNameError', { name: nextMemberName });
    return;
  }

  const nextTree = cloneDraftOrgNode(draftOrgTree.value);
  if (!nextTree) {
    return;
  }

  if (editingPendingSlotId.value) {
    const pendingNode = findNodeById(nextTree, editingPendingSlotId.value);
    const nextNode = createMemberNode(nextMemberName, undefined, {
      hasAssignedDepartment: pendingNode?.hasAssignedDepartment ?? false,
      deptName: '',
      deptResponsibility: '',
    });
    nextNode.roleTemplateId = memberEditorTemplateId.value;
    nextNode.model = memberEditorModel.value.trim();
    nextNode.driver = memberEditorDriver.value || '';

    if (nextTree.id === editingPendingSlotId.value) {
      draftOrgTree.value = nextNode;
    } else {
      replacePendingNode(nextTree, editingPendingSlotId.value, nextNode);
      draftOrgTree.value = nextTree;
    }

    editingPendingSlotId.value = null;
    memberEditorStatus.value = '';
    showGlobalSuccessToast(t('teamTree.updatedToTree'));
    closeMemberEditor();
    return;
  }

  const targetNode = findNodeById(nextTree, editingNodeId.value);
  if (!targetNode) {
    return;
  }

  targetNode.memberName = nextMemberName;
  targetNode.roleTemplateId = memberEditorTemplateId.value;
  targetNode.model = memberEditorModel.value.trim();
  targetNode.driver = memberEditorDriver.value || targetNode.driver || '';
  draftOrgTree.value = nextTree;
  memberEditorStatus.value = '';
  showGlobalSuccessToast(t('teamTree.updatedToTree'));
  closeMemberEditor();
}

function addSubordinate(parentNodeId: string): void {
  const nextTree = cloneDraftOrgNode(draftOrgTree.value);
  if (!nextTree) {
    return;
  }

  const parentNode = findNodeById(nextTree, parentNodeId);
  if (!parentNode) {
    return;
  }

  if (countManagedChildren(parentNode) === 0 && !parentNode.deptName.trim()) {
    parentNode.deptName = buildNextAutoDepartmentName();
  }

  parentNode.children.push(createPendingNode(true));
  draftOrgTree.value = nextTree;
  teamMemberStatus.value = '';
}

function openDepartmentEditor(nodeId: string): void {
  const memberNode = findNodeById(draftOrgTree.value, nodeId);
  if (!memberNode) {
    return;
  }

  departmentEditorEditable.value = true;
  editingDepartmentNodeId.value = nodeId;
  departmentEditorName.value = memberNode.deptName;
  departmentEditorResponsibility.value = memberNode.deptResponsibility;
  teamMemberStatus.value = '';
}

function openDepartmentViewer(nodeId: string): void {
  const memberNode = findNodeById(draftOrgTree.value, nodeId);
  if (!memberNode) {
    return;
  }

  departmentEditorEditable.value = false;
  editingDepartmentNodeId.value = nodeId;
  departmentEditorName.value = memberNode.deptName;
  departmentEditorResponsibility.value = memberNode.deptResponsibility;
  teamMemberStatus.value = '';
}

function closeDepartmentEditor(): void {
  departmentEditorEditable.value = true;
  editingDepartmentNodeId.value = '';
  departmentEditorName.value = '';
  departmentEditorResponsibility.value = '';
}

function saveDepartmentEditor(): void {
  const nodeId = editingDepartmentNodeId.value;
  const nextDepartmentName = departmentEditorName.value.trim();
  const nextDepartmentResponsibility = departmentEditorResponsibility.value.trim();
  if (!nodeId) {
    return;
  }
  if (!nextDepartmentName) {
    teamMemberStatus.value = t('teamTree.emptyDeptNameError');
    return;
  }

  const nextTree = cloneDraftOrgNode(draftOrgTree.value);
  const memberNode = findNodeById(nextTree, nodeId);
  if (!memberNode) {
    return;
  }

  memberNode.deptName = nextDepartmentName;
  memberNode.deptResponsibility = nextDepartmentResponsibility;
  draftOrgTree.value = nextTree;
  showGlobalSuccessToast(t('teamTree.updatedToTree'));
  closeDepartmentEditor();
}

function editPendingSlot(slotId: string): void {
  editingPendingSlotId.value = slotId;
  openPendingMemberEditor('');
}

function removePendingSlot(slotId: string): void {
  const nextTree = cloneDraftOrgNode(draftOrgTree.value);
  if (!nextTree) {
    return;
  }

  if (nextTree.id === slotId) {
    draftOrgTree.value = null;
  } else {
    removePendingNode(nextTree, slotId);
    draftOrgTree.value = nextTree;
  }

  if (editingPendingSlotId.value === slotId) {
    editingPendingSlotId.value = null;
    closeMemberEditor();
  }
}

function requestRemoveMember(nodeId: string): void {
  const memberNode = findNodeById(draftOrgTree.value, nodeId);
  if (!memberNode) {
    return;
  }

  confirmState.value = {
    title: t('teamTree.removeMemberTitle'),
    message: t('teamTree.removeMemberConfirm', { name: memberNode.memberName }),
    confirmLabel: t('teamTree.removeMember'),
    danger: true,
    action: {
      type: 'remove-member',
      nodeId,
      memberName: memberNode.memberName,
    },
  };
}

function closeConfirmDialog(): void {
  confirmState.value = {
    title: '',
    message: '',
    confirmLabel: t('common.confirm'),
    danger: true,
    action: null,
  };
}

async function confirmDangerAction(): Promise<void> {
  const action = confirmState.value.action;
  if (!action) {
    return;
  }

  if (action.type === 'disable-team') {
    pendingEditAfterDisable.value = true;
    closeConfirmDialog();
    emit('disableTeam', props.teamId);
    return;
  }

  if (action.type === 'cancel-edit') {
    cancelTeamMemberEdit();
    closeConfirmDialog();
    return;
  }

  const nextTree = cloneDraftOrgNode(draftOrgTree.value);
  if (!nextTree) {
    closeConfirmDialog();
    return;
  }

  if (nextTree.id === action.nodeId) {
    draftOrgTree.value = null;
  } else {
    removeNodeById(nextTree, action.nodeId);
    draftOrgTree.value = nextTree;
  }

  if (editingNodeId.value === action.nodeId) {
    closeMemberEditor();
  }
  if (editingDepartmentNodeId.value === action.nodeId) {
    closeDepartmentEditor();
  }

  closeConfirmDialog();
}

function openMemberViewerByNode(_agentId: number | null, nodeId: string, _agentName: string): void {
  openMemberViewer(nodeId);
}
</script>

<template>
  <div class="team-tree-editor">
    <p v-if="inlineTeamMemberStatus" class="team-member-status">{{ inlineTeamMemberStatus }}</p>

    <TeamMembersCard
      :team-name="teamName"
      :selected-agents="selectedTeamMembers"
      :member-templates="selectedTeamMemberTemplates"
      :root-node="graphRootNode"
      :status-message="memberPanelStatus"
      :readonly="isReadonly"
      :actions="memberPanelActions"
      :show-edit-action="!isReadonly"
      @action="handleMemberPanelAction"
      @toggle-agent="toggleTeamMember"
      @view-agent="openMemberViewerByNode"
      @edit-agent="openMemberEditor"
      @edit-department="openDepartmentEditor"
      @view-department="openDepartmentViewer"
      @add-subordinate="addSubordinate"
      @edit-pending-slot="editPendingSlot"
      @remove-pending-slot="removePendingSlot"
    />

    <MemberEditorDialog
      :open="memberEditorOpen"
      :editable="memberEditorEditable"
      :agent-id="memberEditorAgentId"
      :team-name="teamName"
      :member-name="memberEditorName"
      :status="memberEditorStatus"
      :employee-number="currentEditingMemberEmployeeNumber"
      :member-model="memberEditorModel"
      :keyword="memberEditorKeyword"
      :selected-template-id="memberEditorTemplateId"
      :selected-template-name="currentTemplateName"
      :current-template-model="currentTemplateModelLabel"
      :current-template-soul="currentTemplateSoul"
      :model-options="memberModelOptions"
      :driver="memberEditorDriver"
      :driver-options="memberDriverOptions"
      :template-options="filteredMemberTemplateOptions"
      @close="closeMemberEditor"
      @save="saveMemberEditor"
      @update:member-name="memberEditorName = $event"
      @update:member-model="memberEditorModel = $event"
      @update:keyword="memberEditorKeyword = $event"
      @update:selected-template="replaceSelectedTemplate($event)"
      @update:driver="memberEditorDriver = $event"
    />

    <ConfirmDialog
      :open="!!confirmState.action"
      :title="confirmState.title"
      :message="confirmState.message"
      :confirm-label="confirmState.confirmLabel"
      :danger="confirmState.danger"
      @close="closeConfirmDialog"
      @confirm="confirmDangerAction"
    />

    <DepartmentEditorDialog
      :open="departmentEditorOpen"
      :editable="departmentEditorEditable"
      :member-name="currentDepartmentMemberName"
      :department-name="departmentEditorName"
      :department-responsibility="departmentEditorResponsibility"
      @close="closeDepartmentEditor"
      @save="saveDepartmentEditor"
      @update:department-name="departmentEditorName = $event"
      @update:department-responsibility="departmentEditorResponsibility = $event"
    />
  </div>
</template>

<style scoped>
.team-tree-editor {
  display: grid;
  gap: 10px;
  min-height: 0;
  align-items: start;
}

.team-member-status {
  margin: -2px 0 0;
  color: var(--muted);
  font-size: 0.72rem;
}
</style>

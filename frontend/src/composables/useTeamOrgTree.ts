import { computed, ref, watch } from 'vue';
import { getAgentsByTeamId, getDeptTree, getFrontendConfig, getRoleTemplates, saveMembersByTeamId, setDeptTree } from '../api';
import { showGlobalSuccessToast } from '../appUiState';
import {
  useMemberEditorDialog,
  type MemberDriverOption,
  type MemberModelOption,
  type MemberTemplateOption,
} from '../composables/useMemberEditorDialog';
import { displayName } from '../utils';
import type { AgentInfo, DeptTreeNode, FrontendConfig, RoleTemplateSummary } from '../types';
import type { TeamGraphNode } from '../components/team/teamGraphTypes';
import {
  assertDraftTreeAgentIdsReady,
  buildCommittedMembersSaveBaseline,
  buildCommittedTreeBaseline,
  buildDeptTreePayload,
  buildDraftOrgTree,
  buildDriverCatalog,
  buildMembersSavePayload,
  buildModelCatalog,
  cloneDraftOrgNode,
  collectMemberNodes,
  countManagedChildren,
  createDepartmentNameAllocator,
  createMemberNode,
  createPendingNode,
  findNodeById,
  hydrateDraftTreeAgentIds,
  isOnBoardAgent,
  normalizeSavedAgentForDraft,
  removeNodeById,
  removePendingNode,
  replacePendingNode,
  treeHasPendingNode,
  type DraftOrgNode,
} from '../utils/teamTreeModel';

export interface TeamTreeEditorProps {
  teamId: number;
  teamName: string;
  teamEnabled: boolean;
}

export interface TeamTreeEditorEmit {
  (event: 'saved'): void;
  (event: 'disableTeam', teamId: number): void;
}

/** i18n 的 `t`：本文件只用到「key + 可选命名参数」两种形态。 */
export type Translate = (key: string, named?: Record<string, unknown>) => string;

/**
 * 组织架构树编辑器的状态与交互（从 `TeamTreeEditor.vue` 抽出的第二批，#213 P3）。
 *
 * 组件本体只剩 props / emits 与模板绑定；本文件负责草稿树、成员/部门编辑态、保存与差异基线。
 * 纯计算部分在 `utils/teamTreeModel.ts`（不依赖 Vue，可单测）。
 */

export function useTeamOrgTree(
  props: TeamTreeEditorProps,
  emit: TeamTreeEditorEmit,
  t: Translate,
) {
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


function buildNextAutoDepartmentName(): string {
  const currentDepartmentNames = collectMemberNodes(draftOrgTree.value)
    .map((node) => node.deptName)
    .filter(Boolean);
  return createDepartmentNameAllocator(t('teamTree.newDeptPrefix'), currentDepartmentNames)();
}

function resolveRoleTemplateNameById(templateId: number | null | undefined): string {
  if (typeof templateId !== 'number' || templateId <= 0) {
    return t('teamTree.noTemplate');
  }
  const template = roleTemplateCatalog.value.find((item) => item.id === templateId);
  return template ? displayName(template) : t('agent.templateFallback', { id: templateId });
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

function toGraphNode(node: DraftOrgNode): TeamGraphNode {
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
    avatarSeed: node.memberName,
    children: node.children.map((child) => toGraphNode(child)),
  };
}

const graphRootNode = computed<TeamGraphNode | null>(() => (
  draftOrgTree.value ? toGraphNode(draftOrgTree.value) : null
));

const hasMemberConfigChanges = computed(() =>
  JSON.stringify(buildMembersSavePayload(draftOrgTree.value))
    !== JSON.stringify(buildCommittedMembersSaveBaseline(committedAgents.value)),
);

const hasDeptTreeChanges = computed(() =>
  JSON.stringify(buildDeptTreePayload(draftOrgTree.value))
    !== JSON.stringify(buildCommittedTreeBaseline(committedOrgTree.value)),
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
      driverCatalog.value = buildDriverCatalog(nextFrontendConfig, t('common.auto'));
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
    const baselineDeptTree = buildCommittedTreeBaseline(committedOrgTree.value);

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

  return {
    isReadonly,
    memberEditorStatus,
    departmentEditorName,
    departmentEditorResponsibility,
    departmentEditorEditable,
    confirmState,
    selectedTeamMembers,
    selectedTeamMemberTemplates,
    memberPanelStatus,
    inlineTeamMemberStatus,
    currentEditingMemberEmployeeNumber,
    currentTemplateModelLabel,
    currentTemplateName,
    currentTemplateSoul,
    graphRootNode,
    departmentEditorOpen,
    currentDepartmentMemberName,
    memberPanelActions,
    handleMemberPanelAction,
    toggleTeamMember,
    saveMemberEditor,
    addSubordinate,
    openDepartmentEditor,
    openDepartmentViewer,
    closeDepartmentEditor,
    saveDepartmentEditor,
    editPendingSlot,
    removePendingSlot,
    closeConfirmDialog,
    confirmDangerAction,
    openMemberViewerByNode,
    memberEditorName,
    memberEditorKeyword,
    memberEditorTemplateId,
    memberEditorModel,
    memberEditorDriver,
    memberEditorOpen,
    memberEditorEditable,
    memberEditorAgentId,
    memberModelOptions,
    filteredMemberTemplateOptions,
    memberDriverOptions,
    openMemberEditor,
    closeMemberEditor,
    replaceSelectedTemplate,
  };
}

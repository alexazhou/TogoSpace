<script setup lang="ts">
import { useI18n } from 'vue-i18n';
import DepartmentEditorDialog from './DepartmentEditorDialog.vue';
import TeamMembersCard from './TeamMembersCard.vue';
import MemberEditorDialog from './MemberEditorDialog.vue';
import ConfirmDialog from '../ui/ConfirmDialog.vue';
import { useTeamOrgTree } from '../../composables/useTeamOrgTree';

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

// 状态与交互全在 composable 里（纯模型在 utils/teamTreeModel.ts），组件只剩绑定。
const {
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
} = useTeamOrgTree(props, emit, t);
</script>

<template>
  <div class="team-tree-editor">
    <p v-if="inlineTeamMemberStatus" class="team-member-status">{{ inlineTeamMemberStatus }}</p>

    <TeamMembersCard
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

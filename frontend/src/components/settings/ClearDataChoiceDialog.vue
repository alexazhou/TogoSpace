<script setup lang="ts">
import { ref, computed } from 'vue';
import { useI18n } from 'vue-i18n';
import { displayName, findDepartmentPath, isDepartmentLeader } from '../../utils';
import type { TeamMember, DeptTreeNode } from '../../types';

const props = defineProps<{
  open: boolean;
  teamName: string;
  members: TeamMember[];
  deptTree?: DeptTreeNode | null;
}>();

const emit = defineEmits<{
  close: [];
  clearTeamData: [];
  clearAgentData: [agentId: number, agentName: string];
}>();

const { t } = useI18n();
const mode = ref<'choice' | 'personal' | 'confirm-team' | 'confirm-personal'>('choice');
const selectedAgentId = ref<number | null>(null);

const selectedAgentName = computed(() => {
  const member = props.members.find((m) => m.id === selectedAgentId.value);
  return member ? displayName(member) : '';
});

const selectedAgentEmpNo = computed(() => {
  const member = props.members.find((m) => m.id === selectedAgentId.value);
  return member ? member.employee_number : 0;
});

function getMemberDeptPath(memberId: number): string {
  if (!props.deptTree) return t('teamTree.unassignedDepartment');
  const path = findDepartmentPath(props.deptTree, memberId);
  return path?.join(' / ') ?? t('teamTree.unassignedDepartment');
}

function getIsLeader(memberId: number): boolean {
  if (!props.deptTree) return false;
  return isDepartmentLeader(props.deptTree, memberId);
}

function handleBack() {
  if (mode.value === 'confirm-personal' && props.members.length > 1) {
    mode.value = 'personal';
  } else {
    mode.value = 'choice';
  }
}

function selectPersonal() {
  if (props.members.length === 1) {
    selectedAgentId.value = props.members[0].id;
    mode.value = 'confirm-personal';
  } else {
    mode.value = 'personal';
  }
}

function selectTeam() {
  mode.value = 'confirm-team';
}

function selectMember(memberId: number) {
  selectedAgentId.value = memberId;
  mode.value = 'confirm-personal';
}

function executeClearTeamData() {
  emit('clearTeamData');
}

function executeClearAgentData() {
  if (selectedAgentId.value !== null) {
    emit('clearAgentData', selectedAgentId.value, selectedAgentName.value);
  }
}

function close() {
  emit('close');
  // Reset state after a short delay to allow transition to finish
  setTimeout(() => {
    mode.value = 'choice';
    selectedAgentId.value = null;
  }, 300);
}
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="clear-data-overlay" @click.self="close">
      <section class="clear-data-dialog panel">
        <div class="clear-data-head">
          <p class="clear-data-eyebrow">{{ t('settings.page.clearDataChoiceTitle') }}</p>
          <h3>{{ teamName }}</h3>
        </div>

        <!-- Mode: Choice -->
        <div v-if="mode === 'choice'" class="clear-data-choices">
          <p class="clear-data-msg">{{ t('settings.page.clearDataChoiceMsg') }}</p>
          
          <button type="button" class="choice-card choice-card--personal" @click="selectPersonal">
            <div class="choice-card-content">
              <strong>{{ t('settings.page.clearPersonalData') }}</strong>
              <p>{{ t('settings.page.personalDataNote') }}</p>
            </div>
            <span class="choice-card-arrow">→</span>
          </button>

          <button type="button" class="choice-card choice-card--team" @click="selectTeam">
            <div class="choice-card-content">
              <strong>{{ t('settings.page.clearTeamData') }}</strong>
              <p>{{ t('settings.page.teamDataNote') }}</p>
            </div>
            <span class="choice-card-arrow">→</span>
          </button>
        </div>

        <!-- Mode: Personal Member Selection -->
        <div v-else-if="mode === 'personal'" class="clear-data-personal">
          <div class="personal-head">
            <button type="button" class="back-button" @click="handleBack">← {{ t('common.back') }}</button>
            <p class="personal-msg">{{ t('member.clearDataTitle') }}</p>
          </div>

          <div class="member-list">
            <button
              v-for="member in members"
              :key="member.id"
              type="button"
              class="member-item"
              :class="{ 'is-selected': selectedAgentId === member.id }"
              @click="selectMember(member.id)"
            >
              <div class="member-item-main">
                <span class="member-id">#{{ member.employee_number }}</span>
                <div class="member-info">
                  <div class="member-name-row">
                    <span class="member-name">{{ displayName(member) }}</span>
                    <span v-if="getIsLeader(member.id)" class="leader-badge">{{ t('agent.departmentLeader') }}</span>
                  </div>
                  <span class="member-dept">{{ getMemberDeptPath(member.id) }}</span>
                </div>
              </div>
            </button>
          </div>
        </div>

        <!-- Mode: Confirm Team Data -->
        <div v-else-if="mode === 'confirm-team'" class="clear-data-confirm">
          <div class="personal-head">
            <button type="button" class="back-button" @click="handleBack">← {{ t('common.back') }}</button>
            <p class="personal-msg">{{ t('settings.page.clearTitle') }}</p>
          </div>

          <div class="confirm-body">
            <div class="warning-box">
              <p>{{ t('settings.page.clearMsg') }}</p>
            </div>
          </div>

          <div class="confirm-actions">
            <button type="button" class="ghost-button" @click="close">
              {{ t('common.cancel') }}
            </button>
            <button
              type="button"
              class="secondary-button secondary-button--danger"
              @click="executeClearTeamData"
            >
              {{ t('settings.page.clearBtn') }}
            </button>
          </div>
        </div>

        <!-- Mode: Confirm Personal Data -->
        <div v-else-if="mode === 'confirm-personal'" class="clear-data-confirm">
          <div class="personal-head">
            <button type="button" class="back-button" @click="handleBack">← {{ t('common.back') }}</button>
            <p class="personal-msg">{{ t('member.clearDataTitle') }}</p>
          </div>

          <div class="confirm-body">
            <div class="warning-box warning-box--narrow">
              <p>{{ t('member.clearDataConfirm', { name: selectedAgentName }) }}</p>
            </div>
            <div class="target-member">
              <div class="target-member-content">
                <div class="target-name-row">
                  <strong>{{ selectedAgentName }}</strong>
                  <span v-if="selectedAgentId !== null && getIsLeader(selectedAgentId)" class="leader-badge">{{ t('agent.departmentLeader') }}</span>
                </div>
                <div class="target-meta">
                  <span class="member-id">#{{ selectedAgentEmpNo }}</span>
                  <span class="member-dept">{{ selectedAgentId !== null ? getMemberDeptPath(selectedAgentId) : '' }}</span>
                </div>
              </div>
            </div>
          </div>

          <div class="confirm-actions">
            <button type="button" class="ghost-button" @click="close">
              {{ t('common.cancel') }}
            </button>
            <button
              type="button"
              class="secondary-button secondary-button--danger"
              @click="executeClearAgentData"
            >
              {{ t('member.clearData') }}
            </button>
          </div>
        </div>

        <div v-if="mode === 'choice' || mode === 'personal'" class="clear-data-footer">
          <button type="button" class="ghost-button" @click="close">
            {{ t('common.cancel') }}
          </button>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.clear-data-overlay {
  position: fixed;
  inset: 0;
  z-index: 120;
  display: grid;
  place-items: center;
  padding: 28px;
  background: rgba(6, 10, 16, 0.52);
  backdrop-filter: blur(8px);
}

.clear-data-dialog {
  width: min(480px, 100%);
  padding: 20px;
  display: grid;
  gap: 16px;
  border-radius: 20px;
  border: 1px solid color-mix(in srgb, var(--interactive-focus-border) 26%, var(--border-default) 74%);
  background:
    linear-gradient(
      180deg,
      color-mix(in srgb, var(--surface-panel) 95%, transparent) 0%,
      color-mix(in srgb, var(--surface-panel-muted) 92%, transparent) 100%
    );
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.34);
}

.clear-data-head {
  display: grid;
  gap: 4px;
}

.clear-data-eyebrow {
  margin: 0;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.14em;
  font-size: 0.68rem;
}

.clear-data-head h3 {
  margin: 0;
  color: var(--text-primary);
  font-size: 1.25rem;
}

.clear-data-msg, .personal-msg {
  margin: 0;
  color: var(--text-secondary);
  font-size: 0.9rem;
}

.clear-data-choices {
  display: grid;
  gap: 12px;
}

.choice-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 16px;
  border: 1px solid var(--border-default);
  border-radius: 14px;
  background: var(--surface-panel-muted);
  text-align: left;
  cursor: pointer;
  transition: all 0.2s ease;
}

.choice-card:hover {
  border-color: var(--interactive-focus-border);
  background: var(--surface-elevated);
  transform: translateY(-2px);
}

.choice-card--personal:hover {
  border-color: var(--accent);
}

.choice-card--team:hover {
  border-color: var(--state-danger);
}

.choice-card-content strong {
  display: block;
  color: var(--text-primary);
  font-size: 0.95rem;
  margin-bottom: 4px;
}

.choice-card-content p {
  margin: 0;
  color: var(--text-secondary);
  font-size: 0.78rem;
  line-height: 1.4;
}

.choice-card-arrow {
  font-size: 1.2rem;
  color: var(--text-muted);
  opacity: 0.5;
  transition: all 0.2s ease;
}

.choice-card:hover .choice-card-arrow {
  opacity: 1;
  color: var(--text-primary);
  transform: translateX(4px);
}

.clear-data-personal, .clear-data-confirm {
  display: grid;
  gap: 12px;
}

.personal-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.back-button {
  border: none;
  background: transparent;
  color: var(--accent);
  font-size: 0.82rem;
  cursor: pointer;
  padding: 0;
}

.back-button:hover {
  text-decoration: underline;
}

.member-list {
  max-height: 240px;
  overflow-y: auto;
  display: grid;
  gap: 6px;
  padding-right: 4px;
  scrollbar-width: thin;
}

.member-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  border: 1px solid var(--border-default);
  border-radius: 10px;
  background: var(--surface-panel-muted);
  cursor: pointer;
  transition: all 0.15s ease;
}

.member-item:hover {
  border-color: var(--interactive-focus-border);
  background: var(--surface-elevated);
}

.member-item.is-selected {
  border-color: var(--accent);
  background: color-mix(in srgb, var(--accent) 12%, var(--surface-elevated));
}

.member-name {
  color: var(--text-primary);
  font-size: 0.88rem;
}

.member-id {
  color: var(--text-muted);
  font-size: 0.72rem;
}

.confirm-body {
  display: grid;
  gap: 12px;
}

.warning-box {
  padding: 12px 14px;
  border: 1px solid color-mix(in srgb, var(--state-danger) 24%, var(--border-default) 76%);
  border-radius: 12px;
  background: rgba(239, 107, 103, 0.08);
  color: #de736f;
  font-size: 0.88rem;
  line-height: 1.5;
  text-align: center;
}

.warning-box--narrow {
  width: 100%;
  max-width: none;
  margin: 0;
}

.target-member {
  display: flex;
  width: 100%;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 14px;
  border: 1px solid var(--border-default);
  background: var(--surface-elevated);
  border-radius: 14px;
}

.target-member-content {
  display: grid;
  gap: 4px;
  text-align: center;
}

.target-name-row {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
}

.target-member strong {
  color: var(--text-primary);
  font-size: 1.15rem;
}

.target-meta {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  color: var(--text-muted);
  font-size: 0.78rem;
}

.member-item-main {
  display: flex;
  align-items: center;
  gap: 14px;
  width: 100%;
}

.member-info {
  display: grid;
  gap: 2px;
  text-align: left;
  min-width: 0;
}

.member-name-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.member-name {
  color: var(--text-primary);
  font-size: 0.92rem;
  font-weight: 500;
}

.member-dept {
  color: var(--text-muted);
  font-size: 0.74rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.leader-badge {
  display: inline-flex;
  align-items: center;
  height: 18px;
  padding: 0 6px;
  border-radius: 6px;
  border: 1px solid color-mix(in srgb, var(--state-success) 24%, var(--border-default) 76%);
  background: color-mix(in srgb, var(--state-success) 12%, var(--surface-panel) 88%);
  color: color-mix(in srgb, var(--state-success) 84%, var(--text-primary) 16%);
  font-size: 0.64rem;
  line-height: 1;
}

.confirm-actions, .clear-data-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 8px;
}

.confirm-actions > button,
.clear-data-footer > button {
  min-width: 88px;
  height: 32px;
  padding: 0 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.84rem;
}

.secondary-button--danger {
  border-color: color-mix(in srgb, var(--state-danger) 30%, var(--border-default) 70%);
  background: color-mix(in srgb, var(--state-danger) 16%, var(--surface-panel) 84%);
}

.secondary-button--danger:hover:not(:disabled) {
  border-color: color-mix(in srgb, var(--state-danger) 62%, var(--interactive-focus-border) 38%);
  background: color-mix(in srgb, var(--state-danger) 26%, var(--surface-elevated) 74%);
}
</style>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import { getAgentTasks, getAgentsByTeamId } from '../../api';
import type { AgentInfo, AgentTask, AgentTaskPriority, AgentTaskStatus } from '../../types';
import { displayName, formatTime } from '../../utils';
import AgentTaskCard from './AgentTaskCard.vue';
import AgentTaskDetailModal from './AgentTaskDetailModal.vue';

const props = defineProps<{
  open: boolean;
  agentId: number | null;
  teamId: number | null;
}>();

const emit = defineEmits<{
  'count-change': [count: number];
}>();

const { t } = useI18n();

const tasksLoading = ref(false);
const tasksErrorMessage = ref('');
const tasks = ref<AgentTask[]>([]);
const teamAgents = ref<AgentInfo[]>([]);
const taskFilter = ref<'all' | 'done' | 'undone'>('undone');
const selectedTask = ref<AgentTask | null>(null);
let tasksRequestToken = 0;
let teamAgentsRequestToken = 0;

const DONE_STATUSES = new Set(['DONE', 'CANCELLED']);

const visibleTasks = computed(() => {
  const filtered = taskFilter.value === 'all'
    ? tasks.value
    : taskFilter.value === 'done'
      ? tasks.value.filter((task) => DONE_STATUSES.has(task.status))
      : tasks.value.filter((task) => !DONE_STATUSES.has(task.status));
  return filtered.slice(0, 30);
});

watch(
  () => visibleTasks.value.length,
  (count) => {
    emit('count-change', count);
  },
  { immediate: true },
);

function formatTaskDateTime(value: string | null): string {
  if (!value) {
    return '';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return '';
  }
  return `${date.toLocaleDateString()} ${formatTime(value)}`.trim();
}

function taskStatusLabel(status: AgentTaskStatus): string {
  return t(`agent.taskStatus.${status}`);
}

function taskPriorityLabel(priority: AgentTaskPriority): string {
  return t(`agent.taskPriority.${priority}`);
}

function taskActorLabel(id: number | null): string {
  if (typeof id !== 'number' || id <= 0) {
    return t('common.notSet');
  }
  const matched = teamAgents.value.find((item) => item.id === id);
  return matched ? displayName(matched) : `#${id}`;
}

function taskActorDetailLabel(id: number | null): string {
  if (typeof id !== 'number' || id <= 0) {
    return t('common.none');
  }
  const matched = teamAgents.value.find((item) => item.id === id);
  return matched ? displayName(matched) : `#${id}`;
}

function taskRoomDetailLabel(id: number | null): string {
  if (typeof id !== 'number' || id <= 0) {
    return t('common.none');
  }
  return `#${id}`;
}

function openTaskDetail(task: AgentTask): void {
  selectedTask.value = task;
}

function closeTaskDetail(): void {
  selectedTask.value = null;
}

async function loadTasks(): Promise<void> {
  if (!props.open || props.agentId === null) {
    tasks.value = [];
    tasksErrorMessage.value = '';
    tasksLoading.value = false;
    emit('count-change', 0);
    return;
  }

  const requestToken = ++tasksRequestToken;
  tasksLoading.value = true;
  tasksErrorMessage.value = '';

  try {
    const nextTasks = await getAgentTasks(props.agentId, taskFilter.value !== 'undone');
    if (requestToken !== tasksRequestToken) {
      return;
    }
    tasks.value = nextTasks;
  } catch (error) {
    if (requestToken !== tasksRequestToken) {
      return;
    }
    tasksErrorMessage.value = t('agent.tasksLoadFailed');
    console.error(error);
  } finally {
    if (requestToken === tasksRequestToken) {
      tasksLoading.value = false;
    }
  }
}

async function loadTeamAgents(): Promise<void> {
  if (!props.open || props.teamId === null) {
    teamAgents.value = [];
    return;
  }

  const requestToken = ++teamAgentsRequestToken;

  try {
    const nextAgents = await getAgentsByTeamId(props.teamId);
    if (requestToken !== teamAgentsRequestToken) {
      return;
    }
    teamAgents.value = nextAgents;
  } catch (error) {
    if (requestToken !== teamAgentsRequestToken) {
      return;
    }
    console.error(error);
  }
}

watch(
  () => [props.open, props.agentId] as const,
  () => {
    taskFilter.value = 'undone';
    selectedTask.value = null;
    loadTasks().catch(console.error);
  },
  { immediate: true },
);

watch(
  () => [props.open, props.teamId] as const,
  () => {
    loadTeamAgents().catch(console.error);
  },
  { immediate: true },
);
</script>

<template>
  <div class="agent-task-panel-body">
    <div class="agent-task-filter">
      <button
        type="button"
        class="agent-task-filter__btn"
        :class="{ 'is-active': taskFilter === 'undone' }"
        @click="taskFilter = 'undone'; loadTasks()"
      >{{ t('agent.taskFilterUndone') }}</button>
      <button
        type="button"
        class="agent-task-filter__btn"
        :class="{ 'is-active': taskFilter === 'done' }"
        @click="taskFilter = 'done'; loadTasks()"
      >{{ t('agent.taskFilterDone') }}</button>
      <button
        type="button"
        class="agent-task-filter__btn"
        :class="{ 'is-active': taskFilter === 'all' }"
        @click="taskFilter = 'all'; loadTasks()"
      >{{ t('agent.taskFilterAll') }}</button>
    </div>

    <div v-if="tasksErrorMessage" class="error-banner">{{ tasksErrorMessage }}</div>
    <div v-else-if="tasksLoading && !visibleTasks.length" class="loading-card">{{ t('agent.loadingTasks') }}</div>
    <div v-else-if="!tasksLoading && !visibleTasks.length" class="agent-activity-empty">
      {{ taskFilter === 'undone' ? t('agent.noTasks') : taskFilter === 'done' ? t('agent.taskFilterNoTasksDone') : t('agent.taskFilterNoTasksAll') }}
    </div>
    <div v-else class="agent-task-list sidebar-scroll">
      <AgentTaskCard
        v-for="task in visibleTasks"
        :key="task.id"
        :task="task"
        :assignee-label="taskActorLabel(task.assignee_id)"
        :manager-label="task.manager_id !== null ? taskActorDetailLabel(task.manager_id) : null"
        clickable
        @select="openTaskDetail"
      />
    </div>

    <AgentTaskDetailModal
      :task="selectedTask"
      :created-at-label="selectedTask ? (formatTaskDateTime(selectedTask.created_at) || t('common.notSet')) : ''"
      :creator-label="selectedTask ? taskActorLabel(selectedTask.creator_id) : ''"
      :assignee-label="selectedTask ? taskActorLabel(selectedTask.assignee_id) : ''"
      :manager-label="selectedTask ? taskActorDetailLabel(selectedTask.manager_id) : ''"
      :room-label="selectedTask ? taskRoomDetailLabel(selectedTask.room_id) : ''"
      :priority-label="selectedTask ? taskPriorityLabel(selectedTask.priority) : ''"
      :status-label="selectedTask ? taskStatusLabel(selectedTask.status) : ''"
      @close="closeTaskDetail"
    />
  </div>
</template>

<style scoped>
.agent-task-panel-body {
  min-height: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
}

.agent-task-list {
  flex: 1;
  min-height: 0;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px 0;
}

.agent-task-filter {
  display: flex;
  flex-shrink: 0;
  gap: 6px;
  padding: 8px 12px 4px;
}

.agent-task-filter__btn {
  padding: 3px 10px;
  border-radius: 99px;
  border: 1px solid var(--panel-border);
  background: transparent;
  color: var(--text-secondary);
  font-size: 12px;
  cursor: pointer;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}

.agent-task-filter__btn:hover {
  background: color-mix(in srgb, var(--surface-soft) 60%, transparent);
  color: var(--text-primary);
}

.agent-task-filter__btn.is-active {
  background: color-mix(in srgb, var(--interactive-selected) 22%, var(--surface-pill) 78%);
  border-color: color-mix(in srgb, var(--interactive-selected) 34%, var(--panel-border) 66%);
  color: var(--text-strong);
}

.agent-activity-empty {
  min-height: 120px;
  display: grid;
  place-items: center;
  color: var(--muted);
  margin: 0 0 10px;
}

.loading-card,
.error-banner {
  padding: 14px;
  margin: 0 10px 10px;
}

.loading-card {
  border: 1px solid var(--panel-border);
  border-radius: 14px;
  background: var(--surface-soft);
}

.error-banner {
  border-radius: 10px;
  background: var(--banner-error-bg);
  color: var(--banner-error-text);
  border: 1px solid var(--banner-error-border);
}
</style>

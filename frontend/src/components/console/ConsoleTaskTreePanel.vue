<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import { getTeamTasks } from '../../api';
import { getTeamTasks as getGlobalTeamTasks, setTeamTasks as setGlobalTeamTasks } from '../../realtime/runtimeStore';
import type { AgentInfo, AgentTask, AgentTaskPriority, AgentTaskStatus } from '../../types';
import { displayName, formatTime } from '../../utils';
import AgentTaskDetailModal from '../agent/AgentTaskDetailModal.vue';
import { buildTaskForest } from '../../utils/taskTree';
import ConsoleTaskTreeNode from './ConsoleTaskTreeNode.vue';

const props = defineProps<{
  teamId: number;
  active: boolean;
  agents: AgentInfo[];
  refreshToken: number;
}>();

const { t } = useI18n();

const tasks = computed(() => getGlobalTeamTasks(props.teamId));
const loading = ref(false);
const errorMessage = ref('');
const hasLoaded = ref(false);
const taskFilter = ref<'all' | 'done' | 'undone' | 'cancelled'>('undone');
const selectedTask = ref<AgentTask | null>(null);

const FINISHED_STATUSES = new Set(['DONE', 'CANCELLED']);

const filteredTasks = computed(() => (
  taskFilter.value === 'all'
    ? tasks.value
    : taskFilter.value === 'cancelled'
      ? tasks.value.filter((task) => task.status === 'CANCELLED')
    : taskFilter.value === 'done'
      ? tasks.value.filter((task) => task.status === 'DONE')
      : tasks.value.filter((task) => !FINISHED_STATUSES.has(task.status))
));
const forest = computed(() => buildTaskForest(filteredTasks.value));
const rootTaskCount = computed(() => buildTaskForest(tasks.value).length);
const openTaskCount = computed(() => (
  tasks.value.filter((task) => task.status !== 'DONE' && task.status !== 'CANCELLED').length
));
const agentLabels = computed(() => new Map(
  props.agents
    .filter((agent) => typeof agent.id === 'number')
    .map((agent) => [agent.id as number, displayName(agent)]),
));

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
  return agentLabels.value.get(id) || `#${id}`;
}

function taskActorDetailLabel(id: number | null): string {
  if (typeof id !== 'number' || id <= 0) {
    return t('common.none');
  }
  return agentLabels.value.get(id) || `#${id}`;
}

function taskRoomDetailLabel(id: number | null): string {
  if (typeof id !== 'number' || id <= 0) {
    return t('common.none');
  }
  return `#${id}`;
}

function openTaskDetail(taskId: number): void {
  selectedTask.value = tasks.value.find((task) => task.id === taskId) ?? null;
}

function closeTaskDetail(): void {
  selectedTask.value = null;
}

async function loadTasks(force = false): Promise<void> {
  if (!props.active || (loading.value || (hasLoaded.value && !force))) {
    return;
  }

  loading.value = true;
  errorMessage.value = '';

  try {
    const data = await getTeamTasks(props.teamId, true, 500);
    setGlobalTeamTasks(props.teamId, data);
    hasLoaded.value = true;
  } catch (error) {
    errorMessage.value = t('console.tasksLoadFailed');
    console.error(error);
  } finally {
    loading.value = false;
  }
}

watch(
  () => [props.teamId, props.active] as const,
  ([, active], previous) => {
    if (!active) {
      return;
    }
    const previousActive = previous?.[1];
    if (previousActive !== active) {
      loadTasks().catch(console.error);
      return;
    }
    hasLoaded.value = false;
    loadTasks(true).catch(console.error);
  },
  { immediate: true },
);

watch(
  () => props.refreshToken,
  () => {
    hasLoaded.value = false;
    loadTasks(true).catch(console.error);
  },
);
</script>

<template>
  <section class="task-panel panel">
    <header class="task-panel__header">
      <h2>{{ t('console.taskListTitle') }}</h2>
      <div class="task-panel__filter">
        <button
          type="button"
          class="task-panel__filter-btn"
          :class="{ 'is-active': taskFilter === 'undone' }"
          @click="taskFilter = 'undone'"
        >{{ t('agent.taskFilterUndone') }}</button>
        <button
          type="button"
          class="task-panel__filter-btn"
          :class="{ 'is-active': taskFilter === 'done' }"
          @click="taskFilter = 'done'"
        >{{ t('agent.taskFilterDone') }}</button>
        <button
          type="button"
          class="task-panel__filter-btn"
          :class="{ 'is-active': taskFilter === 'cancelled' }"
          @click="taskFilter = 'cancelled'"
        >{{ t('agent.taskStatus.CANCELLED') }}</button>
        <button
          type="button"
          class="task-panel__filter-btn"
          :class="{ 'is-active': taskFilter === 'all' }"
          @click="taskFilter = 'all'"
        >{{ t('agent.taskFilterAll') }}</button>
      </div>
      <div class="task-panel__summary">
        <span>{{ t('console.taskSummary', { count: tasks.length }) }}</span>
        <span>{{ t('console.taskOpenCount', { count: openTaskCount }) }}</span>
        <span>{{ t('console.taskRoots', { count: rootTaskCount }) }}</span>
      </div>
    </header>

    <div v-if="errorMessage" class="error-banner">{{ errorMessage }}</div>
    <template v-else>
      <div v-if="loading && !tasks.length" class="loading-card">{{ t('console.loadingTasks') }}</div>
      <div v-else-if="!filteredTasks.length" class="task-panel__empty">
        {{
          taskFilter === 'undone'
            ? t('agent.noTasks')
            : taskFilter === 'done'
              ? t('agent.taskFilterNoTasksDone')
              : taskFilter === 'cancelled'
                ? t('console.noCancelledTasks')
                : t('agent.taskFilterNoTasksAll')
        }}
      </div>
      <div v-else class="task-panel__canvas-wrap sidebar-scroll">
      <div class="task-panel__canvas">
        <div v-for="root in forest" :key="root.task.id" class="task-panel__lane">
          <ConsoleTaskTreeNode
            :node="root"
            :agent-labels="agentLabels"
            @select-task="openTaskDetail"
          />
        </div>
      </div>
      </div>
    </template>
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
  </section>
</template>

<style scoped>
.task-panel {
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 16px;
  overflow: hidden;
}

.task-panel__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.task-panel__header h2 {
  margin: 0;
  flex: 0 0 auto;
  color: var(--text-primary);
  font-size: 22px;
}

.task-panel__summary {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}

.task-panel__summary span {
  display: inline-flex;
  align-items: center;
  padding: 7px 12px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--surface-soft) 76%, transparent);
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 600;
}

.task-panel__canvas-wrap {
  flex: 1 1 auto;
  min-height: 0;
  min-width: 0;
  overflow: auto;
}

.task-panel__filter {
  display: flex;
  flex-wrap: wrap;
  flex: 1 1 auto;
  gap: 6px;
  justify-content: center;
  padding: 0 8px;
}

.task-panel__filter-btn {
  display: inline-flex;
  align-items: center;
  min-height: 18px;
  padding: 0 12px;
  border-radius: 6px;
  border: 1px solid var(--panel-border);
  background: transparent;
  color: var(--text-secondary);
  font-size: 0.72rem;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease, border-color 0.15s ease;
}

.task-panel__filter-btn:hover {
  background: color-mix(in srgb, var(--surface-soft) 60%, transparent);
  color: var(--text-primary);
}

.task-panel__filter-btn.is-active {
  background: color-mix(in srgb, var(--interactive-selected) 22%, var(--surface-pill) 78%);
  border-color: color-mix(in srgb, var(--interactive-selected) 34%, var(--panel-border) 66%);
  color: var(--text-strong);
}

.task-panel__canvas {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: max-content;
  padding: 6px 6px 12px 4px;
}

.task-panel__lane {
  padding: 1px 0;
}

.task-panel__empty {
  display: grid;
  place-items: center;
  min-height: 220px;
  color: var(--text-secondary);
  font-size: 14px;
}

@media (max-width: 960px) {
  .task-panel {
    padding: 14px;
  }

  .task-panel__header {
    flex-direction: column;
    align-items: flex-start;
  }

  .task-panel__summary {
    justify-content: flex-start;
  }

  .task-panel__filter {
    justify-content: flex-start;
    padding: 0;
  }
}
</style>

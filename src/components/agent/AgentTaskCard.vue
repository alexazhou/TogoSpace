<script setup lang="ts">
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';
import type { AgentTask, AgentTaskPriority } from '../../types';

const props = withDefaults(defineProps<{
  task: AgentTask;
  assigneeLabel: string;
  managerLabel?: string | null;
  clickable?: boolean;
  variant?: 'list' | 'tree';
}>(), {
  managerLabel: null,
  clickable: false,
  variant: 'list',
});

const emit = defineEmits<{
  select: [task: AgentTask];
}>();

const { t } = useI18n();

const rootAttrs = computed(() => (
  props.clickable
    ? { role: 'button', tabindex: 0 }
    : {}
));

function taskPriorityLabel(priority: AgentTaskPriority): string {
  switch (priority) {
    case 'HIGH':
      return '高';
    case 'LOW':
      return '低';
    default:
      return '普通';
  }
}

function handleSelect(): void {
  if (!props.clickable) {
    return;
  }
  emit('select', props.task);
}

const isDone = computed(() => props.task.status === 'DONE');
const isCancelled = computed(() => props.task.status === 'CANCELLED');
</script>

<template>
  <article
    class="agent-task-card"
    :class="[`agent-task-card--${variant}`, { 'is-clickable': clickable }]"
    v-bind="rootAttrs"
    @click="handleSelect"
    @keydown.enter.prevent="handleSelect"
    @keydown.space.prevent="handleSelect"
  >
    <div class="agent-task-card__row">
      <div class="agent-task-card__title-wrap">
        <span class="agent-task-card__checkbox" :class="{ 'is-done': isDone }" aria-hidden="true">
          <span v-if="isDone" class="agent-task-card__checkmark">✓</span>
        </span>
        <h5 :class="{ 'is-cancelled': isCancelled }">{{ task.title || t('common.unknown') }}</h5>
      </div>
      <div class="agent-task-card__badges">
        <span class="agent-task-card__badge" :data-priority="task.priority">
          {{ taskPriorityLabel(task.priority) }}
        </span>
      </div>
    </div>
    <div class="agent-task-card__footer">
      <div class="agent-task-card__meta">
        <span>#{{ task.id }}</span>
        <span>{{ t('agent.taskAssignee', { id: assigneeLabel }) }}</span>
        <span v-if="managerLabel" class="agent-task-card__manager">{{ t('agent.taskManager', { id: managerLabel }) }}</span>
      </div>
      <div class="agent-task-card__status-badges">
        <span class="agent-task-card__badge" :data-status="task.status">
          {{ t('agent.taskStatus.' + task.status) }}
        </span>
      </div>
    </div>
  </article>
</template>

<style scoped>
.agent-task-card {
  border: 1px solid color-mix(in srgb, var(--panel-border) 82%, white 18%);
  border-radius: 14px;
  background: var(--surface-panel-muted);
  padding: 10px 14px 8px;
  display: flex;
  flex-direction: column;
  gap: 5px;
  transition:
    border-color 160ms ease,
    background 160ms ease,
    transform 160ms ease;
}

.agent-task-card.is-clickable {
  cursor: pointer;
}

.agent-task-card.is-clickable:hover,
.agent-task-card.is-clickable:focus-visible {
  border-color: var(--room-card-border-active);
  background: var(--interactive-selected);
  outline: none;
}

.agent-task-card--tree {
  width: 320px;
  min-height: 64px;
  justify-content: center;
}

.agent-task-card__row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.agent-task-card__title-wrap {
  min-width: 0;
  flex: 1;
  display: flex;
  align-items: center;
  gap: 8px;
}

.agent-task-card__checkbox {
  width: 16px;
  height: 16px;
  flex: 0 0 16px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid color-mix(in srgb, var(--panel-border-strong) 72%, transparent);
  border-radius: 4px;
  background: color-mix(in srgb, var(--surface-panel) 88%, var(--surface-soft) 12%);
  color: transparent;
}

.agent-task-card__checkbox.is-done {
  border-color: color-mix(in srgb, var(--good) 34%, var(--panel-border) 66%);
  background: color-mix(in srgb, var(--good) 14%, var(--surface-pill) 86%);
  color: var(--good);
}

.agent-task-card__checkmark {
  font-size: 11px;
  line-height: 1;
  font-weight: 800;
}

.agent-task-card__row h5 {
  margin: 0;
  min-width: 0;
  flex: 1;
  color: var(--text-strong);
  font-size: 0.88rem;
  line-height: 1.3;
  overflow: hidden;
  text-overflow: ellipsis;
}

.agent-task-card__row h5.is-cancelled {
  text-decoration: line-through;
  text-decoration-thickness: 1.5px;
  text-decoration-color: color-mix(in srgb, var(--text-secondary) 82%, transparent);
  color: var(--text-secondary);
}

.agent-task-card--list .agent-task-card__row h5 {
  white-space: nowrap;
}

.agent-task-card--tree .agent-task-card__row {
  align-items: center;
}

.agent-task-card--tree .agent-task-card__row h5 {
  white-space: nowrap;
  display: block;
}

.agent-task-card--tree .agent-task-card__badges {
  flex-wrap: nowrap;
}

.agent-task-card--tree .agent-task-card__meta {
  flex-wrap: nowrap;
  overflow: hidden;
  white-space: nowrap;
}

.agent-task-card--tree .agent-task-card__meta span {
  overflow: hidden;
  text-overflow: ellipsis;
}

.agent-task-card__footer {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 8px;
  margin-top: 1px;
}

.agent-task-card__meta {
  display: flex;
  flex-wrap: wrap;
  flex: 1;
  gap: 6px;
  color: var(--muted);
  font-size: 0.7rem;
  line-height: 1.15;
}

.agent-task-card__manager {
  margin-left: 4px;
}

.agent-task-card__badges {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 4px;
}

.agent-task-card__status-badges {
  display: flex;
  gap: 4px;
  align-items: center;
}

.agent-task-card__badge {
  display: inline-flex;
  align-items: center;
  min-height: 18px;
  padding: 0 8px;
  border: 1px solid color-mix(in srgb, var(--panel-border) 72%, transparent);
  border-radius: 6px;
  background: var(--surface-pill);
  color: var(--text-secondary);
  font-size: 0.66rem;
  font-weight: 500;
  white-space: nowrap;
}

.agent-task-card__badge[data-priority='HIGH'] {
  border-color: color-mix(in srgb, var(--danger) 26%, var(--panel-border) 74%);
  background: color-mix(in srgb, var(--danger) 10%, var(--surface-pill) 90%);
  color: var(--danger);
}

.agent-task-card__badge[data-priority='LOW'] {
  border-color: color-mix(in srgb, var(--text-secondary) 18%, var(--panel-border) 82%);
  background: color-mix(in srgb, var(--text-secondary) 7%, var(--surface-pill) 93%);
  color: var(--text-secondary);
}

.agent-task-card__badge[data-priority='NORMAL'] {
  border-color: color-mix(in srgb, var(--good) 24%, var(--panel-border) 76%);
  background: color-mix(in srgb, var(--good) 10%, var(--surface-pill) 90%);
  color: var(--good);
}

.agent-task-card__badge[data-status='DONE'] {
  border-color: color-mix(in srgb, var(--good) 26%, var(--panel-border) 74%);
  background: color-mix(in srgb, var(--good) 10%, var(--surface-pill) 90%);
  color: var(--good);
}

.agent-task-card__badge[data-status='CANCELLED'] {
  border-color: color-mix(in srgb, var(--text-secondary) 18%, var(--panel-border) 82%);
  background: color-mix(in srgb, var(--text-secondary) 7%, var(--surface-pill) 93%);
  color: var(--text-secondary);
}

.agent-task-card__badge[data-status='IN_PROGRESS'],
.agent-task-card__badge[data-status='REVIEWING'] {
  border-color: color-mix(in srgb, var(--interactive-selected) 30%, var(--panel-border) 70%);
  background: color-mix(in srgb, var(--interactive-selected) 14%, var(--surface-pill) 86%);
  color: var(--accent);
}

.agent-task-card__badge[data-status='PENDING'],
.agent-task-card__badge[data-status='ON_HOLD'] {
  border-color: color-mix(in srgb, var(--warn) 24%, var(--panel-border) 76%);
  background: color-mix(in srgb, var(--warn) 10%, var(--surface-pill) 90%);
  color: var(--warn);
}

@media (max-width: 960px) {
  .agent-task-card--tree {
    width: 280px;
    min-height: 56px;
  }
}
</style>

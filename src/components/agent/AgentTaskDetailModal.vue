<script setup lang="ts">
import { useI18n } from 'vue-i18n';
import type { AgentTask } from '../../types';

const props = defineProps<{
  task: AgentTask | null;
  createdAtLabel: string;
  creatorLabel: string;
  assigneeLabel: string;
  managerLabel: string;
  roomLabel: string;
  priorityLabel: string;
  statusLabel: string;
}>();

const emit = defineEmits<{
  close: [];
}>();

const { t } = useI18n();

function requestClose(): void {
  emit('close');
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="task"
      class="task-detail-overlay"
      @click.self="requestClose"
    >
      <section class="task-detail-modal">
        <div class="task-detail-modal__head">
          <div class="task-detail-modal__title-wrap">
            <p class="task-detail-modal__eyebrow">{{ t('agent.taskDetail') }}</p>
            <h4>{{ task.title || t('common.unknown') }}</h4>
          </div>
          <button
            type="button"
            class="task-detail-modal__close"
            :aria-label="t('common.close')"
            @click="requestClose"
          >
            ×
          </button>
        </div>

        <div class="task-detail-modal__badges">
          <span class="task-detail-modal__badge" :data-priority="task.priority">
            {{ priorityLabel }}
          </span>
          <span class="task-detail-modal__badge" :data-status="task.status">
            {{ statusLabel }}
          </span>
        </div>

        <dl class="task-detail-modal__grid">
          <div>
            <dt>{{ t('agent.taskId') }}</dt>
            <dd>#{{ task.id }}</dd>
          </div>
          <div>
            <dt>{{ t('agent.taskCreatedAtLabel') }}</dt>
            <dd>{{ createdAtLabel || t('common.notSet') }}</dd>
          </div>
          <div>
            <dt>{{ t('agent.taskCreatorLabel') }}</dt>
            <dd>{{ creatorLabel }}</dd>
          </div>
          <div>
            <dt>{{ t('agent.taskAssigneeLabel') }}</dt>
            <dd>{{ assigneeLabel }}</dd>
          </div>
          <div>
            <dt>{{ t('agent.taskManagerLabel') }}</dt>
            <dd>{{ managerLabel }}</dd>
          </div>
          <div>
            <dt>{{ t('agent.taskRoomLabel') }}</dt>
            <dd>{{ roomLabel }}</dd>
          </div>
        </dl>

        <div class="task-detail-modal__section">
          <p class="task-detail-modal__section-title">{{ t('agent.taskDescriptionLabel') }}</p>
          <p class="task-detail-modal__section-body">
            {{ task.description || t('agent.noTaskDescription') }}
          </p>
        </div>

        <div class="task-detail-modal__section">
          <p class="task-detail-modal__section-title">{{ t('agent.taskDependsOnLabel') }}</p>
          <p class="task-detail-modal__section-body">
            <template v-if="task.depends_on.length">
              {{ task.depends_on.map((id) => `#${id}`).join(', ') }}
            </template>
            <template v-else>
              {{ t('common.none') }}
            </template>
          </p>
        </div>

        <div v-if="task.result" class="task-detail-modal__section">
          <p class="task-detail-modal__section-title">{{ t('agent.taskResultLabel') }}</p>
          <p class="task-detail-modal__section-body">{{ task.result }}</p>
        </div>

        <div v-if="task.block_reason" class="task-detail-modal__section">
          <p class="task-detail-modal__section-title">{{ t('agent.taskBlockReasonLabel') }}</p>
          <p class="task-detail-modal__section-body">{{ task.block_reason }}</p>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.task-detail-overlay {
  position: fixed;
  inset: 0;
  z-index: 60;
  display: grid;
  place-items: center;
  padding: 28px;
  background: rgba(112, 133, 160, 0.16);
  backdrop-filter: blur(4px);
}

.task-detail-modal {
  width: min(640px, 100%);
  max-height: calc(100vh - 64px);
  overflow: auto;
  scrollbar-width: thin;
  scrollbar-color: var(--scrollbar-thumb) transparent;
  border-radius: 20px;
  border: 1px solid color-mix(in srgb, var(--panel-border) 82%, white 18%);
  background: color-mix(in srgb, var(--panel-bg) 96%, var(--surface-soft) 4%);
  box-shadow:
    0 18px 36px rgba(40, 67, 102, 0.16),
    inset 0 1px 0 rgba(255, 255, 255, 0.65);
  padding: 16px 18px 18px;
}

.task-detail-modal::-webkit-scrollbar {
  width: 10px;
}

.task-detail-modal::-webkit-scrollbar-track {
  background: transparent;
}

.task-detail-modal::-webkit-scrollbar-thumb {
  background: var(--scrollbar-thumb);
  border: 3px solid transparent;
  border-radius: 999px;
  background-clip: padding-box;
}

.task-detail-modal::-webkit-scrollbar-thumb:hover {
  background: var(--scrollbar-thumb-hover);
  border: 3px solid transparent;
  background-clip: padding-box;
}

.task-detail-modal__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.task-detail-modal__title-wrap h4 {
  margin: 0;
  color: var(--text-strong);
  font-size: 1rem;
  line-height: 1.35;
}

.task-detail-modal__eyebrow {
  margin: 0 0 4px;
  color: var(--accent);
  text-transform: uppercase;
  letter-spacing: 0.12em;
  font-size: 0.66rem;
}

.task-detail-modal__close {
  width: 28px;
  height: 28px;
  border: 0;
  border-radius: 999px;
  background: transparent;
  color: var(--muted);
  font-size: 1.1rem;
  line-height: 1;
  cursor: pointer;
}

.task-detail-modal__close:hover {
  background: color-mix(in srgb, var(--surface-soft) 90%, transparent);
  color: var(--text-strong);
}

.task-detail-modal__badges {
  margin-top: 12px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.task-detail-modal__badge {
  display: inline-flex;
  align-items: center;
  min-height: 18px;
  padding: 0 8px;
  border: 1px solid color-mix(in srgb, var(--panel-border) 72%, transparent);
  border-radius: 6px;
  background: var(--surface-pill);
  color: var(--text-secondary);
  font-size: 0.68rem;
  font-weight: 500;
  white-space: nowrap;
}

.task-detail-modal__badge[data-priority='HIGH'] {
  border-color: color-mix(in srgb, var(--danger) 26%, var(--panel-border) 74%);
  background: color-mix(in srgb, var(--danger) 10%, var(--surface-pill) 90%);
  color: var(--danger);
}

.task-detail-modal__badge[data-priority='LOW'] {
  border-color: color-mix(in srgb, var(--text-secondary) 18%, var(--panel-border) 82%);
  background: color-mix(in srgb, var(--text-secondary) 7%, var(--surface-pill) 93%);
  color: var(--text-secondary);
}

.task-detail-modal__badge[data-priority='NORMAL'] {
  border-color: color-mix(in srgb, var(--good) 24%, var(--panel-border) 76%);
  background: color-mix(in srgb, var(--good) 10%, var(--surface-pill) 90%);
  color: var(--good);
}

.task-detail-modal__badge[data-status='DONE'] {
  border-color: color-mix(in srgb, var(--good) 26%, var(--panel-border) 74%);
  background: color-mix(in srgb, var(--good) 10%, var(--surface-pill) 90%);
  color: var(--good);
}

.task-detail-modal__badge[data-status='CANCELLED'] {
  border-color: color-mix(in srgb, var(--text-secondary) 18%, var(--panel-border) 82%);
  background: color-mix(in srgb, var(--text-secondary) 7%, var(--surface-pill) 93%);
  color: var(--text-secondary);
}

.task-detail-modal__badge[data-status='IN_PROGRESS'],
.task-detail-modal__badge[data-status='REVIEWING'] {
  border-color: color-mix(in srgb, var(--interactive-selected) 30%, var(--panel-border) 70%);
  background: color-mix(in srgb, var(--interactive-selected) 14%, var(--surface-pill) 86%);
  color: var(--accent);
}

.task-detail-modal__badge[data-status='PENDING'],
.task-detail-modal__badge[data-status='ON_HOLD'] {
  border-color: color-mix(in srgb, var(--warn) 24%, var(--panel-border) 76%);
  background: color-mix(in srgb, var(--warn) 10%, var(--surface-pill) 90%);
  color: var(--warn);
}

.task-detail-modal__grid {
  margin: 14px 0 0;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px 14px;
}

.task-detail-modal__grid div {
  min-width: 0;
}

.task-detail-modal__grid dt {
  margin: 0 0 3px;
  color: var(--muted);
  font-size: 0.7rem;
}

.task-detail-modal__grid dd {
  margin: 0;
  color: var(--text-primary);
  font-size: 0.8rem;
  line-height: 1.4;
  word-break: break-word;
}

.task-detail-modal__section {
  margin-top: 14px;
}

.task-detail-modal__section-title {
  margin: 0 0 6px;
  color: var(--text-strong);
  font-size: 0.78rem;
  font-weight: 700;
}

.task-detail-modal__section-body {
  margin: 0;
  padding: 10px 12px;
  border-radius: 12px;
  background: color-mix(in srgb, var(--surface-soft) 84%, transparent);
  color: var(--text-primary);
  font-size: 0.8rem;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
}

@media (max-width: 720px) {
  .task-detail-overlay {
    padding: 12px;
  }

  .task-detail-modal {
    width: min(100vw - 24px, 100%);
    max-height: calc(100vh - 24px);
    padding: 14px;
  }

  .task-detail-modal__grid {
    grid-template-columns: 1fr;
  }
}
</style>

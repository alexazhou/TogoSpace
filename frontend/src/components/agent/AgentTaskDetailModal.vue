<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import { getTaskDetail, getTaskEventsPage } from '../../api';
import {
  appendTaskEvent,
  getTaskEvents,
  getTaskEventsTotal,
  prependTaskEvents,
  setTaskEvents,
} from '../../realtime/runtimeStore';
import { subscribeRealtimeEvents } from '../../realtime/wsClient';
import { connectionState } from '../../appUiState';
import type { AgentTask, AgentTaskDependencySummary, AgentTaskStatus } from '../../types';
import { formatBackendDateTime } from '../../utils';
import AgentTaskEventTimeline from './AgentTaskEventTimeline.vue';
import MarkdownContent from '../ui/MarkdownContent.vue';

const props = defineProps<{
  /** 选中任务 id；null 表示关闭。 */
  taskId: number | null;
  /** 列表里已有的任务快照（详情接口返回前先展示，避免弹窗空白）。 */
  task: AgentTask | null;
  creatorLabel: string;
  assigneeLabel: string;
  reviewerLabel: string;
  roomLabel: string;
  /** 时间线 actor 的展示名解析。 */
  actorLabel: (id: number) => string;
}>();

const emit = defineEmits<{
  close: [];
}>();

const { t } = useI18n();

const TIMELINE_PAGE_SIZE = 20;

const detail = ref<AgentTask | null>(null);
const detailLoading = ref(false);
const detailError = ref('');
const eventsLoading = ref(false);
const eventsLoadingMore = ref(false);
const eventsError = ref('');
let requestToken = 0;

/** 详情优先，回落到列表快照。 */
const view = computed<AgentTask | null>(() => detail.value ?? props.task);
const events = computed(() => getTaskEvents(props.taskId));
const eventsTotal = computed(() => getTaskEventsTotal(props.taskId));
const hasMoreEvents = computed(() => events.value.length < eventsTotal.value);

const priorityLabel = computed(() => (view.value ? t(`agent.taskPriority.${view.value.priority}`) : ''));
const statusLabel = computed(() => (view.value ? t(`agent.taskStatus.${view.value.status}`) : ''));
const createdAtLabel = computed(() => formatBackendDateTime(view.value?.created_at) || t('common.notSet'));
const roomDetailLabel = computed(() => {
  const roomId = view.value?.room_id;
  return typeof roomId === 'number' && roomId > 0 ? `#${roomId}` : t('common.none');
});
const dependencies = computed<AgentTaskDependencySummary[]>(() => view.value?.depends_on_details ?? []);

function dependencyLabel(dep: AgentTaskDependencySummary): string {
  const title = dep.title || `#${dep.task_id}`;
  if (dep.status === 'UNKNOWN') {
    return t('agent.taskDependencyDeleted', { title });
  }
  return t('agent.taskDependencyItem', {
    title,
    status: t(`agent.taskStatus.${dep.status}`),
  });
}

async function loadAll(id: number): Promise<void> {
  const token = ++requestToken;
  detailError.value = '';
  eventsError.value = '';
  detail.value = null;
  // 命中缓存则先渲染，避免每次打开都空白一下
  if (getTaskEvents(id).length > 0) {
    detailLoading.value = false;
    eventsLoading.value = false;
  } else {
    detailLoading.value = true;
    eventsLoading.value = true;
  }

  const [detailResult, eventsResult] = await Promise.allSettled([
    getTaskDetail(id),
    getTaskEventsPage(id, { limit: TIMELINE_PAGE_SIZE }),
  ]);
  if (token !== requestToken) {
    return;
  }

  if (detailResult.status === 'fulfilled') {
    detail.value = detailResult.value;
    // 详情接口自带时间线首屏，直接落缓存（比单独请求少一次往返的场景）
    if (Array.isArray(detailResult.value.events) && detailResult.value.events.length > 0) {
      setTaskEvents(id, detailResult.value.events, detailResult.value.events_total);
    }
  } else {
    detailError.value = t('agent.taskDetailLoadFailed');
    console.error(detailResult.reason);
  }

  if (eventsResult.status === 'fulfilled') {
    const page = eventsResult.value;
    // 首屏整份替换，但保留已由推送提前到达的事件（按 id 合并）
    setTaskEvents(id, page.events, page.total);
  } else {
    eventsError.value = t('agent.taskEventsLoadFailed');
    console.error(eventsResult.reason);
  }

  detailLoading.value = false;
  eventsLoading.value = false;
}

async function loadEarlierEvents(): Promise<void> {
  const id = props.taskId;
  if (id === null || eventsLoadingMore.value || !hasMoreEvents.value) {
    return;
  }
  const earliest = events.value[0];
  if (!earliest) {
    return;
  }

  eventsLoadingMore.value = true;
  try {
    const page = await getTaskEventsPage(id, {
      limit: TIMELINE_PAGE_SIZE,
      beforeId: earliest.id,
    });
    prependTaskEvents(id, page.events, page.total);
  } catch (error) {
    eventsError.value = t('agent.taskEventsLoadFailed');
    console.error(error);
  } finally {
    eventsLoadingMore.value = false;
  }
}

watch(
  () => props.taskId,
  (id) => {
    if (id === null) {
      requestToken += 1;
      detail.value = null;
      detailError.value = '';
      eventsError.value = '';
      return;
    }
    loadAll(id).catch(console.error);
  },
  { immediate: true },
);

// 方案 B：推送直达，无需再请求接口。
let unsubscribe: (() => void) | null = null;
watch(
  () => props.taskId,
  (id) => {
    unsubscribe?.();
    unsubscribe = null;
    if (id === null) {
      return;
    }
    unsubscribe = subscribeRealtimeEvents((event) => {
      if (event.type !== 'task_event_appended') {
        return;
      }
      if (event.task.id !== id) {
        return;
      }
      appendTaskEvent(id, event.event);
      // 推送带来的最新快照与当前详情不同步时，以推送为准更新状态等字段
      if (detail.value && event.task.updated_at !== detail.value.updated_at) {
        detail.value = { ...detail.value, ...event.task };
      }
    });
  },
  { immediate: true },
);

onBeforeUnmount(() => {
  unsubscribe?.();
  unsubscribe = null;
});

onBeforeUnmount(() => {
  unsubscribe?.();
  unsubscribe = null;
});

// 断线重连补偿：断开期间的推送不会补发，重连后重拉首屏并按 id 合并。
watch(
  () => connectionState.value,
  (state, previous) => {
    if (state !== 'connected' || previous === undefined || props.taskId === null) {
      return;
    }
    const id = props.taskId;
    const loaded = Math.max(events.value.length, TIMELINE_PAGE_SIZE);
    getTaskEventsPage(id, { limit: loaded })
      .then((page) => setTaskEvents(id, page.events, page.total))
      .catch((error) => console.error(error));
  },
);

function requestClose(): void {
  emit('close');
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="taskId !== null && view"
      class="task-detail-overlay"
      @click.self="requestClose"
    >
      <section class="task-detail-modal">
        <div class="task-detail-modal__head">
          <div class="task-detail-modal__title-wrap">
            <p class="task-detail-modal__eyebrow">{{ t('agent.taskDetail') }}</p>
            <h4>{{ view.title || t('common.unknown') }}</h4>
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
          <span class="task-detail-modal__badge" :data-priority="view.priority">
            {{ priorityLabel }}
          </span>
          <span class="task-detail-modal__badge" :data-status="view.status">
            {{ statusLabel }}
          </span>
        </div>

        <dl class="task-detail-modal__grid">
          <div>
            <dt>{{ t('agent.taskId') }}</dt>
            <dd>#{{ view.id }}</dd>
          </div>
          <div>
            <dt>{{ t('agent.taskCreatedAtLabel') }}</dt>
            <dd>{{ createdAtLabel }}</dd>
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
            <dt>{{ t('agent.taskReviewerLabel') }}</dt>
            <dd>{{ reviewerLabel }}</dd>
          </div>
          <div>
            <dt>{{ t('agent.taskRoomLabel') }}</dt>
            <dd>{{ roomDetailLabel }}</dd>
          </div>
        </dl>

        <div v-if="view.current_block_reason" class="task-detail-modal__section task-detail-modal__section--warn">
          <p class="task-detail-modal__section-title">{{ t('agent.taskBlockReasonLabel') }}</p>
          <p class="task-detail-modal__section-body">{{ view.current_block_reason }}</p>
        </div>

        <div class="task-detail-modal__section">
          <p class="task-detail-modal__section-title">{{ t('agent.taskDescriptionLabel') }}</p>
          <div class="task-detail-modal__section-body task-detail-modal__section-body--scroll">
            <MarkdownContent
              v-if="view.description"
              class="task-detail-modal__markdown"
              :content="view.description"
            />
            <template v-else>{{ t('agent.noTaskDescription') }}</template>
          </div>
        </div>

        <div class="task-detail-modal__section">
          <p class="task-detail-modal__section-title">{{ t('agent.taskDependsOnLabel') }}</p>
          <p class="task-detail-modal__section-body">
            <template v-if="dependencies.length">
              <span
                v-for="dep in dependencies"
                :key="dep.task_id"
                class="task-detail-modal__dep"
              >{{ dependencyLabel(dep) }}</span>
            </template>
            <template v-else-if="view.depends_on.length">
              {{ view.depends_on.map((id) => `#${id}`).join(', ') }}
            </template>
            <template v-else>
              {{ t('common.none') }}
            </template>
          </p>
        </div>

        <div v-if="detailError" class="task-detail-modal__hint">{{ detailError }}</div>

        <AgentTaskEventTimeline
          :events="events"
          :total="eventsTotal"
          :loading="eventsLoading || detailLoading"
          :loading-more="eventsLoadingMore"
          :has-more="hasMoreEvents"
          :error="eventsError"
          :actor-label="actorLabel"
          @load-more="loadEarlierEvents().catch(console.error)"
          @retry="taskId !== null && loadAll(taskId).catch(console.error)"
        />
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

.task-detail-modal__section--warn .task-detail-modal__section-body {
  background: color-mix(in srgb, var(--warn) 12%, var(--surface-soft) 88%);
  color: var(--warn);
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

/* 任务说明可能很长：限制高度，超出内部滚动，避免把弹窗撑得过高 */
.task-detail-modal__section-body--scroll {
  max-height: 180px;
  overflow-y: auto;
  scrollbar-width: thin;
  scrollbar-color: var(--scrollbar-thumb) transparent;
}

/* markdown 渲染后由标签自己控制换行，父级的 pre-wrap 会凭空多出空行 */
.task-detail-modal__markdown {
  white-space: normal;
}

/* 任务说明里的 markdown 段间距收紧（默认 0.7em 在弹窗里偏大） */
.task-detail-modal__markdown :deep(* + p),
.task-detail-modal__markdown :deep(* + blockquote),
.task-detail-modal__markdown :deep(* + ul),
.task-detail-modal__markdown :deep(* + ol),
.task-detail-modal__markdown :deep(* + .markdown-code-block),
.task-detail-modal__markdown :deep(* + table) {
  margin-top: 0.35em;
}

.task-detail-modal__markdown :deep(h1),
.task-detail-modal__markdown :deep(h2),
.task-detail-modal__markdown :deep(h3),
.task-detail-modal__markdown :deep(h4),
.task-detail-modal__markdown :deep(h5),
.task-detail-modal__markdown :deep(h6) {
  margin-top: 0.5em;
}

.task-detail-modal__markdown :deep(li + li) {
  margin-top: 0.15em;
}

.task-detail-modal__section-body--scroll::-webkit-scrollbar {
  width: 8px;
}

.task-detail-modal__section-body--scroll::-webkit-scrollbar-thumb {
  background: var(--scrollbar-thumb);
  border: 2px solid transparent;
  border-radius: 999px;
  background-clip: padding-box;
}

.task-detail-modal__dep {
  display: inline-block;
  margin-right: 8px;
}

.task-detail-modal__hint {
  margin-top: 12px;
  color: var(--muted);
  font-size: 0.72rem;
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

<script setup lang="ts">
import { computed, ref } from 'vue';
import { useI18n } from 'vue-i18n';
import type { AgentTaskEvent, AgentTaskEventKind, AgentTaskStatus } from '../../types';
import { MIGRATION_CONTENT_PREFIX } from '../../types';
import { formatBackendDateTime } from '../../utils';
import MarkdownContent from '../ui/MarkdownContent.vue';

const props = defineProps<{
  events: AgentTaskEvent[];
  total: number;
  loading?: boolean;
  loadingMore?: boolean;
  hasMore?: boolean;
  error?: string;
  /** 时间线里 actor 的展示名（父组件用自己的成员表解析）。 */
  actorLabel: (id: number) => string;
}>();

const emit = defineEmits<{
  'load-more': [];
  retry: [];
}>();

const { t } = useI18n();

/** 系统 / 操作者不是团队成员，在组件内拦截，不交给父组件解析。 */
const SYSTEM_ACTOR_ID = -2;
const OPERATOR_ACTOR_ID = -1;

function eventKind(event: AgentTaskEvent): AgentTaskEventKind {
  if (event.from_status !== null && event.to_status !== null) {
    return 'transition';
  }
  if (event.from_status === null && event.to_status === null) {
    return 'comment';
  }
  // (null, 非null)：创建 / 存量迁移快照
  return event.content.startsWith(MIGRATION_CONTENT_PREFIX) ? 'snapshot' : 'create';
}

function actorName(event: AgentTaskEvent): string {
  if (event.actor_id === SYSTEM_ACTOR_ID) {
    return t('agent.taskEventActorSystem');
  }
  if (event.actor_id === OPERATOR_ACTOR_ID) {
    return t('agent.taskEventActorOperator');
  }
  return props.actorLabel(event.actor_id);
}

function statusLabel(status: AgentTaskStatus): string {
  return t(`agent.taskStatus.${status}`);
}

/** 快照事件的 content 带迁移前缀，展示时剥掉（前缀只用于识别，不是给人看的）。 */
function eventContent(event: AgentTaskEvent, kind: AgentTaskEventKind): string {
  const content = event.content ?? '';
  if (kind === 'snapshot' && content.startsWith(MIGRATION_CONTENT_PREFIX)) {
    return content.slice(MIGRATION_CONTENT_PREFIX.length).trim();
  }
  return content;
}

/**
 * 单行摘要用的纯文本：markdown 语法在单行里不会渲染，先把标记压平成可读文本，
 * 避免摘要行出现 `**加粗**`、`` `code` ``, `- 列表` 这类原文符号。
 */
function stripMarkdown(markdown: string): string {
  return markdown
    .replace(/```[\s\S]*?```/g, ' [代码] ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/^\s{0,3}#{1,6}\s+/gm, '')
    .replace(/^\s{0,3}>\s?/gm, '')
    .replace(/^\s{0,3}[-*+]\s+/gm, '')
    .replace(/^\s{0,3}\d+\.\s+/gm, '')
    .replace(/(\*\*|__)(.*?)\1/g, '$2')
    .replace(/(\*|_)(.*?)\1/g, '$2')
    .replace(/~~(.*?)~~/g, '$1');
}

/** 摘要行用：压成单行（行内空白合并，换行也变空格）。 */
function plainExcerpt(markdown: string): string {
  return stripMarkdown(markdown).replace(/\s+/g, ' ').trim();
}

function eventExcerpt(event: AgentTaskEvent, kind: AgentTaskEventKind): string {
  return plainExcerpt(eventContent(event, kind));
}

/**
 * hover 提示用：**保留换行**（原生 `title` 会把 `\n` 渲染成换行），
 * 只合并行内多余空白、压掉连续空行，避免提示糊成一整段。
 */
function eventTooltip(event: AgentTaskEvent, kind: AgentTaskEventKind): string {
  return stripMarkdown(eventContent(event, kind))
    .replace(/[ \t]+/g, ' ')
    .replace(/ *\n */g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

const hasEvents = computed(() => props.events.length > 0);

/** 已展开 content 的事件 id；默认全部收起（每条只显示一行摘要）。 */
const expandedEventIds = ref<Set<number>>(new Set());

function isExpanded(eventId: number): boolean {
  return expandedEventIds.value.has(eventId);
}

function toggleExpanded(eventId: number): void {
  const next = new Set(expandedEventIds.value);
  if (next.has(eventId)) {
    next.delete(eventId);
  } else {
    next.add(eventId);
  }
  expandedEventIds.value = next;
}
</script>

<template>
  <section class="task-timeline">
    <header class="task-timeline__head">
      <p class="task-timeline__title">{{ t('agent.taskEventsLabel') }}</p>
      <span v-if="total > 0" class="task-timeline__total">
        {{ t('agent.taskEventsTotal', { count: total }) }}
      </span>
    </header>

    <div v-if="error" class="task-timeline__error">
      <span>{{ error }}</span>
      <button type="button" class="task-timeline__retry" @click="emit('retry')">
        {{ t('agent.taskEventsRetry') }}
      </button>
    </div>

    <div v-else-if="loading && !hasEvents" class="task-timeline__empty">
      {{ t('common.loading') }}
    </div>

    <div v-else-if="!hasEvents" class="task-timeline__empty">
      {{ t('agent.taskEventsEmpty') }}
    </div>

    <template v-else>
      <button
        v-if="hasMore"
        type="button"
        class="task-timeline__more"
        :disabled="loadingMore"
        @click="emit('load-more')"
      >
        {{ loadingMore ? t('agent.taskEventsLoadingMore') : t('agent.taskEventsLoadMore') }}
      </button>

      <ol class="task-timeline__list">
        <li
          v-for="event in events"
          :key="event.id"
          class="task-timeline__item"
          :data-kind="eventKind(event)"
        >
          <span class="task-timeline__dot" aria-hidden="true" />
          <div class="task-timeline__body">
            <!-- 摘要行：第一行 类型 · 时间 · 操作人 ·（流转的状态变化）；
                 第二行 content 摘要。整块可点击展开 -->
            <button
              type="button"
              class="task-timeline__summary"
              :class="{ 'is-expanded': isExpanded(event.id) }"
              :aria-expanded="isExpanded(event.id)"
              :title="eventTooltip(event, eventKind(event)) || undefined"
              @click="toggleExpanded(event.id)"
            >
              <span class="task-timeline__meta">
                <span class="task-timeline__actor">{{ actorName(event) }}</span>
                <span class="task-timeline__time">{{ formatBackendDateTime(event.created_at) }}</span>

                <!-- 事件说明放在时间之后：流转显示「旧状态 → 新状态」，其余显示事件类型 -->
                <template v-if="eventKind(event) === 'transition'">
                  <span class="task-timeline__status">{{ statusLabel(event.from_status!) }}</span>
                  <span class="task-timeline__arrow" aria-hidden="true">→</span>
                  <span class="task-timeline__status" :data-to="event.to_status">
                    {{ statusLabel(event.to_status!) }}
                  </span>
                </template>
                <span v-else class="task-timeline__kind">
                  {{ t(`agent.taskEventKind.${eventKind(event)}`) }}
                </span>

                <span class="task-timeline__chevron" aria-hidden="true">
                  {{ isExpanded(event.id) ? '▾' : '▸' }}
                </span>
              </span>

              <span
                v-if="eventExcerpt(event, eventKind(event))"
                class="task-timeline__excerpt"
              >{{ eventExcerpt(event, eventKind(event)) }}</span>
            </button>

            <!-- 展开区：markdown 渲染的完整正文，限制最大高度，超出内部滚动 -->
            <div v-if="isExpanded(event.id)" class="task-timeline__detail">
              <MarkdownContent
                v-if="eventContent(event, eventKind(event))"
                class="task-timeline__detail-text"
                :content="eventContent(event, eventKind(event))"
              />
            </div>
          </div>
        </li>
      </ol>
    </template>
  </section>
</template>

<style scoped>
.task-timeline {
  margin-top: 16px;
}

.task-timeline__head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 8px;
}

.task-timeline__title {
  margin: 0;
  color: var(--text-strong);
  font-size: 0.78rem;
  font-weight: 700;
}

.task-timeline__total {
  color: var(--muted);
  font-size: 0.7rem;
}

.task-timeline__empty {
  padding: 12px;
  border-radius: 12px;
  background: color-mix(in srgb, var(--surface-soft) 84%, transparent);
  color: var(--muted);
  font-size: 0.78rem;
}

.task-timeline__error {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 12px;
  background: var(--banner-error-bg);
  color: var(--banner-error-text);
  border: 1px solid var(--banner-error-border);
  font-size: 0.78rem;
}

.task-timeline__retry {
  margin-left: auto;
  padding: 2px 10px;
  border-radius: 6px;
  border: 1px solid currentColor;
  background: transparent;
  color: inherit;
  font-size: 0.72rem;
  cursor: pointer;
}

.task-timeline__more {
  display: block;
  width: 100%;
  margin-bottom: 10px;
  padding: 6px;
  border-radius: 8px;
  border: 1px dashed color-mix(in srgb, var(--panel-border) 88%, transparent);
  background: transparent;
  color: var(--text-secondary);
  font-size: 0.72rem;
  cursor: pointer;
}

.task-timeline__more:hover:not(:disabled) {
  background: color-mix(in srgb, var(--surface-soft) 70%, transparent);
  color: var(--text-primary);
}

.task-timeline__more:disabled {
  cursor: default;
  opacity: 0.6;
}

.task-timeline__list {
  margin: 0;
  padding: 0 0 0 4px;
  list-style: none;
}

.task-timeline__item {
  position: relative;
  display: flex;
  gap: 10px;
  padding: 0 0 12px 16px;
}

/* 竖线：最后一条不画 */
.task-timeline__item::before {
  content: '';
  position: absolute;
  left: 3px;
  top: 12px;
  bottom: 0;
  width: 1px;
  background: color-mix(in srgb, var(--panel-border) 82%, transparent);
}

.task-timeline__item:last-child::before {
  display: none;
}

.task-timeline__dot {
  position: absolute;
  left: 0;
  top: 5px;
  width: 7px;
  height: 7px;
  border-radius: 999px;
  background: var(--accent);
}

.task-timeline__item[data-kind='comment'] .task-timeline__dot {
  background: transparent;
  border: 1px solid var(--muted);
}

.task-timeline__item[data-kind='snapshot'] .task-timeline__dot {
  background: transparent;
  border: 1px dashed var(--muted);
}

.task-timeline__body {
  min-width: 0;
  flex: 1;
}

/* 摘要块：两行（meta 一行 + content 摘要一行），可点击展开。用 button 保证键盘可达。 */
.task-timeline__summary {
  width: 100%;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 2px;
  padding: 2px 4px;
  margin: -2px -4px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: inherit;
  font: inherit;
  font-size: 0.7rem;
  text-align: left;
  cursor: pointer;
  overflow: hidden;
}

/* meta 行：类型 · 时间 · 操作人 · 状态变化；本身也保持单行 */
.task-timeline__meta {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
}

.task-timeline__summary:hover {
  background: color-mix(in srgb, var(--surface-soft) 70%, transparent);
}

.task-timeline__summary:focus-visible {
  outline: 1px solid var(--interactive-selected);
  outline-offset: 1px;
}

.task-timeline__kind {
  flex: 0 0 auto;
  padding: 0 6px;
  border-radius: 5px;
  background: var(--surface-pill);
  color: var(--text-secondary);
}

.task-timeline__item[data-kind='snapshot'] .task-timeline__kind {
  color: var(--muted);
}

.task-timeline__time {
  flex: 0 0 auto;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}

.task-timeline__actor {
  flex: 0 0 auto;
  color: var(--text-primary);
  font-weight: 600;
}

.task-timeline__status {
  flex: 0 0 auto;
  padding: 0 6px;
  border-radius: 5px;
  border: 1px solid color-mix(in srgb, var(--panel-border) 72%, transparent);
  color: var(--text-secondary);
}

.task-timeline__arrow {
  flex: 0 0 auto;
  color: var(--muted);
}

/* 摘要里的 content：跟在操作人后面，占满剩余宽度，超出省略 */
/* content 摘要：单独一行，占满宽度，超出省略 */
.task-timeline__excerpt {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-secondary);
}

.task-timeline__chevron {
  flex: 0 0 auto;
  margin-left: auto;
  color: var(--muted);
  font-size: 0.62rem;
}

/* 展开区：限制最大高度，超出内部滚动 */
.task-timeline__detail {
  margin-top: 6px;
  max-height: 180px;
  overflow-y: auto;
  scrollbar-width: thin;
  scrollbar-color: var(--scrollbar-thumb) transparent;
  padding: 8px 10px;
  border-radius: 10px;
  border: 1px solid color-mix(in srgb, var(--panel-border) 70%, transparent);
  background: color-mix(in srgb, var(--surface-soft) 84%, transparent);
}

.task-timeline__detail::-webkit-scrollbar {
  width: 8px;
}

.task-timeline__detail::-webkit-scrollbar-thumb {
  background: var(--scrollbar-thumb);
  border: 2px solid transparent;
  border-radius: 999px;
  background-clip: padding-box;
}

.task-timeline__detail-text {
  color: var(--text-primary);
  font-size: 0.78rem;
  line-height: 1.55;
  word-break: break-word;
}

/* 展开区 markdown：段间距比全局默认收一半，紧凑文本块里 0.7em 太大 */
.task-timeline__detail-text :deep(* + p),
.task-timeline__detail-text :deep(* + blockquote),
.task-timeline__detail-text :deep(* + ul),
.task-timeline__detail-text :deep(* + ol),
.task-timeline__detail-text :deep(* + .markdown-code-block),
.task-timeline__detail-text :deep(* + table) {
  margin-top: 0.35em;
}

.task-timeline__detail-text :deep(h1),
.task-timeline__detail-text :deep(h2),
.task-timeline__detail-text :deep(h3),
.task-timeline__detail-text :deep(h4),
.task-timeline__detail-text :deep(h5),
.task-timeline__detail-text :deep(h6) {
  margin-top: 0.5em;
}

.task-timeline__detail-text :deep(li + li) {
  margin-top: 0.15em;
}
</style>

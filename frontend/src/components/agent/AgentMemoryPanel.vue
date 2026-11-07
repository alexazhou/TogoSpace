<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import { getAgentMemories } from '../../api';
import { subscribeRealtimeEvents } from '../../realtime/wsClient';
import { formatBackendDateTime } from '../../utils';
import type { AgentMemory } from '../../types';

/**
 * Agent 个人卡片「记忆」Tab —— **纯只读**查阅面板。
 *
 * 后端只提供查询接口（`GET /agents/<id>/memories.json`），记忆的增删改一律由
 * Agent 在对话中自治完成（step1 §2.2 已定），所以这里刻意没有新建/编辑/删除按钮。
 *
 * 星标统计口径：固定取**该 Agent 全部 ACTIVE 记忆**的占用数，不受 tab 筛选影响。
 * 否则切到「已归档」时星标数会变成 0，看起来像额度被清空了。
 */
const props = defineProps<{
  open: boolean;
  agentId: number | null;
}>();

const emit = defineEmits<{
  'count-change': [count: number];
}>();

const { t } = useI18n();

type MemoryFilter = 'active' | 'archived' | 'all';

const memories = ref<AgentMemory[]>([]);
const loading = ref(false);
const errorMessage = ref('');
const filter = ref<MemoryFilter>('active');
/** 展开正文的记忆 id 集合（卡片内原地展开，不弹二级窗口）。 */
const expandedIds = ref<Set<number>>(new Set());
let requestToken = 0;

const STAR_TOTAL_LIMIT = 12;
const STAR_SUMMARY_LIMIT = 6;

/**
 * 前端也按「活跃」过滤统计，而不是简单数 `star_level !== 'none'`：
 * 归档记忆虽然后端已经强制剥离星标，但历史数据或异常路径下仍可能残留星标值，
 * 这里按业务口径（归档不占额度）再兜一层，避免统计数字与后端配额判断打架。
 */
const starredStats = computed(() => {
  const active = memories.value.filter((memory) => memory.status === 'active');
  const summary = active.filter((memory) => memory.star_level === 'summary').length;
  const title = active.filter((memory) => memory.star_level === 'title').length;
  return { total: summary + title, summary };
});

const starUsageLabel = computed(() => t('agent.memoryStarUsage', {
  total: starredStats.value.total,
  totalLimit: STAR_TOTAL_LIMIT,
  summary: starredStats.value.summary,
  summaryLimit: STAR_SUMMARY_LIMIT,
}));

const activeCount = computed(() => memories.value.filter((memory) => memory.status === 'active').length);
const archivedCount = computed(() => memories.value.filter((memory) => memory.status === 'archived').length);

const visibleMemories = computed(() => {
  if (filter.value === 'archived') {
    return memories.value.filter((memory) => memory.status === 'archived');
  }
  if (filter.value === 'active') {
    return memories.value.filter((memory) => memory.status === 'active');
  }
  return memories.value;
});

const memoryCountLabel = computed(() => t('agent.memoryCount', { count: visibleMemories.value.length }));

/** 无记忆时的提示文案随筛选变化，避免「全部」筛到空列表却说「暂无长期记忆」。 */
const emptyLabel = computed(() => t('agent.noMemories'));

watch(
  () => [visibleMemories.value.length] as const,
  () => {
    emit('count-change', visibleMemories.value.length);
  },
  { immediate: true },
);

async function loadMemories(): Promise<void> {
  if (!props.open || props.agentId === null) {
    memories.value = [];
    errorMessage.value = '';
    loading.value = false;
    expandedIds.value = new Set();
    emit('count-change', 0);
    return;
  }

  const token = ++requestToken;
  loading.value = true;
  errorMessage.value = '';

  try {
    // 一次拉全量（含归档）在前端筛：切 tab 不重发请求，也不会出现切换瞬间的空窗。
    const next = await getAgentMemories(props.agentId, true);
    if (token !== requestToken) {
      return;
    }
    memories.value = next;
  } catch (error) {
    if (token !== requestToken) {
      return;
    }
    errorMessage.value = t('agent.memoryLoadFailed');
    console.error(error);
  } finally {
    if (token === requestToken) {
      loading.value = false;
    }
  }
}

watch(
  () => [props.open, props.agentId] as const,
  () => {
    // `agentId` 变化时清空展开状态：id 是对应旧 Agent 的，留着会误展开新 Agent 的同 id 记忆。
    expandedIds.value = new Set();
    loadMemories().catch(console.error);
  },
  { immediate: true },
);

/**
 * 跟随 WS 推送重拉：Agent 在对话里调 memory_create / memory_update 后，
 * 后端只推一条「某某的记忆变了」，列表数据仍需自己再取一次。
 *
 * 面板是 `v-show` 不销毁、弹窗开着时上面的 watch 不会触发，所以必须在这里
 * 单独订阅——否则用户会看到旧列表，只能关掉弹窗重开才刷新（原本的缺陷）。
 * 只认本面板对应的 agent，别的 Agent 变更不打扰这次请求。
 */
let unsubscribeMemoryEvents: (() => void) | null = null;
watch(
  () => [props.open, props.agentId] as const,
  ([open, agentId]) => {
    unsubscribeMemoryEvents?.();
    unsubscribeMemoryEvents = null;
    if (!open || agentId === null) {
      return;
    }
    unsubscribeMemoryEvents = subscribeRealtimeEvents((event) => {
      if (event.type !== 'agent_memory_changed' || event.agentId !== agentId) {
        return;
      }
      loadMemories().catch(console.error);
    });
  },
  { immediate: true },
);

onBeforeUnmount(() => {
  unsubscribeMemoryEvents?.();
  unsubscribeMemoryEvents = null;
});

function toggleExpanded(id: number): void {
  const next = new Set(expandedIds.value);
  if (next.has(id)) {
    next.delete(id);
  } else {
    next.add(id);
  }
  expandedIds.value = next;
}

function isExpanded(id: number): boolean {
  return expandedIds.value.has(id);
}

function starBadgeLabel(memory: AgentMemory): string {
  if (memory.star_level === 'summary') {
    return t('agent.memoryStarSummary');
  }
  if (memory.star_level === 'title') {
    return t('agent.memoryStarTitle');
  }
  return '';
}

function updatedAtLabel(memory: AgentMemory): string {
  const formatted = formatBackendDateTime(memory.updated_at);
  return formatted ? t('agent.memoryUpdatedAt', { time: formatted }) : '';
}

function setFilter(next: MemoryFilter): void {
  filter.value = next;
}
</script>

<template>
  <div class="agent-memory-panel-body">
    <div class="agent-memory-toolbar">
      <div class="agent-memory-filter">
        <button
          type="button"
          class="agent-memory-filter__btn"
          :class="{ 'is-active': filter === 'active' }"
          @click="setFilter('active')"
        >{{ t('agent.memoryFilterActive') }} ({{ activeCount }})</button>
        <button
          type="button"
          class="agent-memory-filter__btn"
          :class="{ 'is-active': filter === 'archived' }"
          @click="setFilter('archived')"
        >{{ t('agent.memoryFilterArchived') }} ({{ archivedCount }})</button>
        <button
          type="button"
          class="agent-memory-filter__btn"
          :class="{ 'is-active': filter === 'all' }"
          @click="setFilter('all')"
        >{{ t('agent.memoryFilterAll') }} ({{ memories.length }})</button>
      </div>
      <span class="agent-memory-stars" :data-full="starredStats.total >= STAR_TOTAL_LIMIT">
        {{ starUsageLabel }}
      </span>
    </div>

    <div v-if="errorMessage" class="error-banner">{{ errorMessage }}</div>
    <div v-else-if="loading && !memories.length" class="loading-card">{{ t('agent.loadingMemories') }}</div>
    <div v-else-if="!loading && !visibleMemories.length" class="agent-activity-empty">
      {{ emptyLabel }}
    </div>
    <div v-else class="agent-memory-list sidebar-scroll">
      <article
        v-for="memory in visibleMemories"
        :key="memory.id"
        class="agent-memory-card"
        :class="{
          'is-archived': memory.status === 'archived',
          'is-starred': memory.star_level !== 'none',
        }"
        :data-memory-id="memory.id"
        :data-star-level="memory.star_level"
        :data-status="memory.status"
      >
        <header class="agent-memory-card__head" @click="toggleExpanded(memory.id)">
          <span v-if="starBadgeLabel(memory)" class="agent-memory-card__star">
            {{ starBadgeLabel(memory) }}
          </span>
          <span class="agent-memory-card__title">{{ memory.title }}</span>
          <span v-if="memory.status === 'archived'" class="agent-memory-card__archived">
            {{ t('agent.memoryArchived') }}
          </span>
          <span class="agent-memory-card__meta">
            <span class="agent-memory-card__id">#{{ memory.id }}</span>
            <span v-if="updatedAtLabel(memory)" class="agent-memory-card__time">{{ updatedAtLabel(memory) }}</span>
          </span>
        </header>

        <p class="agent-memory-card__summary">{{ memory.summary }}</p>

        <div class="agent-memory-card__tags">
          <template v-if="memory.tags.length">
            <span v-for="tag in memory.tags" :key="tag" class="agent-memory-card__tag">{{ tag }}</span>
          </template>
          <button
            type="button"
            class="agent-memory-card__toggle"
            :aria-expanded="isExpanded(memory.id)"
            @click="toggleExpanded(memory.id)"
          >
            {{ isExpanded(memory.id) ? t('agent.memoryCollapse') : t('agent.memoryExpand') }}
          </button>
        </div>

        <pre v-if="isExpanded(memory.id)" class="agent-memory-card__content">{{ memory.content }}</pre>
      </article>
    </div>
  </div>
</template>

<style scoped>
.agent-memory-panel-body {
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: 100%;
}

.agent-memory-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-shrink: 0;
  padding: 8px 12px 4px;
}

.agent-memory-filter {
  display: flex;
  gap: 6px;
}

.agent-memory-filter__btn {
  padding: 3px 10px;
  border-radius: 99px;
  border: 1px solid var(--panel-border);
  background: transparent;
  color: var(--text-secondary);
  font-size: 12px;
  cursor: pointer;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}

.agent-memory-filter__btn:hover {
  background: color-mix(in srgb, var(--surface-soft) 60%, transparent);
  color: var(--text-primary);
}

.agent-memory-filter__btn.is-active {
  background: color-mix(in srgb, var(--interactive-selected) 22%, var(--surface-pill) 78%);
  border-color: color-mix(in srgb, var(--interactive-selected) 34%, var(--panel-border) 66%);
  color: var(--text-strong);
}

.agent-memory-stars {
  font-size: 12px;
  color: var(--text-secondary);
  white-space: nowrap;
}

.agent-memory-stars[data-full='true'] {
  color: var(--banner-error-text);
}

.agent-memory-list {
  flex: 1;
  min-height: 0;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px 0;
}

.agent-memory-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px 12px;
  border: 1px solid var(--panel-border);
  border-radius: 12px;
  background: var(--surface-soft);
}

.agent-memory-card.is-starred {
  border-color: color-mix(in srgb, var(--accent) 32%, var(--panel-border) 68%);
}

.agent-memory-card.is-archived {
  opacity: 0.78;
}

.agent-memory-card__head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  cursor: pointer;
}

.agent-memory-card__star {
  flex-shrink: 0;
  padding: 1px 8px;
  border-radius: 99px;
  font-size: 11px;
  background: color-mix(in srgb, var(--accent) 18%, var(--surface-pill) 82%);
  color: var(--text-strong);
}

.agent-memory-card__title {
  font-weight: 600;
  color: var(--text-strong);
  word-break: break-word;
}

.agent-memory-card__archived {
  flex-shrink: 0;
  padding: 1px 8px;
  border-radius: 99px;
  font-size: 11px;
  border: 1px solid var(--panel-border);
  color: var(--text-secondary);
}

.agent-memory-card__meta {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
  color: var(--muted);
  white-space: nowrap;
}

.agent-memory-card__summary {
  margin: 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--text-secondary);
  word-break: break-word;
}

.agent-memory-card__tags {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  font-size: 11px;
  color: var(--muted);
}

.agent-memory-card__tag {
  padding: 1px 8px;
  border-radius: 99px;
  border: 1px solid var(--panel-border);
}

/* 与标签同一行、贴右：`margin-left: auto` 吃掉标签行剩余空间；
   无标签时这一行只剩按钮，仍然靠右（模板里该行常驻渲染）。 */
.agent-memory-card__toggle {
  margin-left: auto;
  flex-shrink: 0;
  padding: 2px 8px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--text-secondary);
  font-size: 12px;
  cursor: pointer;
}

.agent-memory-card__toggle:hover {
  background: color-mix(in srgb, var(--surface-pill) 70%, transparent);
  color: var(--text-primary);
}

/* 保留换行与空格：正文常含步骤列表 / 代码块形态，按普通文本折叠会读不出结构。 */
.agent-memory-card__content {
  margin: 0;
  padding: 8px 10px;
  border-radius: 10px;
  background: var(--surface-pill);
  color: var(--text-primary);
  font-family: inherit;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 320px;
  overflow: auto;
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

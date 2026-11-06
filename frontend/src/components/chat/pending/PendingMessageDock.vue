<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import PendingMessageBar from './PendingMessageBar.vue';
import type { PendingMessageItem } from './types';

/**
 * 待读消息浮层：聊天页底部与 Agent 活动面板底部共用。
 *
 * 三层限高里这一层负责「② 同一时间只展开一条」与「③ 整体限高 / 折叠」：
 * - 条数超过 `collapseThreshold`（默认 3）时，收起态只显示最新 `visibleWhenCollapsed`
 *   （默认 2）条，上方给一条「还有 N 条待读消息」的细栏；点开显示全部。
 * - 浮层本体 `max-height: min(45%, 320px)`，超出在浮层内部滚动，保证消息区至少露出一半。
 * - 展开状态由这里统一持有：卡片自己只管「我这条要不要展开」，互斥由父级保证。
 *
 * 组件不判断「哪些消息待读」——那是调用方用 `readCursor.ts` 算好再传进来的。
 */
const props = withDefaults(defineProps<{
  items: PendingMessageItem[];
  /** 超过该数量才折叠。 */
  collapseThreshold?: number;
  /** 折叠时显示最新几条。 */
  visibleWhenCollapsed?: number;
  /** 是否显示「立即引导」按钮。 */
  canEscalate?: boolean;
  /** 正在引导中的 db_id。 */
  escalatingIds?: number[];
  /** 是否 sticky 贴底。活动面板与聊天页都贴底，默认 true。 */
  sticky?: boolean;
}>(), {
  collapseThreshold: 3,
  visibleWhenCollapsed: 2,
  canEscalate: true,
  escalatingIds: () => [],
  sticky: true,
});

const emit = defineEmits<{
  escalate: [dbId: number];
  clickSender: [senderId: number];
}>();

const { t } = useI18n();

/** 当前展开的那条：同一时间只允许一条。 */
const expandedKey = ref<string | null>(null);
/** 折叠栏是否被用户点开。 */
const listExpanded = ref(false);

const collapsible = computed(() => props.items.length > props.collapseThreshold);

const hiddenCount = computed(() => {
  if (!collapsible.value || listExpanded.value) {
    return 0;
  }
  return Math.max(props.items.length - props.visibleWhenCollapsed, 0);
});

/**
 * 实际渲染的条目：折叠态取**最新**的 `visibleWhenCollapsed` 条。
 *
 * 取最新的而不是最早的：待读区要解决的是「我刚发的消息去哪了」，最旧的那几条本来
 * 就快被读走了，藏最新的反而藏掉用户最关心的。
 */
const visibleItems = computed(() => {
  if (!collapsible.value || listExpanded.value) {
    return props.items;
  }
  return props.items.slice(-props.visibleWhenCollapsed);
});

function isEscalating(item: PendingMessageItem): boolean {
  const dbId = item.message.db_id;
  if (dbId === null || dbId === undefined) {
    return false;
  }
  return props.escalatingIds.includes(dbId);
}

function handleToggle(item: PendingMessageItem): void {
  expandedKey.value = expandedKey.value === item.key ? null : item.key;
}

function handleEscalate(item: PendingMessageItem): void {
  const dbId = item.message.db_id;
  if (dbId === null || dbId === undefined) {
    return;
  }
  emit('escalate', dbId);
}

function handleClickSender(item: PendingMessageItem): void {
  emit('clickSender', item.message.sender_id);
}

function toggleListExpanded(): void {
  listExpanded.value = !listExpanded.value;
}

/**
 * 被展开的那条从 `items` 里消失（Agent 读走了）时清空展开状态。
 * 不清的话 `expandedKey` 会一直指向不存在的 key，下一次同 key 复用时会被意外展开。
 */
watch(
  () => props.items.map((item) => item.key).join('|'),
  () => {
    if (expandedKey.value !== null && !props.items.some((item) => item.key === expandedKey.value)) {
      expandedKey.value = null;
    }
    if (!collapsible.value) {
      listExpanded.value = false;
    }
  },
);

/** 全部读走后把折叠状态复位，避免下批消息直接以「展开全部」的样子出现。 */
watch(
  () => props.items.length,
  (length) => {
    if (length === 0) {
      expandedKey.value = null;
      listExpanded.value = false;
    }
  },
);
</script>

<template>
  <div
    v-if="items.length"
    class="pending-message-dock"
    :class="{ 'pending-message-dock--sticky': sticky }"
  >
    <button
      v-if="collapsible && !listExpanded"
      type="button"
      class="pending-message-dock__collapsed-bar"
      :aria-expanded="false"
      @click="toggleListExpanded"
    >
      {{ t('chat.pendingMore', { count: hiddenCount }) }} ▾
    </button>
    <button
      v-else-if="collapsible && listExpanded"
      type="button"
      class="pending-message-dock__collapsed-bar"
      :aria-expanded="true"
      @click="toggleListExpanded"
    >
      {{ t('chat.pendingCollapse') }} ▴
    </button>
    <div class="pending-message-dock__list sidebar-scroll">
      <PendingMessageBar
        v-for="item in visibleItems"
        :key="item.key"
        :item="item"
        :expanded="expandedKey === item.key"
        :can-escalate="canEscalate"
        :escalating="isEscalating(item)"
        @toggle="handleToggle(item)"
        @escalate="handleEscalate(item)"
        @click-sender="handleClickSender(item)"
      />
    </div>
  </div>
</template>

<style scoped>
.pending-message-dock {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px 0 2px;
  box-sizing: border-box;
  /* ③ 整体限高：至少给消息区留一半。父容器无确定高度时 45% 会退化，
     此时 320px 兜底；两者取小。 */
  max-height: min(45%, 320px);
}

.pending-message-dock--sticky {
  position: sticky;
  bottom: 0;
  z-index: 2;
  margin-top: auto;
}

.pending-message-dock__list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-height: 0;
  overflow-y: auto;
  overflow-x: visible;
}

.pending-message-dock__collapsed-bar {
  align-self: stretch;
  padding: 3px 10px;
  border: none;
  border-radius: 999px;
  background: color-mix(in srgb, var(--surface-panel-muted) 66%, transparent);
  color: var(--text-secondary);
  font-size: 0.72rem;
  font-weight: 600;
  text-align: center;
  cursor: pointer;
}

.pending-message-dock__collapsed-bar:hover {
  color: var(--text-primary);
}

:global(html.bp-compact) .pending-message-dock {
  gap: 4px;
  padding-top: 8px;
}
</style>

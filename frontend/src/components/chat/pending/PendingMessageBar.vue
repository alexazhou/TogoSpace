<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, useTemplateRef, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import { renderMarkdownPreviewText } from '../../../utils/markdown';
import MarkdownContent from '../../ui/MarkdownContent.vue';
import type { PendingMessageItem } from './types';

/**
 * 单条待读消息卡片。
 *
 * 收起态固定 1 行：「提醒有哪些消息还在等 Agent 读」，全文等 Agent 读到后自然以完整
 * 气泡进入消息流，所以这里不需要展开式预览。只有**真的被截断**时才允许点开（没截断的
 * 短消息点了也没内容可看，反而误导）。
 *
 * 截断检测用 `scrollWidth > clientWidth`：`text-overflow: ellipsis` 本身不提供「是否
 * 省略」的观测手段，只能量几何。窗口宽度变化用 `ResizeObserver` 重算，否则窄窗转宽后
 * 卡片会一直保持「可点击」的假状态。
 */
const props = defineProps<{
  item: PendingMessageItem;
  expanded: boolean;
  canEscalate: boolean;
  escalating: boolean;
}>();

const emit = defineEmits<{
  toggle: [];
  escalate: [];
  clickSender: [];
}>();

const { t } = useI18n();

const previewRef = useTemplateRef<HTMLElement>('previewRef');
const truncated = ref(false);
let resizeObserver: ResizeObserver | null = null;

/**
 * 收起态文案：所有换行与连续空白合并成一个空格。
 * 不做这一步的话，多行消息在单行容器里会在换行处直接被切断，看起来像内容缺失。
 */
const previewText = computed(() => renderMarkdownPreviewText(props.item.message.content).replace(/\s+/g, ' ').trim());

const canToggle = computed(() => truncated.value && !props.expanded);

const statusLabel = computed(() => (
  props.item.status === 'pending-immediate'
    ? t('chat.pendingImmediateLabel')
    : t('chat.queuedMessageLabel')
));

const statusTooltip = computed(() => (
  props.item.status === 'pending-immediate'
    ? t('chat.pendingImmediateTip')
    : t('chat.queuedMessageTip')
));

const isImmediate = computed(() => props.item.status === 'pending-immediate');

/** 「立即引导」只对排队消息有意义：加急消息本来就会被尽快注入。 */
const showEscalate = computed(
  () => props.canEscalate
    && !isImmediate.value
    && props.item.message.db_id !== null
    && props.item.message.db_id !== undefined,
);

function measureTruncation(): void {
  const el = previewRef.value;
  if (!el) {
    truncated.value = false;
    return;
  }
  truncated.value = el.scrollWidth - el.clientWidth > 1;
}

function handleToggle(): void {
  if (!truncated.value) {
    return;
  }
  emit('toggle');
}

function handleKeydown(event: KeyboardEvent): void {
  if (!truncated.value) {
    return;
  }
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault();
    emit('toggle');
    return;
  }
  if (event.key === 'Escape' && props.expanded) {
    event.preventDefault();
    emit('toggle');
  }
}

function handleEscalate(event: MouseEvent): void {
  event.stopPropagation();
  if (props.escalating) {
    return;
  }
  emit('escalate');
}

function handleSenderClick(event: MouseEvent): void {
  event.stopPropagation();
  if (!props.item.sender.clickable) {
    return;
  }
  emit('clickSender');
}

watch(
  () => [previewRef.value, previewText.value] as const,
  ([el]) => {
    resizeObserver?.disconnect();
    resizeObserver = null;
    if (!el || typeof ResizeObserver === 'undefined') {
      // 无 ResizeObserver 的环境（部分测试宿主）退化为「挂载时量一次」。
      measureTruncation();
      return;
    }
    resizeObserver = new ResizeObserver(() => measureTruncation());
    resizeObserver.observe(el);
    measureTruncation();
  },
  { flush: 'post', immediate: true },
);

watch(
  () => props.expanded,
  (expanded) => {
    // 展开后预览行仍在（作为标题行），收起回来时需要重新判断它是否还被截断。
    if (!expanded) {
      nextTick(measureTruncation);
    }
  },
);

onBeforeUnmount(() => {
  resizeObserver?.disconnect();
  resizeObserver = null;
});
</script>

<template>
  <div
    class="pending-message-bar"
    :class="{
      'pending-message-bar--expandable': truncated,
      'pending-message-bar--expanded': expanded,
    }"
    :role="truncated ? 'button' : undefined"
    :tabindex="truncated ? 0 : undefined"
    :aria-expanded="truncated ? expanded : undefined"
    @click="handleToggle"
    @keydown="handleKeydown"
  >
    <img
      v-if="item.sender.avatarUrl"
      class="pending-message-avatar"
      :class="{ 'pending-message-avatar--clickable': item.sender.clickable }"
      :src="item.sender.avatarUrl"
      :alt="`${item.sender.displayName} avatar`"
      @click="handleSenderClick"
    />
    <div class="pending-message-body">
      <div ref="previewRef" class="pending-message-content">{{ previewText }}</div>
      <div v-if="expanded" class="pending-message-full">
        <MarkdownContent :content="item.message.content" />
      </div>
    </div>
    <span
      v-if="isImmediate"
      class="pending-message-status pending-message-status--pending-immediate"
      :data-tooltip="statusTooltip"
    >⚡ {{ statusLabel }}</span>
    <span
      v-else
      class="pending-message-status pending-message-status--queued"
      :data-tooltip="statusTooltip"
    >⏳ {{ statusLabel }}</span>
    <button
      v-if="showEscalate"
      type="button"
      class="pending-message-action"
      :disabled="escalating"
      @click="handleEscalate"
    >
      {{ escalating ? t('chat.guidingNow') : t('chat.guideNow') }}
    </button>
    <span v-if="truncated" class="pending-message-chevron" aria-hidden="true">{{ expanded ? '▾' : '▸' }}</span>
  </div>
</template>

<style scoped>
.pending-message-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 12px;
  border: 1px solid color-mix(in srgb, var(--interactive-focus-border) 14%, var(--border-default) 86%);
  border-radius: 14px;
  background: var(--chat-floating-message-bg);
  box-sizing: border-box;
}

.pending-message-bar--expandable {
  cursor: pointer;
}

.pending-message-avatar {
  width: 28px;
  height: 28px;
  border-radius: 9px;
  object-fit: cover;
  flex-shrink: 0;
  border: 1px solid color-mix(in srgb, var(--border-strong) 28%, transparent);
  background: color-mix(in srgb, var(--surface-elevated) 84%, var(--border-default) 16%);
}

.pending-message-avatar--clickable {
  cursor: pointer;
}

/* 收起态固定单行：容器只分配一行高度，超出的用省略号。 */
.pending-message-body {
  min-width: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.pending-message-content {
  min-width: 0;
  color: var(--text-primary);
  font-size: 0.84rem;
  line-height: 1.35;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 展开后的全文：限高在卡片内部滚动，避免单条把整屏撑爆。 */
.pending-message-full {
  max-height: 40vh;
  overflow: auto;
  padding-top: 4px;
  border-top: 1px dashed color-mix(in srgb, var(--border-default) 70%, transparent);
}

.pending-message-chevron {
  flex-shrink: 0;
  color: var(--text-secondary);
  font-size: 0.7rem;
  line-height: 1;
}

.pending-message-status {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  min-height: 20px;
  padding: 0 7px;
  border-radius: 999px;
  font-size: 0.7rem;
  font-weight: 600;
  letter-spacing: 0.01em;
  white-space: nowrap;
}

.pending-message-status--queued {
  color: var(--text-secondary);
  background: color-mix(in srgb, var(--surface-panel-muted) 78%, var(--surface-panel) 22%);
}

.pending-message-status--pending-immediate {
  color: #b66a00;
  background: color-mix(in srgb, #f59e0b 14%, white 86%);
}

.pending-message-status[data-tooltip]:not([data-tooltip=''])::after {
  content: attr(data-tooltip);
  position: absolute;
  right: 0;
  bottom: calc(100% + 8px);
  width: max-content;
  max-width: min(320px, calc(100vw - 48px));
  padding: 8px 10px;
  border-radius: 10px;
  background: var(--surface-overlay);
  border: 1px solid var(--border-default);
  box-shadow: 0 12px 28px rgba(15, 23, 42, 0.14);
  color: var(--text-primary);
  font-size: 0.72rem;
  font-weight: 500;
  line-height: 1.35;
  letter-spacing: 0;
  white-space: normal;
  opacity: 0;
  transform: translateY(4px);
  pointer-events: none;
  transition:
    opacity 140ms ease,
    transform 140ms ease;
}

.pending-message-status[data-tooltip]:not([data-tooltip='']):hover::after {
  opacity: 1;
  transform: translateY(0);
}

.pending-message-action {
  flex-shrink: 0;
  min-height: 24px;
  padding: 0 10px;
  border: 1px solid color-mix(in srgb, var(--interactive-focus-border) 38%, var(--border-default) 62%);
  border-radius: 999px;
  background: color-mix(in srgb, var(--surface-chat) 70%, var(--surface-panel) 30%);
  color: var(--text-primary);
  font-size: 0.72rem;
  font-weight: 600;
  line-height: 1;
  cursor: pointer;
  transition:
    border-color 140ms ease,
    background 140ms ease,
    color 140ms ease,
    opacity 140ms ease;
}

.pending-message-action:hover:not(:disabled) {
  border-color: var(--interactive-focus-border);
  background: color-mix(in srgb, var(--interactive-selected) 18%, var(--surface-panel) 82%);
}

.pending-message-action:disabled {
  opacity: 0.58;
  cursor: default;
}

:global(html.bp-compact) .pending-message-bar {
  padding: 7px 10px;
  border-radius: 12px;
}

:global(html.bp-compact) .pending-message-avatar {
  width: 24px;
  height: 24px;
  border-radius: 8px;
}

:global(html.bp-compact) .pending-message-content {
  font-size: 0.8rem;
}

:global(html.bp-compact) .pending-message-action {
  min-height: 22px;
  padding: 0 8px;
  font-size: 0.68rem;
}
</style>

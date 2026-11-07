<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, useTemplateRef, watch, type CSSProperties } from 'vue';
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

/**
 * 卡片是否可交互（可点开 / 可收起）。
 *
 * 注意**不能**只写 `truncated`：展开后如果窗口变宽、预览行不再被截断，`truncated` 会变
 * false，那样收起通路就断了（点击和 Esc 都被 gate 掉，卡片再也收不起来）。展开态本身
 * 就是「可交互」的理由，两者取或。
 */
const canToggle = computed(() => truncated.value || props.expanded);

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

/**
 * 徽标提示的定位。
 *
 * 不能继续用 `::after` 画在徽标正上方：待读浮层的列表是滚动容器（`max-height` 超出要内部
 * 滚动），伪元素落到滚动区外会被裁掉，实测第一条卡片的提示只剩 7px 可见。改成 Teleport
 * 到 body + `position: fixed`，不受任何祖先 `overflow` 裁剪。
 */
const tooltipVisible = ref(false);
const tooltipStyle = ref<CSSProperties>({});
const tooltipAnchorRef = useTemplateRef<HTMLElement>('tooltipAnchorRef');

/**
 * 按徽标当前位置重算 tooltip 坐标。
 *
 * 单独抽出来是因为它**不只 hover 时要用**：fixed 坐标是快照，hover 期间一旦发生滚动，
 * 徽标跟着走而 tooltip 停在原地就会错位（实测滚 8px 后间距从 +6 变成 -2，即压住徽标）。
 * 所以 visible 期间要在 capture 阶段监听 scroll / resize 重算。
 */
function repositionTooltip(): void {
  const rect = tooltipAnchorRef.value?.getBoundingClientRect();
  if (!rect || typeof window === 'undefined') {
    return;
  }
  tooltipStyle.value = {
    position: 'fixed',
    // 画在徽标上方，留 6px 间距；右对齐徽标右边缘。
    bottom: `${window.innerHeight - rect.top + 6}px`,
    right: `${window.innerWidth - rect.right}px`,
  };
}

/**
 * 滚动 / 尺寸变化的重算监听。
 *
 * `scroll` 事件不冒泡，但**捕获阶段**仍会从 window 往下走，所以在 window 上挂 capture
 * 就能同时收到页面滚动与任意祖先滚动容器（待读浮层的 `__list`）的滚动，不用去猜哪个
 * 祖先可滚。tooltip 不可见时立即摘掉，不留常驻监听。
 */
function attachRepositionListeners(): void {
  if (typeof window === 'undefined') {
    return;
  }
  window.addEventListener('scroll', repositionTooltip, true);
  window.addEventListener('resize', repositionTooltip);
}

function detachRepositionListeners(): void {
  if (typeof window === 'undefined') {
    return;
  }
  window.removeEventListener('scroll', repositionTooltip, true);
  window.removeEventListener('resize', repositionTooltip);
}

function showTooltip(): void {
  if (!tooltipAnchorRef.value) {
    return;
  }
  repositionTooltip();
  tooltipVisible.value = true;
  attachRepositionListeners();
}

function hideTooltip(): void {
  tooltipVisible.value = false;
  detachRepositionListeners();
}

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

/**
 * 收起通路的 gate 是 `canToggle`（截断 **或** 已展开），不是 `truncated`。
 * 只 gate 在 truncated 上时，展开后窗口变宽会让卡片彻底收不起来。
 */
function handleToggle(event?: MouseEvent): void {
  if (!canToggle.value) {
    return;
  }
  if (isSelectionInsideCard(event)) {
    return;
  }
  emit('toggle');
}

/**
 * 本次点击是不是「拖选的收尾 click」。
 *
 * 拖选文字结束时浏览器仍会补一个 click，选中内容非空说明用户在选字（想复制正文），
 * 不该把卡片收起来——否则「选中一段复制」这个动作永远做不成。
 *
 * 判据必须带上「选区在本卡片内」：只看 selection 非空的话，用户随便在页面上选中
 * 一段文字后，本卡片就再也点不开了，那是新造一个缺陷。没有 event（键盘触发）时
 * 不看选区，键盘用户本来就不会产生选区。
 */
function isSelectionInsideCard(event?: MouseEvent): boolean {
  if (!event || typeof window === 'undefined') {
    return false;
  }
  const selection = window.getSelection();
  if (!selection || selection.isCollapsed || !selection.toString().trim()) {
    return false;
  }
  const card = event.currentTarget as Node | null;
  const anchors = [selection.anchorNode, selection.focusNode];
  return anchors.some((node) => Boolean(node) && Boolean(card?.contains(node)));
}

function handleKeydown(event: KeyboardEvent): void {
  if (!canToggle.value) {
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

/**
 * 全文区内的点击不应触发展开 / 收起。
 * 不拦的话在全文里选字、点链接、拖横向滚动条都会把卡片收起，40vh 全文区基本没法读。
 */
function handleFullClick(event: MouseEvent): void {
  event.stopPropagation();
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
    // 展开期间预览行被 v-show 隐藏（display:none 量出来是 0/0），收起回来
    // 需要等 DOM 恢复可见后再重新判断它是否还被截断。
    if (!expanded) {
      nextTick(measureTruncation);
    }
  },
);

onBeforeUnmount(() => {
  resizeObserver?.disconnect();
  resizeObserver = null;
  // tooltip 是 Teleport 出去的，卸载时 DOM 会跟着走，但 window 上的重算监听不会自己摘。
  detachRepositionListeners();
});
</script>

<template>
  <div
    class="pending-message-bar"
    :class="{
      'pending-message-bar--expandable': canToggle,
      'pending-message-bar--expanded': expanded,
    }"
    :role="canToggle ? 'button' : undefined"
    :tabindex="canToggle ? 0 : undefined"
    :aria-expanded="canToggle ? expanded : undefined"
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
      <!--
        展开后隐藏预览行：全文本身已包含首行，再留一行摘要就是重复展示。
        用 `v-show` 而不是 `v-if`：元素保留在 DOM 里，截断测量用的 ref /
        ResizeObserver 不用跟着卸载重建（收起时 nextTick 直接量即可）。
      -->
      <div v-show="!expanded" ref="previewRef" class="pending-message-content">{{ previewText }}</div>
      <!-- 全文区自己拦点击：里面选字 / 点链接 / 拖滚动条都不该收起卡片。 -->
      <div v-if="expanded" class="pending-message-full" @click="handleFullClick">
        <MarkdownContent :content="item.message.content" />
      </div>
    </div>
    <span
      ref="tooltipAnchorRef"
      v-if="isImmediate"
      class="pending-message-status pending-message-status--pending-immediate"
      @mouseenter="showTooltip"
      @mouseleave="hideTooltip"
    >⚡ {{ statusLabel }}</span>
    <span
      v-else
      ref="tooltipAnchorRef"
      class="pending-message-status pending-message-status--queued"
      @mouseenter="showTooltip"
      @mouseleave="hideTooltip"
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
    <span v-if="canToggle" class="pending-message-chevron" aria-hidden="true">{{ expanded ? '▾' : '▸' }}</span>
    <!--
      tooltip 挂到 body 上：待读浮层的列表是滚动容器，画在卡片内部的提示会被
      overflow 裁掉（实测第一条只剩 7px 可见）。fixed 定位不受任何祖先裁剪。
    -->
    <Teleport to="body">
      <span v-if="tooltipVisible" class="pending-message-tooltip" :style="tooltipStyle">
        {{ statusTooltip }}
      </span>
    </Teleport>
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

/* 展开后的全文：限高在卡片内部滚动，避免单条把整屏撑爆。
   字号必须显式给：MarkdownContent 用 `1em`，不写就会继承面板/根字号（明显大于
   收起态的 0.84rem 预览行）。展开后预览行已隐藏，也不再需要上边框做分隔。 */
.pending-message-full {
  max-height: 40vh;
  overflow: auto;
  font-size: 0.84rem;
  line-height: 1.5;
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

/*
 * 提示浮层用 fixed + Teleport 到 body，不参与祖先的 overflow 裁剪。
 * 不用 ::after 是因为列表是滚动容器，画在卡片内的伪元素会被裁掉。
 */
.pending-message-tooltip {
  position: fixed;
  z-index: 9999;
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
  width: max-content;
  max-width: min(320px, calc(100vw - 48px));
  pointer-events: none;
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

:global(html.bp-compact) .pending-message-full {
  font-size: 0.8rem;
}

:global(html.bp-compact) .pending-message-action {
  min-height: 22px;
  padding: 0 8px;
  font-size: 0.68rem;
}
</style>

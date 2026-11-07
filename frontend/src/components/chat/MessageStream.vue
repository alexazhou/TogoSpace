<script setup lang="ts">
import { getAgentAvatarUrl } from '../../avatar';
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useTemplateRef, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import type { MessageInfo, RoomMemberProfile } from '../../types';
import { bubbleSide, displayName, formatTime } from '../../utils';
import MarkdownContent from '../ui/MarkdownContent.vue';
import PendingMessageDock from './pending/PendingMessageDock.vue';
import { buildPendingKey } from './pending/types';
import type { PendingMessageItem } from './pending/types';
import {
  isUnreadPrivateMessage as isUnreadByReadCursor,
  resolveMessageStatus as resolveMessageStatusByReadCursor,
  resolvePendingStatus,
} from '../../realtime/readCursor';
import type { MessageStatusValue, ReadCursorContext } from '../../realtime/readCursor';

const props = defineProps<{
  messages: MessageInfo[];
  memberProfiles: RoomMemberProfile[];
  workingAgent?: RoomMemberProfile | null;
  hasMoreHistory?: boolean;
  loadingOlderMessages?: boolean;
  escalatingMessageIds?: number[];
  /** 是否私聊房间。「排队中 / 即将注入」只对私聊中「发往 AI 接收方」的消息成立。 */
  isPrivate?: boolean;
  /** 目标 AI 的读游标（下一条待读的 seq）。未读判定：message.seq >= agentReadSeq。 */
  agentReadSeq?: number;
  /** 私聊房间成员 ID 列表（含 Operator -1 与 AI 正数 ID），用于定位接收方 AI。 */
  roomAgents?: number[];
  /** 房间内各 Agent 的读游标：{ [agentId]: 下一条待读的 seq }。 */
  agentReadIndex?: Record<string, number>;
}>();

const emit = defineEmits<{
  clickWorkingAgent: [agentId: number];
  clickAgent: [agentId: number];
  loadOlderMessages: [];
  escalateMessage: [messageId: number];
}>();

const { t } = useI18n();

const streamRef = useTemplateRef('streamRef');
const pendingDockRef = useTemplateRef('pendingDockRef');
const hasScrollbar = ref(false);
const historyLoadRequested = ref(false);
const stickToBottom = ref(true);
let programmaticScroll = false;
let resizeObserver: ResizeObserver | null = null;
let pendingDockResizeObserver: ResizeObserver | null = null;
let pendingPrependAnchor: { scrollHeight: number; scrollTop: number } | null = null;

const TOP_HISTORY_LOAD_THRESHOLD_PX = 24;

const NAME_COLORS = [
  '#56d4b0',
  '#7eb8d4',
  '#c4a55a',
  '#d4847e',
  '#b392d4',
  '#8cc152',
  '#4fc1e9',
  '#ffce54',
  '#fc6e51',
  '#ac92ec',
  '#e8a838',
  '#a0d468',
  '#5d9cec',
  '#ed5565',
  '#fb6e52',
];

function senderColor(sender: string): string {
  if (sender.toUpperCase() === 'OPERATOR') {
    return '#7f91a4';
  }
  const hash = Array.from(sender).reduce((sum, char) => sum + char.charCodeAt(0), 0);
  return NAME_COLORS[hash % NAME_COLORS.length];
}

function resolveSenderProfile(senderId: number): RoomMemberProfile | null {
  return props.memberProfiles.find((member) => member.id === senderId) ?? null;
}

function resolveSenderStableName(senderId: number): string {
  if (senderId === -1) {
    return 'OPERATOR';
  }
  if (senderId === -2) {
    return 'SYSTEM';
  }
  return resolveSenderProfile(senderId)?.name ?? String(senderId);
}

function resolveSenderDisplayName(senderId: number): string {
  if (senderId === -1) {
    return 'OPERATOR';
  }
  if (senderId === -2) {
    return 'SYSTEM';
  }
  const profile = resolveSenderProfile(senderId);
  return profile ? displayName(profile) : String(senderId);
}

function resolveSenderAgentId(senderId: number): number | null {
  if (senderId <= 0) {
    return null;
  }
  return resolveSenderProfile(senderId) ? senderId : null;
}

function handleSenderAvatarClick(senderId: number): void {
  const agentId = resolveSenderAgentId(senderId);
  if (agentId === null) {
    return;
  }
  emit('clickAgent', agentId);
}

/**
 * 待读判定的上下文：把 props 里的房间类型、成员、读游标整成一份传给共用纯函数。
 * 判定规则只在 `realtime/readCursor.ts` 里实现一次，聊天页与 Agent 活动面板不会各写一套。
 */
const readCursorCtx = computed<ReadCursorContext>(() => ({
  isPrivate: props.isPrivate,
  roomAgents: props.roomAgents,
  agentReadIndex: props.agentReadIndex,
  agentReadSeq: props.agentReadSeq,
}));

/**
 * 是否为「私聊中发出、接收方 AI 尚未读到」的消息。
 *
 * 两条缺一不可：
 * - `resolveReceiverReadSeq` 非 null：存在接收方 AI 读游标；
 * - `seq >= readSeq`：读游标语义是「下一条待读的 seq」，等于游标的也还没读。
 */
function isUnreadPrivateMessage(message: MessageInfo): boolean {
  return isUnreadByReadCursor(message, readCursorCtx.value);
}

function resolveMessageStatus(message: MessageInfo): MessageStatusValue {
  return resolveMessageStatusByReadCursor(message, readCursorCtx.value);
}

const streamMessages = computed(() => props.messages.filter((message) => !isUnreadPrivateMessage(message)));

/**
 * 待读条目的展示数据。`PendingMessageDock` / `PendingMessageBar` 只负责展示，
 * 「哪些算待读、状态是什么、发送者怎么显示」全部在这里算好再传进去。
 */
const pendingItems = computed<PendingMessageItem[]>(() => {
  const context = readCursorCtx.value;
  return props.messages
    .filter((message) => isUnreadByReadCursor(message, context))
    .map((message, index) => ({
      key: buildPendingKey(message, index),
      message,
      status: resolvePendingStatus(message, context) ?? 'queued',
      sender: {
        displayName: resolveSenderDisplayName(message.sender_id),
        // SYSTEM 消息在主流里是居中的系统提示、不带头像，待读区保持同样处理。
        avatarUrl: bubbleSide(message.sender_id) === 'center'
          ? ''
          : getAgentAvatarUrl(resolveSenderStableName(message.sender_id)),
        clickable: resolveSenderAgentId(message.sender_id) !== null,
      },
    }));
});

const shouldDockWorkingIndicator = computed(
  () => Boolean(props.workingAgent) && pendingItems.value.length === 0,
);

function messageKey(message: MessageInfo, index: number): string {
  return String(message.db_id ?? `${message.time}-${message.sender_id}-${index}`);
}

function pendingImmediateTooltip(): string {
  return t('chat.pendingImmediateTip');
}

/** 待读卡片的头像点击：转成 Agent ID 后沿用既有 `clickAgent` 事件，Operator/SYSTEM 不可点。 */
function handlePendingSenderClick(senderId: number): void {
  const agentId = resolveSenderAgentId(senderId);
  if (agentId === null) {
    return;
  }
  emit('clickAgent', agentId);
}

function updateScrollbarState(): void {
  const stream = streamRef.value;
  if (!stream) {
    hasScrollbar.value = false;
    return;
  }

  hasScrollbar.value = stream.scrollHeight - stream.clientHeight > 1;
}

function isAtBottom(): boolean {
  const stream = streamRef.value;
  if (!stream) return true;
  return stream.scrollTop + stream.clientHeight >= stream.scrollHeight - 2;
}

function scrollToBottom(): void {
  const stream = streamRef.value;
  if (!stream) return;
  programmaticScroll = true;
  stream.scrollTop = stream.scrollHeight;
  requestAnimationFrame(() => {
    programmaticScroll = false;
  });
}

function requestOlderMessages(): void {
  const stream = streamRef.value;
  if (
    !stream
    || !props.hasMoreHistory
    || props.loadingOlderMessages
    || historyLoadRequested.value
  ) {
    return;
  }

  pendingPrependAnchor = {
    scrollHeight: stream.scrollHeight,
    scrollTop: stream.scrollTop,
  };
  historyLoadRequested.value = true;
  emit('loadOlderMessages');
}

function maybeLoadOlderMessagesForShortViewport(): void {
  const stream = streamRef.value;
  if (!stream || !props.hasMoreHistory || props.loadingOlderMessages || historyLoadRequested.value) {
    return;
  }

  if (stream.scrollHeight <= stream.clientHeight + 2) {
    requestOlderMessages();
  }
}

function restorePrependedHistoryAnchor(): void {
  const stream = streamRef.value;
  if (!stream) {
    pendingPrependAnchor = null;
    historyLoadRequested.value = false;
    return;
  }

  if (pendingPrependAnchor) {
    stream.scrollTop = pendingPrependAnchor.scrollTop + (stream.scrollHeight - pendingPrependAnchor.scrollHeight);
  }
  pendingPrependAnchor = null;
  historyLoadRequested.value = false;
}

function handleStreamScroll(): void {
  updateScrollbarState();

  if (!programmaticScroll) {
    stickToBottom.value = isAtBottom();
  }

  const stream = streamRef.value;
  if (!stream) {
    return;
  }

  if (stream.scrollTop <= TOP_HISTORY_LOAD_THRESHOLD_PX) {
    requestOlderMessages();
  }
}

watch(
  () => props.messages,
  async () => {
    const shouldScroll = stickToBottom.value;
    await nextTick();
    updateScrollbarState();
    if (pendingPrependAnchor) {
      restorePrependedHistoryAnchor();
      maybeLoadOlderMessagesForShortViewport();
      return;
    }
    if (shouldScroll) scrollToBottom();
    maybeLoadOlderMessagesForShortViewport();
  },
  { deep: true },
);

watch(
  () => props.workingAgent,
  async () => {
    const shouldScroll = stickToBottom.value;
    await nextTick();
    updateScrollbarState();
    if (shouldScroll) scrollToBottom();
  },
);

/**
 * 浮层是 `v-if` 渲染的：从「没有待读」到「有 1 条」时元素才第一次出现，
 * 这时要重新绑 `ResizeObserver`，否则后续展开/折叠的高度变化不会被观察到。
 */
watch(
  () => pendingItems.value.length > 0,
  async () => {
    await nextTick();
    observePendingDock();
  },
  { flush: 'post' },
);

watch(
  () => props.loadingOlderMessages,
  async (loadingOlderMessages, previousLoadingOlderMessages) => {
    if (!previousLoadingOlderMessages || loadingOlderMessages) {
      return;
    }

    await nextTick();
    if (pendingPrependAnchor) {
      restorePrependedHistoryAnchor();
    }
    maybeLoadOlderMessagesForShortViewport();
  },
);

watch(
  () => props.hasMoreHistory,
  async () => {
    await nextTick();
    maybeLoadOlderMessagesForShortViewport();
  },
);

/**
 * 待读浮层高度变化（新增一条、展开、折叠、读走一条）时保持贴底。
 *
 * `.message-stream` 是滚动容器，内容变高不会触发它自身的 `ResizeObserver`（盒子尺寸没变），
 * 所以这里单独观察浮层根元素。用户已经往上翻（`stickToBottom` 为 false）时不动，
 * 否则会把用户正在看的历史内容拽走。
 */
function observePendingDock(): void {
  pendingDockResizeObserver?.disconnect();
  pendingDockResizeObserver = null;
  const el = pendingDockRef.value?.$el as HTMLElement | undefined;
  if (!el || typeof ResizeObserver === 'undefined') {
    return;
  }
  pendingDockResizeObserver = new ResizeObserver(() => {
    if (!stickToBottom.value) {
      return;
    }
    scrollToBottom();
    updateScrollbarState();
  });
  pendingDockResizeObserver.observe(el);
}

onMounted(() => {
  updateScrollbarState();
  scrollToBottom();
  streamRef.value?.addEventListener('scroll', handleStreamScroll, { passive: true });
  maybeLoadOlderMessagesForShortViewport();
  observePendingDock();
  if (typeof ResizeObserver === 'undefined' || !streamRef.value) {
    return;
  }

  resizeObserver = new ResizeObserver(() => {
    updateScrollbarState();
    maybeLoadOlderMessagesForShortViewport();
  });
  resizeObserver.observe(streamRef.value);
});

onBeforeUnmount(() => {
  streamRef.value?.removeEventListener('scroll', handleStreamScroll);
  resizeObserver?.disconnect();
  resizeObserver = null;
  pendingDockResizeObserver?.disconnect();
  pendingDockResizeObserver = null;
  pendingPrependAnchor = null;
});
</script>

<template>
  <div ref="streamRef" class="message-stream" :class="{ 'has-scrollbar': hasScrollbar }">
    <div v-if="loadingOlderMessages" class="history-loader">
      {{ t('chat.loadingOlderMessages') }}
    </div>

    <div
      v-for="(message, index) in streamMessages"
      :key="messageKey(message, index)"
      class="message-row"
      :class="`side-${bubbleSide(message.sender_id)}`"
    >
      <template v-if="bubbleSide(message.sender_id) === 'center'">
        <div class="system-note">
          <MarkdownContent :content="message.content" />
        </div>
      </template>
      <template v-else>
        <div class="message-meta">
          <template v-if="bubbleSide(message.sender_id) === 'left'">
            <img
              class="sender-avatar"
              :class="{ 'sender-avatar--clickable': resolveSenderAgentId(message.sender_id) !== null }"
              :src="getAgentAvatarUrl(resolveSenderStableName(message.sender_id))"
              :alt="`${resolveSenderDisplayName(message.sender_id)} avatar`"
              @click="handleSenderAvatarClick(message.sender_id)"
            />
            <span class="sender" :style="{ color: senderColor(resolveSenderStableName(message.sender_id)) }">
              {{ resolveSenderDisplayName(message.sender_id) }}
            </span>
          </template>
          <span class="time">{{ formatTime(message.time) }}</span>
          <span
            v-if="resolveMessageStatus(message) === 'queued'"
            class="msg-status msg-status--queued"
            title="消息排队中，等待 Agent 回复后注入"
          >⏳ 排队中</span>
          <span
            v-else-if="resolveMessageStatus(message) === 'pending-immediate'"
            class="msg-status msg-status--pending-immediate"
            :title="pendingImmediateTooltip()"
          >⚡ {{ t('chat.pendingImmediateLabel') }}</span>
          <span
            v-else-if="resolveMessageStatus(message) === 'immediate'"
            class="msg-status msg-status--immediate"
            title="已立即注入"
          >⚡</span>
          <template v-if="bubbleSide(message.sender_id) === 'right'">
            <span class="sender" :style="{ color: senderColor(resolveSenderStableName(message.sender_id)) }">
              {{ resolveSenderDisplayName(message.sender_id) }}
            </span>
            <img
              class="sender-avatar"
              :class="{ 'sender-avatar--clickable': resolveSenderAgentId(message.sender_id) !== null }"
              :src="getAgentAvatarUrl(resolveSenderStableName(message.sender_id))"
              :alt="`${resolveSenderDisplayName(message.sender_id)} avatar`"
              @click="handleSenderAvatarClick(message.sender_id)"
            />
          </template>
        </div>
        <div class="bubble">
          <MarkdownContent :content="message.content" />
        </div>
      </template>
    </div>

    <div
      v-if="workingAgent"
      class="working-indicator working-indicator--clickable"
      :class="{ 'working-indicator--dock-bottom': shouldDockWorkingIndicator }"
      role="button"
      tabindex="0"
      @click="emit('clickWorkingAgent', workingAgent.id)"
      @keydown.enter="emit('clickWorkingAgent', workingAgent.id)"
    >
      <img
        class="working-indicator-avatar"
        :src="getAgentAvatarUrl(workingAgent.name)"
        :alt="`${displayName(workingAgent)} avatar`"
      />
      <span class="working-indicator-text">{{ t('chat.processing', { name: displayName(workingAgent) }) }}</span>
      <span class="working-indicator-dots">
        <span class="dot"></span>
        <span class="dot"></span>
        <span class="dot"></span>
      </span>
      <i class="fa-solid fa-chevron-right working-indicator-icon"></i>
    </div>

    <PendingMessageDock
      v-if="pendingItems.length"
      ref="pendingDockRef"
      :items="pendingItems"
      :escalating-ids="escalatingMessageIds ?? []"
      @escalate="emit('escalateMessage', $event)"
      @click-sender="handlePendingSenderClick"
    />
  </div>
</template>

<style scoped>
.message-stream {
  height: 100%;
  min-height: 0;
  overflow-y: auto;
  padding: 8px 0;
  display: flex;
  flex-direction: column;
  gap: 14px;
  scrollbar-width: thin;
  scrollbar-color: var(--scrollbar-thumb) var(--scrollbar-track);
}

.history-loader {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 28px;
  color: var(--text-secondary);
  font-size: 0.72rem;
  line-height: 1.2;
}

.message-stream::-webkit-scrollbar {
  width: 10px;
}

.message-stream::-webkit-scrollbar-track {
  background: var(--scrollbar-track);
  border-radius: 999px;
}

.message-stream::-webkit-scrollbar-thumb {
  background: var(--scrollbar-thumb);
  border-radius: 999px;
  border: 2px solid var(--scrollbar-track);
}

.message-stream::-webkit-scrollbar-thumb:hover {
  background: var(--scrollbar-thumb-hover);
}

.message-row {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.message-row.side-left {
  align-items: flex-start;
}

.message-row.side-right {
  align-items: flex-end;
}

.message-stream.has-scrollbar .message-row.side-right {
  padding-right: 6px;
}

.message-row.side-center {
  align-items: center;
  padding: 2px 0 4px;
}

.message-meta {
  display: inline-flex;
  gap: 6px;
  align-items: center;
  color: var(--text-secondary);
  font-size: 0.72rem;
  padding: 0 6px;
}

.sender {
  font-weight: 600;
  font-size: 0.84rem;
  line-height: 1;
}

.sender-avatar {
  width: 36px;
  height: 36px;
  border-radius: 10px;
  object-fit: cover;
  flex-shrink: 0;
  border: 1px solid color-mix(in srgb, var(--border-strong) 30%, transparent);
  background: color-mix(in srgb, var(--surface-elevated) 84%, var(--border-default) 16%);
}

.sender-avatar--clickable {
  cursor: pointer;
}

.time {
  color: var(--text-tertiary);
  /* 日期+时钟整体不换行：「2026/10/28 02:07:41」被拆成两行会很难读 */
  white-space: nowrap;
}

.badge-immediate,
.msg-status {
  font-size: 0.72rem;
  line-height: 1;
}

.msg-status--immediate {
  color: #f59e0b;
}

.msg-status--queued {
  color: var(--text-tertiary);
  font-style: italic;
}

.msg-status--pending-immediate {
  color: #f59e0b;
  font-style: italic;
}

.bubble,
.system-note {
  max-width: min(80%, 820px);
  border-radius: 6px;
  padding: 10px 14px;
  line-height: 1.55;
  word-break: break-word;
  overflow-wrap: anywhere;
  font-size: 0.82rem;
}

.bubble {
  background: var(--bubble-left);
  color: var(--bubble-left-text, inherit);
  border: 1px solid color-mix(in srgb, var(--border-default) 18%, transparent);
  box-shadow: var(--bubble-shadow, none);
  transition: opacity 0.25s ease;
}

.bubble :deep(.markdown-content) {
  color: inherit;
}

.message-row.side-left .bubble :deep(.markdown-code-block) {
  background: var(--chat-bubble-left-code-bg);
}

.side-right .bubble {
  background: var(--bubble-right);
  color: var(--bubble-right-text);
  border: 1px solid color-mix(in srgb, var(--interactive-focus-border) 22%, transparent);
  box-shadow: var(--bubble-right-shadow, none);
}

.message-row.side-right .bubble :deep(.markdown-content) {
  --markdown-link-color: var(--markdown-link-color-on-accent);
  --markdown-link-hover-color: var(--markdown-link-hover-color-on-accent);
}

.system-note {
  text-align: center;
  color: color-mix(in srgb, var(--text-secondary) 78%, var(--text-primary) 22%);
  background: transparent;
  padding: 0;
  max-width: min(72%, 760px);
  line-height: 1.5;
  font-size: 0.76rem;
  letter-spacing: 0.01em;
}

:global(html.bp-layout-narrow) .bubble,
:global(html.bp-layout-narrow) .system-note {
  max-width: 100%;
}

:global(html.bp-compact) .message-stream {
  gap: 12px;
  padding: 6px 0 10px;
}

:global(html.bp-compact) .message-meta {
  gap: 5px;
  padding: 0 2px;
  font-size: 0.7rem;
  flex-wrap: wrap;
}

:global(html.bp-compact) .message-stream.has-scrollbar .message-row.side-right {
  padding-right: 0;
}

:global(html.bp-compact) .sender {
  font-size: 0.8rem;
}

:global(html.bp-compact) .sender-avatar {
  width: 32px;
  height: 32px;
  border-radius: 9px;
}

:global(html.bp-compact) .bubble {
  max-width: min(92%, 640px);
  padding: 10px 12px;
  font-size: 0.8rem;
  line-height: 1.5;
}

:global(html.bp-compact) .system-note {
  max-width: 100%;
  font-size: 0.72rem;
  line-height: 1.45;
}

:global(html.bp-compact) .working-indicator {
  padding: 10px 8px;
  font-size: 0.76rem;
}

.working-indicator {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  color: var(--text-secondary);
  font-size: 0.78rem;
  animation: fade-in 0.25s ease-out;
}

.working-indicator--dock-bottom {
  margin-top: auto;
}

.working-indicator--clickable {
  cursor: pointer;
  border-radius: 8px;
  transition: background 0.15s ease;
}

.working-indicator--clickable:hover {
  background: color-mix(in srgb, var(--border-default) 20%, transparent);
}

.working-indicator-avatar {
  width: 24px;
  height: 24px;
  border-radius: 6px;
  object-fit: cover;
  flex-shrink: 0;
  border: 1px solid color-mix(in srgb, var(--border-strong) 30%, transparent);
  background: color-mix(in srgb, var(--surface-elevated) 84%, var(--border-default) 16%);
}

.working-indicator-text {
  color: var(--text-primary);
  font-weight: 500;
}

.working-indicator-dots {
  display: inline-flex;
  gap: 3px;
  align-items: center;
}

.working-indicator-dots .dot {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--text-secondary);
  animation: dot-pulse 1.4s infinite ease-in-out;
}

.working-indicator-dots .dot:nth-child(2) {
  animation-delay: 0.2s;
}

.working-indicator-dots .dot:nth-child(3) {
  animation-delay: 0.4s;
}

.working-indicator-icon {
  margin-left: 6px;
  font-size: 0.8rem;
  color: var(--text-secondary);
  opacity: 0.75;
  transition: opacity 0.15s ease, color 0.15s ease, transform 0.15s ease;
}

.working-indicator--clickable:hover .working-indicator-icon {
  opacity: 1;
  color: var(--text-primary);
  transform: translateX(2px);
}

@keyframes dot-pulse {
  0%, 80%, 100% { opacity: 0.3; transform: scale(0.8); }
  40% { opacity: 1; transform: scale(1); }
}

@keyframes fade-in {
  from { opacity: 0; transform: translateY(4px); }
  to { opacity: 1; transform: translateY(0); }
}
</style>

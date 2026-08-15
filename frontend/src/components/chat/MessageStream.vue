<script setup lang="ts">
import { getAgentAvatarUrl } from '../../avatar';
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useTemplateRef, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import type { MessageInfo, RoomMemberProfile } from '../../types';
import { bubbleSide, displayName, formatTime } from '../../utils';
import { renderMarkdownPreviewText } from '../../utils/markdown';
import MarkdownContent from '../ui/MarkdownContent.vue';

const props = defineProps<{
  messages: MessageInfo[];
  memberProfiles: RoomMemberProfile[];
  workingAgent?: RoomMemberProfile | null;
  hasMoreHistory?: boolean;
  loadingOlderMessages?: boolean;
  escalatingMessageIds?: number[];
}>();

const emit = defineEmits<{
  clickWorkingAgent: [agentId: number];
  clickAgent: [agentId: number];
  loadOlderMessages: [];
  escalateMessage: [messageId: number];
}>();

const { t } = useI18n();

const streamRef = useTemplateRef('streamRef');
const hasScrollbar = ref(false);
const historyLoadRequested = ref(false);
const stickToBottom = ref(true);
let programmaticScroll = false;
let resizeObserver: ResizeObserver | null = null;
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

type MessageStatus = 'published' | 'immediate' | 'pending-immediate' | 'queued';

function resolveMessageStatus(message: MessageInfo): MessageStatus {
  if (message.seq !== null) {
    return message.insert_immediately ? 'immediate' : 'published';
  }
  return message.insert_immediately ? 'pending-immediate' : 'queued';
}

const streamMessages = computed(() => props.messages.filter((message) => message.seq !== null));
const floatingMessages = computed(() => props.messages.filter((message) => message.seq === null));
const shouldDockWorkingIndicator = computed(
  () => Boolean(props.workingAgent) && floatingMessages.value.length === 0,
);

function messageKey(message: MessageInfo, index: number): string {
  return String(message.db_id ?? `${message.time}-${message.sender_id}-${index}`);
}

function isEscalatingMessage(message: MessageInfo): boolean {
  return message.db_id !== null && Boolean(props.escalatingMessageIds?.includes(message.db_id));
}

function pendingImmediateTooltip(): string {
  return t('chat.pendingImmediateTip');
}

function queuedTooltip(): string {
  return t('chat.queuedMessageTip');
}

function messagePreviewContent(message: MessageInfo): string {
  return renderMarkdownPreviewText(message.content);
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

onMounted(() => {
  updateScrollbarState();
  scrollToBottom();
  streamRef.value?.addEventListener('scroll', handleStreamScroll, { passive: true });
  maybeLoadOlderMessagesForShortViewport();
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

    <div v-if="floatingMessages.length" class="floating-messages-dock">
      <div
        v-for="(message, index) in floatingMessages"
        :key="messageKey(message, index)"
        class="floating-message-bar"
      >
        <img
          v-if="bubbleSide(message.sender_id) !== 'center'"
          class="floating-message-avatar"
          :class="{ 'floating-message-avatar--clickable': resolveSenderAgentId(message.sender_id) !== null }"
          :src="getAgentAvatarUrl(resolveSenderStableName(message.sender_id))"
          :alt="`${resolveSenderDisplayName(message.sender_id)} avatar`"
          @click="handleSenderAvatarClick(message.sender_id)"
        />
        <div class="floating-message-content">{{ messagePreviewContent(message) }}</div>
        <span
          v-if="resolveMessageStatus(message) === 'queued'"
          class="floating-message-status floating-message-status--queued"
          :data-tooltip="queuedTooltip()"
        >⏳ {{ t('chat.queuedMessageLabel') }}</span>
        <span
          v-else-if="resolveMessageStatus(message) === 'pending-immediate'"
          class="floating-message-status floating-message-status--pending-immediate"
          :data-tooltip="pendingImmediateTooltip()"
        >⚡ {{ t('chat.pendingImmediateLabel') }}</span>
        <button
          v-if="resolveMessageStatus(message) === 'queued' && message.db_id !== null"
          type="button"
          class="floating-message-action"
          :disabled="isEscalatingMessage(message)"
          @click="emit('escalateMessage', message.db_id)"
        >
          {{ isEscalatingMessage(message) ? t('chat.guidingNow') : t('chat.guideNow') }}
        </button>
      </div>
    </div>
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

.floating-messages-dock {
  position: sticky;
  bottom: 0;
  z-index: 2;
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: auto;
  padding: 8px 0 2px;
  background: transparent;
}

.floating-message-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 12px;
  border: 1px solid color-mix(in srgb, var(--interactive-focus-border) 14%, var(--border-default) 86%);
  border-radius: 14px;
  background: var(--chat-floating-message-bg);
}

.floating-message-avatar {
  width: 28px;
  height: 28px;
  border-radius: 9px;
  object-fit: cover;
  flex-shrink: 0;
  border: 1px solid color-mix(in srgb, var(--border-strong) 28%, transparent);
  background: color-mix(in srgb, var(--surface-elevated) 84%, var(--border-default) 16%);
}

.floating-message-avatar--clickable {
  cursor: pointer;
}

.floating-message-status {
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
}

.floating-message-status--queued {
  color: var(--text-secondary);
  background: color-mix(in srgb, var(--surface-panel-muted) 78%, var(--surface-panel) 22%);
}

.floating-message-status--pending-immediate {
  color: #b66a00;
  background: color-mix(in srgb, #f59e0b 14%, white 86%);
}

.floating-message-status[data-tooltip]:not([data-tooltip=''])::after {
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

.floating-message-status[data-tooltip]:not([data-tooltip='']):hover::after {
  opacity: 1;
  transform: translateY(0);
}

.floating-message-action {
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

.floating-message-action:hover:not(:disabled) {
  border-color: var(--interactive-focus-border);
  background: color-mix(in srgb, var(--interactive-selected) 18%, var(--surface-panel) 82%);
}

.floating-message-action:disabled {
  opacity: 0.58;
  cursor: default;
}

.floating-message-content {
  min-width: 0;
  flex: 1;
  color: var(--text-primary);
  font-size: 0.84rem;
  line-height: 1.35;
  word-break: break-word;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}

:global(html.bp-compact) .floating-messages-dock {
  gap: 4px;
  padding-top: 8px;
}

:global(html.bp-compact) .floating-message-bar {
  padding: 7px 10px;
  border-radius: 12px;
}

:global(html.bp-compact) .floating-message-avatar {
  width: 24px;
  height: 24px;
  border-radius: 8px;
}

:global(html.bp-compact) .floating-message-content {
  font-size: 0.8rem;
}

:global(html.bp-compact) .floating-message-action {
  min-height: 22px;
  padding: 0 8px;
  font-size: 0.68rem;
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

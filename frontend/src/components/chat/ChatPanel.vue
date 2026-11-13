<script setup lang="ts">
import { computed, ref, useTemplateRef, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import { displayName, i18nText } from '../../utils';
import type { MessageInfo, RoomMemberProfile, RoomState } from '../../types';
import { useAgentStatus } from '../../realtime/selectors';
import { useChatComposerResize } from '../../composables/useChatComposerResize';
import ChatComposer from './ChatComposer.vue';
import ChatMembersDrawer from './ChatMembersDrawer.vue';
import MessageStream from './MessageStream.vue';

/**
 * 聊天面板外壳（P4 拆分后）：房间头部（标题 / 调度胶囊 / 成员与设置按钮）、
 * 错误与加载 banner、消息流、输入区、成员抽屉。
 *
 * 抽出的两块：拖拽与高度计算 → `useChatComposerResize`；输入区结构 → `ChatComposer`；
 * 成员抽屉 → `ChatMembersDrawer`。props/emit 契约、DOM 层级与 class 名保持不变。
 */
const props = defineProps<{
  currentRoom: RoomState | null;
  memberProfiles: RoomMemberProfile[];
  messages: MessageInfo[];
  hasMoreHistory: boolean;
  loadingOlderMessages: boolean;
  errorMessage: string;
  reloadingMessages: boolean;
  draft: string;
  composerNotice: string;
  escalatingMessageIds?: number[];
}>();

const emit = defineEmits<{
  updateDraft: [value: string];
  submit: [];
  clickWorkingAgent: [agentId: number];
  clickAgent: [agentId: number];
  loadOlderMessages: [];
  escalateMessage: [messageId: number];
  openRoomSettings: [];
}>();

const { t } = useI18n();

const hasBanner = computed(() => Boolean(props.errorMessage || props.reloadingMessages));
const hasComposer = computed(() => Boolean(props.currentRoom && !props.composerNotice));
const membersOpen = ref(false);
const isDraftComposing = ref(false);
const currentMembers = computed(() => props.memberProfiles);

const chatRef = useTemplateRef('chatRef');
const chatHeadRef = useTemplateRef('chatHeadRef');
const chatBannerRef = useTemplateRef('chatBannerRef');
const messageViewportRef = useTemplateRef('messageViewportRef');

const {
  composerDividerDragging,
  chatLayoutStyle,
  composerStyle,
  startResize,
} = useChatComposerResize({
  chatRef,
  chatHeadRef,
  chatBannerRef,
  messageViewportRef,
  hasComposer,
  hasBanner,
  composerNotice: () => props.composerNotice,
});

const isScheduling = computed(() => props.currentRoom?.state === 'scheduling');
const currentTurnAgentId = computed(() => props.currentRoom?.current_turn_agent_id ?? null);
const currentTurnAgent = computed<RoomMemberProfile | null>(() => {
  const agentId = currentTurnAgentId.value;
  if (agentId === null) {
    return null;
  }
  return props.memberProfiles.find((member) => member.id === agentId) ?? null;
});
const currentSpeaker = computed(() => {
  const agent = currentTurnAgent.value;
  return agent ? displayName(agent) : null;
});

const turnAgentStatus = useAgentStatus(currentTurnAgentId);
const workingAgent = computed<RoomMemberProfile | null>(() => {
  if (
    isScheduling.value
    && currentTurnAgent.value
    && turnAgentStatus.value === 'active'
  ) {
    return currentTurnAgent.value;
  }
  return null;
});

const isDeptRoom = computed(() => props.currentRoom?.tags?.includes('DEPT') ?? false);

const isPrivateRoom = computed(() => props.currentRoom?.room_type === 'private');

/**
 * 目标 AI 的读游标：私聊房间里那个非 Operator、非 System 的 AI 成员。
 * 私聊固定 1 个 AI，取第一个即可；取不到时返回 0（视作「全部未读」），
 * 宁可多显示浮动待读区，也不要漏掉一条 Operator 消息。
 */
const targetAgentReadSeq = computed(() => {
  const room = props.currentRoom;
  if (!room || room.room_type !== 'private') {
    return 0;
  }
  const index = room.agent_read_index ?? {};
  const agentId = room.agents.find((id) => id > 0);
  if (agentId === undefined) {
    return 0;
  }
  const seq = index[String(agentId)];
  return typeof seq === 'number' && Number.isFinite(seq) ? seq : 0;
});

watch(
  () => props.currentRoom?.room_id ?? null,
  () => {
    membersOpen.value = false;
  },
);

function toggleMembers(): void {
  if (!props.currentRoom) {
    return;
  }
  membersOpen.value = !membersOpen.value;
}

function closeMembers(): void {
  membersOpen.value = false;
}

function openRoomSettings(): void {
  if (!props.currentRoom) {
    return;
  }
  emit('openRoomSettings');
}

function handleComposerSubmit(): void {
  if (isDraftComposing.value) {
    return;
  }
  emit('submit');
}

function handleResizeStart(event: PointerEvent): void {
  startResize(event);
}
</script>

<template>
  <section
    ref="chatRef"
    class="chat panel"
    :class="{ 'has-banner': hasBanner, 'no-banner': !hasBanner }"
    :style="chatLayoutStyle"
  >
    <div ref="chatHeadRef" class="chat-head">
      <div class="chat-head-title">
        <h2>{{ currentRoom ? i18nText(currentRoom.i18n, 'display_name', currentRoom.room_name) : t('chat.noRoom') }}</h2>
      </div>
      <div class="chat-side-info">
        <template v-if="currentRoom">
          <span
            class="chat-head-pill"
            :class="isScheduling ? 'chat-head-pill-scheduling' : 'chat-head-pill-idle'"
            :data-tooltip="isScheduling && currentSpeaker ? t('chat.waitingSpeaker', { name: currentSpeaker }) : ''"
          >
            {{ isScheduling ? t('chat.active') : t('chat.idle') }}
          </span>
        </template>
        <button
          type="button"
          class="chat-members-button"
          :disabled="!currentRoom"
          @click="toggleMembers"
        >
          {{ t('chat.membersLabel', { count: currentMembers.length }) }}
        </button>
        <button
          type="button"
          class="chat-members-button chat-settings-button"
          :disabled="!currentRoom || isDeptRoom"
          @click="openRoomSettings"
        >
          <i class="fa-solid fa-gear" aria-hidden="true"></i>
          <span>{{ t('roomSettings.openButton') }}</span>
        </button>
      </div>
    </div>

    <div v-if="errorMessage" ref="chatBannerRef" class="banner error">{{ errorMessage }}</div>
    <div v-else-if="reloadingMessages" ref="chatBannerRef" class="banner">{{ t('chat.loadingMessages') }}</div>

    <div ref="messageViewportRef" class="message-viewport">
      <MessageStream
        :messages="messages"
        :member-profiles="memberProfiles"
        :working-agent="workingAgent"
        :has-more-history="hasMoreHistory"
        :loading-older-messages="loadingOlderMessages"
        :escalating-message-ids="escalatingMessageIds"
        :is-private="isPrivateRoom"
        :agent-read-seq="targetAgentReadSeq"
        :room-agents="currentRoom?.agents"
        :agent-read-index="currentRoom?.agent_read_index"
        @click-agent="emit('clickAgent', $event)"
        @click-working-agent="emit('clickWorkingAgent', $event)"
        @load-older-messages="emit('loadOlderMessages')"
        @escalate-message="emit('escalateMessage', $event)"
      />
    </div>

    <ChatComposer
      v-if="currentRoom || composerNotice"
      :draft="draft"
      :dragging="composerDividerDragging"
      :composer-style="composerStyle"
      :notice="composerNotice"
      @update-draft="emit('updateDraft', $event)"
      @submit="handleComposerSubmit"
      @resize-start="handleResizeStart"
      @composing-change="isDraftComposing = $event"
    />

    <ChatMembersDrawer
      :open="membersOpen"
      :members="currentMembers"
      @close="closeMembers"
    />
  </section>
</template>

<style scoped>
.chat {
  display: grid;
  gap: 0;
  padding: 8px 7px;
  height: 100%;
  min-height: 0;
  overflow: hidden;
  background: var(--surface-chat);
  border-color: var(--panel-border-soft);
}

.chat-head {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  align-items: center;
  padding: 0 2px 8px;
  border-bottom: 1px solid var(--border-subtle);
}

.chat-head h2 {
  margin: 0;
  font-family: 'IBM Plex Sans', 'Noto Sans SC', sans-serif;
  font-weight: 600;
  letter-spacing: 0;
  color: var(--text-primary);
}

.chat-head-title {
  display: flex;
  align-items: center;
}

.chat-side-info {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  justify-content: flex-end;
  align-items: center;
  margin-left: auto;
}

.chat-head-pill,
.chat-members-button {
  border: 1px solid var(--border-subtle);
  background: color-mix(in srgb, var(--surface-pill) 88%, var(--surface-panel-muted) 12%);
  color: var(--text-secondary);
  font-size: 0.76rem;
  line-height: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  white-space: nowrap;
}

.chat-settings-button {
  gap: 6px;
}

.chat-settings-button i {
  font-size: 0.78rem;
}

.chat-head-pill {
  position: relative;
  min-height: 28px;
  padding: 4px 10px 2px;
  border-radius: 8px;
  font-weight: 600;
}

.chat-head-pill-scheduling {
  color: var(--state-success);
  border-color: color-mix(in srgb, var(--state-success) 35%, var(--border-default) 65%);
  background: color-mix(in srgb, var(--state-success) 14%, var(--surface-panel) 86%);
}

.chat-head-pill-idle {
  color: var(--text-secondary);
}

.chat-head-pill[data-tooltip]:not([data-tooltip=''])::after {
  content: attr(data-tooltip);
  position: absolute;
  right: 0;
  top: calc(100% + 8px);
  padding: 6px 10px;
  border-radius: 8px;
  background: var(--surface-overlay);
  border: 1px solid var(--border-default);
  box-shadow: 0 10px 24px rgba(15, 23, 42, 0.16);
  color: var(--text-primary);
  font-size: 0.72rem;
  font-weight: 500;
  line-height: 1.2;
  white-space: nowrap;
  opacity: 0;
  transform: translateY(-4px);
  pointer-events: none;
  transition:
    opacity 140ms ease,
    transform 140ms ease;
}

.chat-head-pill[data-tooltip]:not([data-tooltip='']):hover::after {
  opacity: 1;
  transform: translateY(0);
}

.chat-members-button {
  min-height: 28px;
  padding: 4px 10px 2px;
  border-radius: 8px;
  cursor: pointer;
  transition:
    border-color 140ms ease,
    background 140ms ease,
    color 140ms ease;
}

.chat-members-button:hover:not(:disabled) {
  border-color: var(--interactive-focus-border);
  color: var(--text-primary);
  background: color-mix(in srgb, var(--interactive-selected) 58%, var(--surface-panel) 42%);
}

.chat-members-button:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.banner {
  margin-top: 8px;
  border-radius: 6px;
  padding: 6px 8px;
  background: var(--surface-panel);
  font-size: 0.78rem;
}

.banner.error {
  background: var(--banner-error-bg);
  color: var(--banner-error-text);
}

.message-viewport {
  min-height: 0;
  overflow: hidden;
  margin-top: 2px;
}

:global(html.bp-compact) .chat {
  padding: 10px 6px 8px;
  border-radius: 16px;
}

:global(html.bp-compact) .chat-head {
  gap: 8px;
  padding-bottom: 10px;
}

:global(html.bp-compact) .chat-head h2 {
  font-size: 1.06rem;
  line-height: 1.2;
}

:global(html.bp-compact) .chat-side-info {
  width: 100%;
  justify-content: flex-start;
  gap: 8px;
}

:global(html.bp-compact) .chat-head-pill,
:global(html.bp-compact) .chat-members-button {
  min-height: 32px;
  padding: 6px 12px 4px;
  font-size: 0.78rem;
}

:global(html.bp-compact) .banner {
  font-size: 0.76rem;
}

:global(html.bp-console-short) .chat {
  padding: 6px 5px 5px;
  border-radius: 12px;
}

:global(html.bp-console-short) .chat-head {
  gap: 4px;
  padding: 0 2px 4px;
}

:global(html.bp-console-short) .chat-head h2 {
  font-size: 0.92rem;
  line-height: 1.2;
}

:global(html.bp-console-short) .chat-side-info {
  gap: 4px;
}

:global(html.bp-console-short) .chat-head-pill,
:global(html.bp-console-short) .chat-members-button {
  min-height: 24px;
  padding: 3px 8px 2px;
  font-size: 0.68rem;
}

:global(html.bp-console-short) .banner {
  padding: 4px 6px;
  font-size: 0.68rem;
}

:global(html.bp-console-short) .chat.has-banner .banner {
  margin-top: 4px;
}

:global(html.bp-console-short) .chat.no-banner .message-viewport {
  margin-top: 0;
}
</style>

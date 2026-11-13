<script setup lang="ts">
import { useI18n } from 'vue-i18n';
import { getAgentAvatarUrl } from '../../avatar';
import { displayName } from '../../utils';
import type { RoomMemberProfile } from '../../types';

/**
 * 房间成员抽屉（Teleport 到 body 的模态层）：从 `ChatPanel.vue` 抽出（P4）。
 *
 * 保持三个原样：`Teleport to="body"`、遮罩自点击关闭（`@click.self`）、
 * 以及全部 `chat-members-*` / `chat-member-card*` class 名（样式随之搬入本组件）。
 */
const { t } = useI18n();

defineProps<{
  open: boolean;
  members: RoomMemberProfile[];
}>();

const emit = defineEmits<{
  close: [];
}>();
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="chat-members-modal" @click.self="emit('close')">
      <section class="chat-members-dialog">
        <div class="chat-members-dialog__head">
          <div>
            <p class="chat-members-dialog__eyebrow">Room Members</p>
            <h3>{{ t('chat.roomMembers') }}</h3>
          </div>
          <div class="chat-members-dialog__actions">
            <span>{{ t('room.membersCount', { count: members.length }) }}</span>
            <button type="button" class="chat-members-dialog__close" @click="emit('close')">{{ t('common.close') }}</button>
          </div>
        </div>

        <div v-if="members.length" class="chat-members-grid">
          <article v-for="member in members" :key="member.name" class="chat-member-card">
            <span
              v-if="member.employee_number !== null && member.employee_number >= 0"
              class="chat-member-card__employee"
            >#{{ member.employee_number }}</span>
            <div class="chat-member-card__avatar-wrap">
              <span v-if="member.is_leader" class="chat-member-card__leader-flag">Leader</span>
              <img class="chat-member-card__avatar" :src="getAgentAvatarUrl(member.name)" :alt="`${displayName(member)} avatar`" />
            </div>
            <strong>{{ displayName(member) }}</strong>
            <span v-if="member.role_template_name" class="chat-member-card__meta">{{ member.role_template_name }}</span>
          </article>
        </div>
        <p v-else class="chat-members-empty">{{ t('chat.noMembers') }}</p>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.chat-members-modal {
  position: fixed;
  inset: 0;
  z-index: 60;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgba(6, 10, 16, 0.42);
  backdrop-filter: blur(6px);
}

.chat-members-dialog {
  width: min(860px, 100%);
  max-height: min(720px, calc(100vh - 48px));
  overflow: auto;
  padding: 18px;
  border: 1px solid color-mix(in srgb, var(--interactive-focus-border) 20%, var(--border-default) 80%);
  border-radius: 20px;
  background: var(--surface-overlay);
  box-shadow: 0 24px 60px rgba(15, 23, 42, 0.18);
}

.chat-members-dialog__head,
.chat-members-dialog__actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.chat-members-dialog__head {
  margin-bottom: 16px;
}

.chat-members-dialog__eyebrow {
  margin: 0 0 4px;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.14em;
  font-size: 0.68rem;
}

.chat-members-dialog__head h3 {
  margin: 0;
  color: var(--text-primary);
  font-size: 1.15rem;
}

.chat-members-dialog__actions span,
.chat-members-empty {
  color: var(--text-secondary);
  font-size: 0.74rem;
}

.chat-members-dialog__close {
  border: 1px solid var(--border-default);
  border-radius: 999px;
  background: transparent;
  color: var(--text-secondary);
  font-size: 0.76rem;
  line-height: 1;
  padding: 7px 12px;
  cursor: pointer;
}

.chat-members-dialog__close:hover {
  border-color: var(--interactive-focus-border);
  color: var(--text-primary);
}

.chat-members-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 12px;
}

.chat-member-card {
  position: relative;
  padding: 12px 8px 10px;
  border: 1px solid color-mix(in srgb, var(--interactive-focus-border) 10%, var(--border-default) 90%);
  border-radius: 14px;
  background: color-mix(in srgb, var(--surface-panel-muted) 80%, var(--surface-panel) 20%);
  display: grid;
  justify-items: center;
  gap: 8px;
  text-align: center;
}

.chat-member-card__employee {
  position: absolute;
  top: 8px;
  left: 8px;
  color: var(--text-secondary);
  font-size: 0.8rem;
  line-height: 1;
  letter-spacing: 0.04em;
}

.chat-member-card__avatar-wrap {
  position: relative;
  padding-top: 14px;
}

.chat-member-card__leader-flag {
  position: absolute;
  top: 0;
  left: 50%;
  transform: translate(-50%, -30%);
  color: color-mix(in srgb, var(--state-info) 72%, var(--text-primary) 28%);
  font-size: 0.72rem;
  line-height: 1;
  font-weight: 700;
  letter-spacing: 0.03em;
  white-space: nowrap;
}

.chat-member-card__avatar {
  width: 56px;
  height: 56px;
  border-radius: 16px;
  border: 1px solid color-mix(in srgb, var(--interactive-focus-border) 22%, var(--border-default) 78%);
  object-fit: cover;
  background: color-mix(in srgb, var(--surface-panel-muted) 76%, var(--surface-panel) 24%);
}

.chat-member-card strong {
  color: var(--text-primary);
  font-size: 0.78rem;
  line-height: 1.25;
  word-break: break-word;
}

.chat-member-card__meta {
  color: var(--text-secondary);
  font-size: 0.76rem;
  line-height: 1.25;
  word-break: break-word;
}

.chat-members-empty {
  margin: 0;
}

:global(html.bp-layout-narrow) .chat-members-grid {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

:global(html.bp-compact) .chat-members-modal {
  padding: 14px;
}

:global(html.bp-compact) .chat-members-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}
</style>

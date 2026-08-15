<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import { useRoute, useRouter } from 'vue-router';
import { getAgentAvatarUrl } from '../../avatar';
import { createTeamRoom } from '../../api';
import { showGlobalSuccessToast } from '../../appUiState';
import { loadRoleTemplates, loadTeamAgents, loadTeamRooms } from '../../realtime/runtimeStore';
import { useRoleTemplates, useTeamAgents } from '../../realtime/selectors';
import { displayName } from '../../utils';
import type { AgentStatus } from '../../types';
import ConfirmDialog from '../ui/ConfirmDialog.vue';

type CreateRoomMemberOption = {
  id: number;
  name: string;
  avatarName: string;
  subtitle?: string | null;
  status?: AgentStatus;
};

const props = defineProps<{
  open: boolean;
}>();

const emit = defineEmits<{
  close: [];
}>();

const route = useRoute();
const router = useRouter();
const { t } = useI18n();

const roomName = ref('');
const selectedMemberIds = ref<number[]>([]);
const loadingMembers = ref(false);
const submitting = ref(false);
const confirmOpen = ref(false);
const errorMessage = ref('');

const teamId = computed<number | null>(() => {
  const raw = route.params.teamId;
  if (typeof raw !== 'string') {
    return null;
  }

  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
});

const agents = useTeamAgents(teamId);
const roleTemplates = useRoleTemplates();
const roleTemplateNameMap = computed<Record<number, string>>(() =>
  Object.fromEntries(roleTemplates.value.map((template) => [template.id, displayName(template)])),
);

const members = computed<CreateRoomMemberOption[]>(() =>
  agents.value
    .filter((agent): agent is typeof agent & { id: number } =>
      typeof agent.id === 'number'
      && agent.id !== 0
      && agent.special !== 'system'
      && (agent.special !== null && agent.special !== undefined
        || String(agent.employ_status ?? '').toUpperCase() !== 'OFF_BOARD'),
    )
    .map((agent) => ({
      id: agent.id,
      name: displayName(agent),
      avatarName: agent.name,
      subtitle: agent.special === 'operator'
        ? t('createRoom.operator')
        : agent.special === 'system'
          ? t('createRoom.systemSender')
          : (agent.role_template_id ? (roleTemplateNameMap.value[agent.role_template_id] ?? null) : null),
      status: agent.status,
    })),
);

const canSubmit = computed(() =>
  Boolean(roomName.value.trim())
  && selectedMemberIds.value.length >= 2
  && !submitting.value
  && !loadingMembers.value,
);

const confirmMessage = computed(() => {
  const memberNameMap = new Map(members.value.map((member) => [member.id, member.name]));
  const selectedNames = selectedMemberIds.value
    .map((memberId) => memberNameMap.get(memberId))
    .filter((name): name is string => Boolean(name));

  return t('createRoom.confirmMessage', {
    name: roomName.value.trim(),
    members: selectedNames.join('、') || t('createRoom.noMembersLabel'),
  });
});

function isSelected(memberId: number): boolean {
  return selectedMemberIds.value.includes(memberId);
}

function resetDialogState(): void {
  roomName.value = '';
  selectedMemberIds.value = [];
  loadingMembers.value = false;
  submitting.value = false;
  confirmOpen.value = false;
  errorMessage.value = '';
}

async function loadDialogData(): Promise<void> {
  if (!props.open || teamId.value === null) {
    return;
  }

  loadingMembers.value = true;
  errorMessage.value = '';

  try {
    await Promise.all([
      loadRoleTemplates(),
      loadTeamAgents(teamId.value, { includeSpecial: true }),
    ]);
  } catch (error) {
    errorMessage.value = t('createRoom.loadFailed');
    console.error(error);
  } finally {
    loadingMembers.value = false;
  }
}

function requestClose(force = false): void {
  if (!force && submitting.value) {
    return;
  }
  resetDialogState();
  emit('close');
}

function toggleMember(memberId: number): void {
  selectedMemberIds.value = selectedMemberIds.value.includes(memberId)
    ? selectedMemberIds.value.filter((id) => id !== memberId)
    : [...selectedMemberIds.value, memberId];
}

function requestConfirm(): void {
  if (!canSubmit.value) {
    return;
  }
  confirmOpen.value = true;
}

function closeConfirm(): void {
  if (submitting.value) {
    return;
  }
  confirmOpen.value = false;
}

async function confirmCreateRoom(): Promise<void> {
  if (teamId.value === null || !canSubmit.value) {
    return;
  }

  submitting.value = true;
  errorMessage.value = '';

  try {
    const payload = {
      name: roomName.value.trim(),
      agent_ids: [...selectedMemberIds.value],
    };
    const result = await createTeamRoom(teamId.value, payload);
    const nextRooms = await loadTeamRooms(teamId.value);
    const createdRoom = nextRooms.find((room) => room.room_name === result.room_name) ?? null;

    confirmOpen.value = false;
    showGlobalSuccessToast(t('createRoom.createSuccess'));

    if (createdRoom) {
      await router.push({
        name: 'console',
        params: { teamId: teamId.value, roomId: createdRoom.room_id },
      });
    }

    requestClose(true);
  } catch (error) {
    errorMessage.value = t('createRoom.createFailed');
    console.error(error);
  } finally {
    submitting.value = false;
  }
}

watch(
  () => props.open,
  (open) => {
    if (!open) {
      resetDialogState();
      return;
    }
    loadDialogData().catch(console.error);
  },
  { immediate: true },
);
</script>

<template>
  <Teleport to="body">
    <div v-if="open && !confirmOpen" class="create-room-overlay" @click.self="() => requestClose()">
      <section class="create-room-dialog panel">
        <div class="create-room-head">
          <p class="create-room-eyebrow">Create Room</p>
          <h3>{{ t('createRoom.title') }}</h3>
        </div>

        <div v-if="errorMessage" class="create-room-error">{{ errorMessage }}</div>

        <label class="create-room-field">
          <span>{{ t('createRoom.nameLabel') }}</span>
          <input
            v-model="roomName"
            type="text"
            maxlength="64"
            :placeholder="t('createRoom.namePlaceholder')"
          />
        </label>

        <section class="create-room-members">
          <div class="create-room-members-head">
            <span>{{ t('createRoom.selectMembers') }}</span>
            <small>{{ t('createRoom.selectedCount', { count: selectedMemberIds.length }) }}</small>
          </div>

          <div v-if="loadingMembers" class="create-room-empty">
            {{ t('createRoom.loadingMembers') }}
          </div>

          <div v-else-if="members.length" class="create-room-members-grid">
            <button
              v-for="member in members"
              :key="member.id"
              type="button"
              class="create-room-member"
              :class="{ 'is-selected': isSelected(member.id) }"
              @click="toggleMember(member.id)"
            >
              <span v-if="isSelected(member.id)" class="create-room-member-check">✓</span>
              <img
                class="create-room-member-avatar"
                :src="getAgentAvatarUrl(member.avatarName)"
                :alt="`${member.name} avatar`"
              />
              <div class="create-room-member-body">
                <div class="create-room-member-head">
                  <strong>{{ member.name }}</strong>
                </div>
                <p>{{ member.subtitle || t('createRoom.noRole') }}</p>
              </div>
            </button>
          </div>

          <div v-else class="create-room-empty">
            {{ t('createRoom.noAvailableMembers') }}
          </div>
        </section>

        <div class="create-room-actions">
          <button type="button" class="ghost-button" @click="() => requestClose()">{{ t('common.cancel') }}</button>
          <button
            type="button"
            class="secondary-button"
            :disabled="submitting || !canSubmit"
            @click="requestConfirm"
          >
            {{ t('common.create') }}
          </button>
        </div>
      </section>
    </div>
  </Teleport>

  <ConfirmDialog
    :open="open && confirmOpen"
    :title="t('createRoom.confirmTitle')"
    :message="confirmMessage"
    :confirm-label="t('createRoom.confirmButton')"
    :cancel-label="t('createRoom.backButton')"
    @close="closeConfirm"
    @confirm="confirmCreateRoom"
  />
</template>

<style scoped>
.create-room-overlay {
  position: fixed;
  inset: 0;
  z-index: 60;
  display: grid;
  place-items: center;
  padding: 28px;
  background: rgba(6, 10, 16, 0.52);
  backdrop-filter: blur(8px);
}

.create-room-dialog {
  width: min(560px, 100%);
  max-height: min(720px, calc(100vh - 40px));
  padding: 18px;
  display: grid;
  grid-template-rows: auto auto auto minmax(0, 1fr) auto;
  gap: 14px;
  border-radius: 18px;
  border: 1px solid color-mix(in srgb, var(--focus-border) 26%, var(--panel-border) 74%);
  background:
    linear-gradient(
      180deg,
      color-mix(in srgb, var(--panel-bg) 95%, transparent) 0%,
      color-mix(in srgb, var(--surface-soft) 92%, transparent) 100%
    );
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.34);
}

.create-room-head {
  display: grid;
  gap: 4px;
}

.create-room-eyebrow {
  margin: 0;
  color: var(--accent);
  text-transform: uppercase;
  letter-spacing: 0.14em;
  font-size: 0.68rem;
}

.create-room-head h3 {
  margin: 0;
  color: var(--text-strong);
  font-size: 1.12rem;
}

.create-room-error {
  padding: 10px 12px;
  border: 1px solid color-mix(in srgb, #ef4444 24%, var(--panel-border) 76%);
  border-radius: 12px;
  background: color-mix(in srgb, #fee2e2 58%, var(--panel-bg) 42%);
  color: #b42318;
  font-size: 0.82rem;
}

.create-room-field {
  display: grid;
  gap: 8px;
}

.create-room-field > span,
.create-room-members-head > span {
  color: var(--muted);
  font-size: 0.74rem;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.create-room-field input {
  width: 100%;
  height: 38px;
  border: 1px solid color-mix(in srgb, var(--focus-border) 34%, var(--panel-border) 66%);
  border-radius: 12px;
  background: color-mix(in srgb, var(--surface-soft) 82%, var(--panel-bg) 18%);
  color: var(--text-strong);
  padding: 0 12px;
  outline: none;
  transition:
    border-color 0.18s ease,
    box-shadow 0.18s ease,
    background 0.18s ease;
}

.create-room-field input:focus {
  border-color: var(--focus-border);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--focus-border) 14%, transparent);
}

.create-room-members {
  min-height: 0;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  gap: 8px;
}

.create-room-members-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}

.create-room-members-head small {
  color: var(--muted);
  font-size: 0.74rem;
}

.create-room-members-grid {
  min-height: 0;
  overflow: auto;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(156px, 1fr));
  gap: 10px;
  align-content: start;
  padding-top: 3px;
  padding-right: 4px;
  padding-bottom: 3px;
  scrollbar-width: thin;
  scrollbar-color: var(--scrollbar-thumb) var(--scrollbar-track);
}

.create-room-members-grid::-webkit-scrollbar {
  width: 10px;
}

.create-room-members-grid::-webkit-scrollbar-track {
  background: var(--scrollbar-track);
  border-radius: 999px;
}

.create-room-members-grid::-webkit-scrollbar-thumb {
  background: var(--scrollbar-thumb);
  border-radius: 999px;
  border: 2px solid var(--scrollbar-track);
}

.create-room-member {
  position: relative;
  display: grid;
  grid-template-columns: 48px minmax(0, 1fr);
  gap: 8px;
  align-items: stretch;
  border: 1px solid color-mix(in srgb, var(--panel-border) 82%, transparent 18%);
  border-radius: 14px;
  background: color-mix(in srgb, var(--surface-soft) 78%, var(--panel-bg) 22%);
  color: var(--text-strong);
  padding: 10px;
  text-align: left;
  cursor: pointer;
  transition:
    border-color 0.18s ease,
    background 0.18s ease,
    transform 0.18s ease,
    box-shadow 0.18s ease;
}

.create-room-member:hover {
  border-color: color-mix(in srgb, var(--focus-border) 52%, var(--panel-border) 48%);
  transform: translateY(-1px);
}

.create-room-member.is-selected {
  border-color: var(--focus-border);
  background: color-mix(in srgb, var(--selected) 30%, var(--surface-soft) 70%);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--focus-border) 28%, transparent);
}

.create-room-member-check {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 18px;
  height: 18px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 999px;
  background: var(--focus-border);
  color: white;
  font-size: 0.72rem;
  font-weight: 700;
  box-shadow: 0 6px 14px color-mix(in srgb, var(--focus-border) 30%, transparent);
}

.create-room-member-avatar {
  width: 48px;
  height: 48px;
  border-radius: 14px;
  border: 1px solid color-mix(in srgb, var(--focus-border) 18%, var(--panel-border) 82%);
  object-fit: cover;
  background: color-mix(in srgb, var(--surface-soft) 82%, var(--panel-bg) 18%);
}

.create-room-member-body {
  min-width: 0;
  display: grid;
  gap: 4px;
}

.create-room-member-head {
  display: flex;
  align-items: center;
  gap: 6px;
}

.create-room-member-head strong {
  min-width: 0;
  color: var(--text-strong);
  font-size: 0.84rem;
  line-height: 1.15;
}

.create-room-member-body p {
  margin: 0;
  color: var(--muted);
  font-size: 0.72rem;
  line-height: 1.35;
}

.create-room-empty {
  padding: 12px;
  border-radius: 12px;
  background: color-mix(in srgb, var(--surface-soft) 70%, var(--panel-bg) 30%);
  color: var(--muted);
  font-size: 0.78rem;
}

.create-room-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

.create-room-actions > .primary-button,
.create-room-actions > .ghost-button,
.create-room-actions > .secondary-button {
  min-width: 88px;
  height: 34px;
  padding: 0 14px;
}
</style>

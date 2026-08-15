<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useRoute } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { updateTeamRoom } from '../../api';
import { showGlobalSuccessToast } from '../../appUiState';
import type { RoomState } from '../../types';

const props = defineProps<{
  open: boolean;
  room: RoomState | null;
}>();

const emit = defineEmits<{
  close: [];
  updated: [];
}>();

const route = useRoute();
const { t } = useI18n();

const roomName = ref('');
const errorMessage = ref('');
const submitting = ref(false);

const teamId = computed<number | null>(() => {
  const raw = route.params.teamId;
  if (typeof raw !== 'string') {
    return null;
  }

  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
});

const isDeptRoom = computed(() => props.room?.tags?.includes('DEPT') ?? false);
const trimmedRoomName = computed(() => roomName.value.trim());
const canSubmit = computed(() => (
  Boolean(props.room)
  && !isDeptRoom.value
  && Boolean(trimmedRoomName.value)
  && trimmedRoomName.value !== (props.room?.room_name ?? '')
  && !submitting.value
));

function resetState(): void {
  roomName.value = props.room?.room_name ?? '';
  errorMessage.value = '';
  submitting.value = false;
}

function requestClose(force = false): void {
  if (!force && submitting.value) {
    return;
  }
  resetState();
  emit('close');
}

async function handleSave(): Promise<void> {
  if (teamId.value === null || !props.room || !canSubmit.value) {
    return;
  }

  submitting.value = true;
  errorMessage.value = '';

  try {
    await updateTeamRoom(teamId.value, props.room.room_id, {
      name: trimmedRoomName.value,
    });
    showGlobalSuccessToast(t('roomSettings.saveSuccess'));
    requestClose(true);
    emit('updated');
  } catch (error) {
    errorMessage.value = t('roomSettings.saveFailed');
    console.error(error);
  } finally {
    submitting.value = false;
  }
}

watch(
  () => [props.open, props.room?.room_id, props.room?.room_name] as const,
  ([open]) => {
    if (!open) {
      resetState();
      return;
    }
    roomName.value = props.room?.room_name ?? '';
    errorMessage.value = '';
  },
  { immediate: true },
);
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="room-settings-overlay" @click.self="() => requestClose()">
      <section class="room-settings-dialog panel">
        <div class="room-settings-head">
          <p class="room-settings-eyebrow">Room Settings</p>
          <h3>{{ t('roomSettings.title') }}</h3>
        </div>

        <div v-if="errorMessage" class="room-settings-error">{{ errorMessage }}</div>
        <div v-if="isDeptRoom" class="room-settings-notice">{{ t('roomSettings.deptRoomNotice') }}</div>

        <label class="room-settings-field">
          <span>{{ t('roomSettings.nameLabel') }}</span>
          <input
            v-model="roomName"
            type="text"
            maxlength="64"
            :disabled="isDeptRoom"
            :placeholder="t('roomSettings.namePlaceholder')"
          />
        </label>

        <div class="room-settings-actions">
          <button type="button" class="room-settings-button secondary" :disabled="submitting" @click="() => requestClose()">
            {{ t('common.cancel') }}
          </button>
          <button type="button" class="room-settings-button" :disabled="!canSubmit" @click="handleSave">
            {{ t('common.save') }}
          </button>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.room-settings-overlay {
  position: fixed;
  inset: 0;
  z-index: 60;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgba(6, 10, 16, 0.42);
  backdrop-filter: blur(6px);
}

.room-settings-dialog {
  width: min(640px, 100%);
  display: grid;
  gap: 14px;
  padding: 18px;
  border-radius: 20px;
  background: var(--surface-overlay);
  border: 1px solid color-mix(in srgb, var(--interactive-focus-border) 20%, var(--border-default) 80%);
  box-shadow: 0 24px 60px rgba(15, 23, 42, 0.18);
}

.room-settings-head {
  display: grid;
  gap: 4px;
}

.room-settings-eyebrow {
  margin: 0;
  font-size: 0.68rem;
  text-transform: uppercase;
  letter-spacing: 0.14em;
  color: var(--text-secondary);
}

.room-settings-head h3 {
  margin: 0;
  font-size: 1.15rem;
  line-height: 1.2;
  color: var(--text-primary);
}

.room-settings-notice {
  padding: 12px 14px;
  border-radius: 16px;
  border: 1px solid color-mix(in srgb, var(--text-secondary) 20%, transparent);
  background: color-mix(in srgb, var(--text-secondary) 8%, transparent);
  color: var(--text-secondary);
  font-size: 0.84rem;
}

.room-settings-error {
  padding: 12px 14px;
  border-radius: 16px;
  border: 1px solid color-mix(in srgb, var(--danger) 22%, transparent);
  background: color-mix(in srgb, var(--danger) 10%, transparent);
  color: var(--danger);
}

.room-settings-field {
  display: grid;
  gap: 8px;
}

.room-settings-field span {
  font-size: 0.74rem;
  color: var(--text-secondary);
}

.room-settings-field input {
  width: 100%;
  border-radius: 16px;
  border: 1px solid color-mix(in srgb, var(--interactive-focus-border) 10%, var(--border-default) 90%);
  background: color-mix(in srgb, var(--surface-panel-muted) 80%, var(--surface-panel) 20%);
  color: var(--text-primary);
  font-size: 0.92rem;
  padding: 12px 14px;
  outline: none;
  transition:
    border-color 0.18s ease,
    box-shadow 0.18s ease;
}

.room-settings-field input::placeholder {
  color: var(--hint-text);
}

.room-settings-field input:focus {
  border-color: var(--interactive-focus-border);
  box-shadow: 0 0 0 2px var(--input-focus-ring);
}

.room-settings-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

.room-settings-button {
  min-width: 88px;
  padding: 7px 12px;
  border-radius: 999px;
  border: 1px solid var(--border-default);
  background: transparent;
  color: var(--text-secondary);
  font-size: 0.76rem;
  line-height: 1;
  font-weight: 600;
  cursor: pointer;
  transition:
    border-color 0.18s ease,
    color 0.18s ease,
    opacity 0.18s ease;
}

.room-settings-button.secondary {
  background: transparent;
}

.room-settings-button:hover:not(:disabled) {
  border-color: var(--interactive-focus-border);
  color: var(--text-primary);
}

.room-settings-button:disabled {
  opacity: 0.54;
  cursor: not-allowed;
}
</style>

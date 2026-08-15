<script setup lang="ts">
import { computed, ref } from 'vue';
import { useI18n } from 'vue-i18n';
import { showGlobalSuccessToast } from '../../appUiState';
import { createTeam } from '../../api';
import TeamInfoCard from '../team/TeamInfoCard.vue';
import SettingsBreadcrumb from './SettingsBreadcrumb.vue';
import ConfirmDialog from '../ui/ConfirmDialog.vue';
import type { SettingsBreadcrumbItem } from './types';

const props = defineProps<{
  breadcrumbItems: SettingsBreadcrumbItem[];
}>();

const emit = defineEmits<{
  navigateBreadcrumb: [key: string];
  created: [teamId: number];
  cancel: [];
}>();

const { t } = useI18n();

const name = ref('');
const workingDirectory = ref('');
const slogan = ref('');
const rules = ref('');
const submitting = ref(false);
const errorMessage = ref('');
const confirmOpen = ref(false);

const hasDraftChanges = computed(() =>
  name.value.trim().length > 0
  || workingDirectory.value.trim().length > 0
  || slogan.value.trim().length > 0
  || rules.value.trim().length > 0,
);

const canSubmit = computed(() => name.value.trim().length > 0 && !submitting.value);

function resetDraft(): void {
  name.value = '';
  workingDirectory.value = '';
  slogan.value = '';
  rules.value = '';
  errorMessage.value = '';
}

async function handleSubmit(): Promise<void> {
  if (!canSubmit.value) {
    return;
  }

  confirmOpen.value = false;
  submitting.value = true;
  errorMessage.value = '';

  try {
    const created = await createTeam({
      name: name.value.trim(),
      working_directory: workingDirectory.value.trim(),
      config: {
        slogan: slogan.value.trim(),
        rules: rules.value.trim(),
      },
    });
    showGlobalSuccessToast(t('settings.teams.createSuccess'));
    emit('created', created.id);
  } catch (error) {
    errorMessage.value = t('settings.teams.createFailed');
    console.error(error);
  } finally {
    submitting.value = false;
  }
}

function requestSubmit(): void {
  if (!canSubmit.value) {
    return;
  }

  confirmOpen.value = true;
}

function closeConfirmDialog(): void {
  confirmOpen.value = false;
}
</script>

<template>
  <section class="config-section">
    <div v-if="errorMessage" class="error-banner">{{ errorMessage }}</div>

    <SettingsBreadcrumb :items="breadcrumbItems" @navigate="emit('navigateBreadcrumb', $event)" />

    <div class="team-detail-head team-detail-head--compact">
      <div class="team-detail-actions">
        <button type="button" class="secondary-button" @click="emit('cancel')">{{ t('settings.teams.backToList') }}</button>
      </div>
    </div>

    <form class="team-detail-stack" @submit.prevent="requestSubmit">
      <TeamInfoCard
        :name="name"
        :working-directory="workingDirectory"
        :slogan="slogan"
        :rules="rules"
        @update:name="name = $event"
        @update:working-directory="workingDirectory = $event"
        @update:slogan="slogan = $event"
        @update:rules="rules = $event"
      >
        <template #actions>
          <button
            type="button"
            class="secondary-button team-info-action-button team-info-action-button--compact"
            :disabled="submitting"
            @click="resetDraft"
          >
            {{ t('settings.teams.reset') }}
          </button>
          <button
            type="submit"
            class="primary-button team-info-action-button"
            :disabled="!canSubmit"
          >
            {{ submitting ? t('settings.teams.creating') : t('settings.teams.createBtn') }}
          </button>
        </template>
      </TeamInfoCard>
    </form>

    <ConfirmDialog
      :open="confirmOpen"
      :title="t('settings.teams.createConfirmTitle')"
      :message="t('settings.teams.createConfirmMsg')"
      :confirm-label="t('settings.teams.createConfirmBtn')"
      @close="closeConfirmDialog"
      @confirm="handleSubmit"
    />
  </section>
</template>

<style scoped>
.config-section {
  padding: 12px 0 0;
}

.error-banner {
  padding: 8px 12px;
  margin-bottom: 12px;
  border: 1px solid color-mix(in srgb, var(--danger) 40%, var(--panel-border) 60%);
  border-radius: 8px;
  background: color-mix(in srgb, var(--danger) 12%, var(--panel-bg) 88%);
  color: var(--text-strong);
  font-size: 0.82rem;
}

.team-detail-head {
  margin-top: 4px;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}

.team-detail-head--compact {
  justify-content: flex-end;
}

.team-detail-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 8px;
}

.team-detail-stack {
  display: grid;
  grid-template-columns: 1fr;
  gap: 10px;
  min-height: 0;
  align-items: start;
}

.team-info-action-button {
  min-width: 132px;
}

.team-info-action-button--compact {
  min-width: 88px;
}

.primary-button {
  flex: 0 0 auto;
  min-width: 132px;
  height: 32px;
  padding: 0 14px;
  border: 1px solid var(--interactive-focus-border);
  border-radius: 8px;
  background: var(--interactive-selected);
  color: var(--text-primary);
  font-size: 0.82rem;
  font-weight: 600;
  cursor: pointer;
  transition: all 140ms ease;
}

.primary-button:hover:not(:disabled) {
  background: color-mix(in srgb, var(--interactive-selected) 82%, var(--surface-panel) 18%);
}

.primary-button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.secondary-button {
  flex: 0 0 auto;
  min-width: 132px;
  height: 32px;
  padding: 0 14px;
  border: 1px solid var(--panel-border);
  border-radius: 8px;
  background: var(--surface-pill);
  color: var(--text-secondary);
  font-size: 0.82rem;
  font-weight: 600;
  cursor: pointer;
  transition: all 140ms ease;
}

.secondary-button:hover:not(:disabled) {
  border-color: var(--focus-border);
  color: var(--text-primary);
}

.secondary-button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>

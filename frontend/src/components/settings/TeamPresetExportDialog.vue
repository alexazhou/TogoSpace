<script setup lang="ts">
import type { TeamSummary } from '../../types';
import { useI18n } from 'vue-i18n';

const props = defineProps<{
  open: boolean;
  teams: TeamSummary[];
  selectedTeamId: number | null;
  isExporting: boolean;
}>();

const emit = defineEmits<{
  close: [];
  confirm: [];
  updateSelectedTeamId: [teamId: number];
}>();

const { t } = useI18n();
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="team-export-overlay" @click.self="emit('close')">
      <section class="team-export-dialog panel">
        <div class="team-export-head">
          <p class="team-export-eyebrow">Team Preset Export</p>
          <h3>{{ t('settings.maintenance.exportDialogTitle') }}</h3>
        </div>

        <p class="team-export-message">{{ t('settings.maintenance.exportDialogMsg') }}</p>

        <label class="team-export-field">
          <span class="team-export-label">{{ t('settings.maintenance.exportTeamLabel') }}</span>
          <select
            class="team-export-select"
            :value="selectedTeamId ?? ''"
            :disabled="!props.teams.length || isExporting"
            @change="emit('updateSelectedTeamId', Number(($event.target as HTMLSelectElement).value))"
          >
            <option value="" disabled>{{ t('settings.maintenance.exportTeamPlaceholder') }}</option>
            <option v-for="team in props.teams" :key="team.id" :value="team.id">
              {{ team.name }}
            </option>
          </select>
        </label>

        <div class="team-export-actions">
          <button type="button" class="ghost-button" @click="emit('close')">
            {{ t('common.cancel') }}
          </button>
          <button
            type="button"
            class="secondary-button"
            :disabled="selectedTeamId === null || isExporting"
            @click="emit('confirm')"
          >
            {{ isExporting ? t('settings.maintenance.exporting') : t('settings.maintenance.exportButton') }}
          </button>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.team-export-overlay {
  position: fixed;
  inset: 0;
  z-index: 120;
  display: grid;
  place-items: center;
  padding: 28px;
  background: rgba(6, 10, 16, 0.52);
  backdrop-filter: blur(8px);
}

.team-export-dialog {
  width: min(460px, 100%);
  padding: 18px;
  display: grid;
  gap: 14px;
  border-radius: 18px;
  border: 1px solid color-mix(in srgb, var(--interactive-focus-border) 26%, var(--border-default) 74%);
  background:
    linear-gradient(
      180deg,
      color-mix(in srgb, var(--surface-panel) 95%, transparent) 0%,
      color-mix(in srgb, var(--surface-panel-muted) 92%, transparent) 100%
    );
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.34);
}

.team-export-head {
  display: grid;
  gap: 4px;
}

.team-export-eyebrow {
  margin: 0;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.14em;
  font-size: 0.68rem;
}

.team-export-head h3 {
  margin: 0;
  color: var(--text-primary);
  font-size: 1.12rem;
}

.team-export-message {
  margin: 0;
  color: var(--text-secondary);
  font-size: 0.9rem;
  line-height: 1.55;
}

.team-export-field {
  display: grid;
  gap: 6px;
}

.team-export-label {
  color: var(--text-secondary);
  font-size: 0.84rem;
}

.team-export-select {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--border-color);
  border-radius: 10px;
  background: var(--panel-bg);
  color: var(--text-primary);
  font: inherit;
}

.team-export-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

.team-export-actions > .ghost-button,
.team-export-actions > .secondary-button {
  min-width: 88px;
  height: 32px;
  padding: 0 14px;
}
</style>

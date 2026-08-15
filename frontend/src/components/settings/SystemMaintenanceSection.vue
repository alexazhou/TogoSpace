<script setup lang="ts">
import type { TeamSummary } from '../../types';
import { useI18n } from 'vue-i18n';
import SettingsBreadcrumb from './SettingsBreadcrumb.vue';
import type { SettingsBreadcrumbItem } from './types';

defineProps<{
  breadcrumbItems: SettingsBreadcrumbItem[];
  teams: TeamSummary[];
  isBackingUp: boolean;
}>();

const emit = defineEmits<{
  backupDatabase: [];
  exportTeamPreset: [];
  navigateBreadcrumb: [key: string];
}>();

const { t } = useI18n();
</script>

<template>
  <section id="maintenance" class="config-section maintenance-section">
    <SettingsBreadcrumb :items="breadcrumbItems" @navigate="emit('navigateBreadcrumb', $event)" />

    <section class="maintenance-card-grid">
      <section class="maintenance-panel panel">
        <div class="maintenance-head">
          <div class="maintenance-title-group">
            <h3>{{ t('settings.maintenance.title') }}</h3>
          </div>
          <button
            type="button"
            class="secondary-button"
            :disabled="isBackingUp"
            @click="emit('backupDatabase')"
          >
            {{ isBackingUp ? t('settings.maintenance.backingUp') : t('settings.maintenance.backupButton') }}
          </button>
        </div>

        <div class="maintenance-body">
          <p class="maintenance-description">{{ t('settings.maintenance.description') }}</p>
          <p class="maintenance-note">{{ t('settings.maintenance.backupLocation') }}</p>
        </div>
      </section>

      <section class="maintenance-panel panel">
        <div class="maintenance-head">
          <div class="maintenance-title-group">
            <h3>{{ t('settings.maintenance.exportTitle') }}</h3>
          </div>
          <button
            type="button"
            class="secondary-button"
            :disabled="!teams.length"
            @click="emit('exportTeamPreset')"
          >
            {{ t('settings.maintenance.exportButton') }}
          </button>
        </div>

        <div class="maintenance-body">
          <p class="maintenance-description">{{ t('settings.maintenance.exportDescription') }}</p>
          <p class="maintenance-note">{{ t('settings.maintenance.exportNote') }}</p>
        </div>
      </section>
    </section>
  </section>
</template>

<style scoped>
.maintenance-section {
  display: grid;
  gap: 10px;
}

.maintenance-card-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 10px;
  align-items: start;
}

.maintenance-panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 14px;
  border-radius: 16px;
}

.maintenance-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.maintenance-title-group {
  display: grid;
  gap: 2px;
}

.maintenance-title-group h3 {
  margin: 0;
  color: var(--text-primary);
  font-size: 1rem;
}

.maintenance-description,
.maintenance-note {
  margin: 0;
  color: var(--text-secondary);
  font-size: 0.84rem;
  line-height: 1.45;
}

.maintenance-note {
  color: var(--text-tertiary);
}

.maintenance-body {
  display: grid;
  gap: 6px;
}
</style>

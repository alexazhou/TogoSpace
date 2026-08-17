<script setup lang="ts">
import { useI18n } from 'vue-i18n';
import type { LlmModelConfig } from '../../types';

const props = defineProps<{
  providerName: string;
  models: LlmModelConfig[];
}>();

const emit = defineEmits<{
  back: [];
  add: [];
  test: [modelIndex: number];
  edit: [modelIndex: number];
  delete: [modelIndex: number];
}>();

const { t } = useI18n();
</script>

<template>
  <section class="providers-section">
    <div class="providers-header">
      <div style="display: flex; align-items: center; gap: 8px;">
        <button type="button" class="ghost-button" style="padding: 4px 8px;" @click="emit('back')">&larr; {{ t('common.back', 'Back') }}</button>
        <h4 style="margin: 0;">【{{ providerName }}】 {{ t('settings.models.providerModelsTitle', 'Models') }}</h4>
      </div>
      <button type="button" class="secondary-button" @click="emit('add')">
        {{ t('settings.models.addModel', 'Add Model') }}
      </button>
    </div>

    <div class="models-table-wrap">
      <table class="settings-table models-table">
        <thead>
          <tr>
            <th>{{ t('settings.models.modelNameLabel', 'Model') }}</th>
            <th>{{ t('settings.models.table.inputType', 'Input Type') }}</th>
            <th>{{ t('settings.models.protocolLabel', 'Protocol') }}</th>
            <th class="actions-th">{{ t('settings.models.table.actions') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(model, mIndex) in models" :key="mIndex">
            <td>
              <strong>{{ model.name }}</strong>
              <span v-if="model.input?.includes('image')" class="model-vision-badge">{{ t('settings.models.visionBadge', 'Vision') }}</span>
            </td>
            <td>
              <div class="models-cell-tags-inner">
                <span v-for="type in (model.input || ['text'])" :key="type" class="model-tag">
                  {{ t(`settings.models.inputTypes.${type}`, type) }}
                </span>
              </div>
            </td>
            <td><span class="models-cell-type">{{ model.protocol }}</span></td>
            <td class="models-cell-actions">
              <div class="models-cell-actions-inner">
                <button type="button" class="ghost-button" @click="emit('test', mIndex)">
                  {{ t('settings.models.table.testBtn', 'Test') }}
                </button>
                <button type="button" class="ghost-button" @click="emit('edit', mIndex)">{{ t('settings.models.table.editBtn', 'Edit') }}</button>
                <button type="button" class="ghost-button text-danger" @click="emit('delete', mIndex)">{{ t('settings.models.table.delBtn', 'Del') }}</button>
              </div>
            </td>
          </tr>
          <tr v-if="models.length === 0">
            <td colspan="4" class="models-empty">{{ t('settings.models.table.emptyModels', 'No models configured for this provider.') }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.providers-header {
  display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;
}
.providers-header h4 { margin: 0; color: var(--text-strong); font-size: 0.95rem; }

.text-danger { color: #e5484d; }

.models-table-wrap {
  border-radius: 12px;
  background: var(--panel-bg);
  padding: 0;
  overflow-x: auto;
  border: 1px solid var(--panel-border);
}
.settings-table {
  width: max-content; min-width: 100%; border-collapse: separate; border-spacing: 0; table-layout: auto;
}
.settings-table th, .settings-table td {
  padding: 10px 14px; text-align: left; vertical-align: middle;
}
.settings-table thead th {
  border-bottom: 1px solid color-mix(in srgb, var(--divider) 86%, transparent);
  color: var(--text-strong); font-size: 0.8rem; font-weight: 700; white-space: nowrap;
  background: var(--settings-table-head-bg);
}
.settings-table tbody td {
  border-bottom: 1px solid color-mix(in srgb, var(--divider) 76%, transparent);
  color: var(--text-strong); font-size: 0.84rem;
}
.settings-table tbody tr:last-child td { border-bottom: none; }
.settings-table tbody tr:hover td { background: var(--settings-table-row-hover); }

.settings-table th:nth-child(1),
.settings-table td:nth-child(1) { min-width: 220px; white-space: nowrap; }
.settings-table th:nth-child(2),
.settings-table td:nth-child(2) { min-width: 140px; white-space: nowrap; }

.models-cell-type { color: var(--muted); }
.model-vision-badge { margin-left: 8px; padding: 1px 8px; border-radius: 999px; font-size: 0.7rem; color: var(--accent); border: 1px solid var(--accent); }
.settings-table th.actions-th { min-width: 180px; text-align: right; }
.settings-table td.models-cell-actions {
  min-width: 180px;
  text-align: right;
  white-space: nowrap;
  padding-right: 18px;
}
.models-cell-actions-inner {
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
}
.models-empty { color: var(--muted); font-size: 0.86rem; }

.models-cell-tags-inner {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.model-tag {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border-radius: 6px;
  background: var(--panel-bg);
  border: 1px solid var(--panel-border);
  color: var(--text-strong);
  font-size: 0.78rem;
  white-space: nowrap;
}
</style>

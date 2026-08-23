<script setup lang="ts">
import { useI18n } from 'vue-i18n';
import type { LlmModelConfig } from '../../types';
import UiTag from '../ui/UiTag.vue';

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

    <div class="ui-table-wrap">
      <table class="ui-table models-table">
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
            </td>
            <td>
              <div class="models-cell-tags-inner">
                <UiTag v-for="type in (model.input || ['text'])" :key="type" shape="rounded" size="sm">
                  {{ t(`settings.models.inputTypes.${type}`, type) }}
                </UiTag>
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

.ui-table th:nth-child(1),
.ui-table td:nth-child(1) { min-width: 220px; white-space: nowrap; }
.ui-table th:nth-child(2),
.ui-table td:nth-child(2) { min-width: 140px; white-space: nowrap; }

.models-cell-type { color: var(--muted); }
.ui-table th.actions-th { min-width: 180px; text-align: right; }
.ui-table td.models-cell-actions {
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
</style>

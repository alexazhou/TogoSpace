<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref, computed } from 'vue';
import { useI18n } from 'vue-i18n';
import { getFrontendConfig, getLlmConfig, saveLlmConfig } from '../../api';
import type { FrontendConfig, LlmConfigPayload, LlmProviderConfig, LlmModelConfig } from '../../types';
import ProviderEditorDialog from './ProviderEditorDialog.vue';
import ModelEditorDialog from './ModelEditorDialog.vue';
import ModelTestDialog from './ModelTestDialog.vue';
import ContextConfigSection from './ContextConfigSection.vue';
import DefaultModelsSection from './DefaultModelsSection.vue';
import SettingsBreadcrumb from './SettingsBreadcrumb.vue';
import ConfirmDialog from '../ui/ConfirmDialog.vue';
import ProviderModelsTable from './ProviderModelsTable.vue';
import type { SettingsBreadcrumbItem } from './types';
import UiTag from '../ui/UiTag.vue';
import { showGlobalSuccessToast } from '../../appUiState';

const props = defineProps<{
  breadcrumbItems: SettingsBreadcrumbItem[];
  detailProviderIndex?: number | null;
}>();

const emit = defineEmits<{
  navigateBreadcrumb: [key: string];
  openProviderModels: [index: number];
  clearProviderModels: [];
}>();

const { t } = useI18n();

function onSettingsReload(): void {
  void loadData();
}

onMounted(() => {
  window.addEventListener('settings-reload', onSettingsReload);
});

onBeforeUnmount(() => {
  window.removeEventListener('settings-reload', onSettingsReload);
});

const config = ref<LlmConfigPayload | null>(null);
const frontendConfig = ref<FrontendConfig | null>(null);
const initialConfigSnapshot = ref<string>('');
const isDirty = computed(() => {
  if (!config.value) return false;
  return JSON.stringify(config.value) !== initialConfigSnapshot.value;
});
const isLoading = ref(false);
const isSaving = ref(false);
const statusText = ref('');

const providerDialogRef = ref<InstanceType<typeof ProviderEditorDialog> | null>(null);
const modelDialogRef = ref<InstanceType<typeof ModelEditorDialog> | null>(null);
const saveConfirmOpen = ref(false);
const deleteProviderConfirmOpen = ref(false);
const deleteProviderIndex = ref<number>(-1);
const deleteModelConfirmOpen = ref(false);
const deleteModelInfo = ref<{ providerIndex: number; modelIndex: number }>({ providerIndex: -1, modelIndex: -1 });

// We need to keep track of which provider we are editing models for
const currentEditingProviderIndex = ref<number | null>(null);
const currentEditingModelIndex = ref<number | null>(null);
const currentEditingProviderIndexForEdit = ref<number | null>(null);

async function loadData(): Promise<void> {
  isLoading.value = true;
  statusText.value = '';
  try {
    const [llmConfig, fc] = await Promise.all([getLlmConfig(), getFrontendConfig()]);
    config.value = llmConfig;
    frontendConfig.value = fc;
    // Initialize context_config with defaults if null
    if (!config.value.context_config) {
      config.value.context_config = { ...fc.context_config_defaults };
    }
    initialConfigSnapshot.value = JSON.stringify(config.value);
  } catch (error) {
    console.error(error);
    statusText.value = t('settings.models.loadFailed', 'Failed to load configuration');
  } finally {
    isLoading.value = false;
  }
}

function resetChanges(): void {
  if (!initialConfigSnapshot.value) return;
  config.value = JSON.parse(initialConfigSnapshot.value) as LlmConfigPayload;
}

function requestSave(): void {
  saveConfirmOpen.value = true;
}

function closeSaveConfirm(): void {
  saveConfirmOpen.value = false;
}

async function confirmSave(): Promise<void> {
  saveConfirmOpen.value = false;
  if (!config.value) return;
  isSaving.value = true;
  statusText.value = '';
  try {
    await saveLlmConfig(config.value);
    initialConfigSnapshot.value = JSON.stringify(config.value);
    showGlobalSuccessToast(t('settings.models.saveSuccess', 'Configuration saved successfully!'));
  } catch (error) {
    console.error(error);
    statusText.value = t('settings.models.saveFailed', 'Failed to save configuration');
  } finally {
    isSaving.value = false;
  }
}

// Provider actions
function openAddProvider() {
  currentEditingProviderIndexForEdit.value = null;
  providerDialogRef.value?.openCreate();
}

function openEditProvider(index: number) {
  if (!config.value) return;
  currentEditingProviderIndexForEdit.value = index;
  providerDialogRef.value?.openEdit(config.value.llm_providers[index]);
}

function requestDeleteProvider(index: number) {
  deleteProviderIndex.value = index;
  deleteProviderConfirmOpen.value = true;
}

function confirmDeleteProvider() {
  if (!config.value) return;
  config.value.llm_providers.splice(deleteProviderIndex.value, 1);
  deleteProviderConfirmOpen.value = false;
  showGlobalSuccessToast(t('settings.models.deletePending'));
}

function handleProviderSave(provider: LlmProviderConfig) {
  if (!config.value) return;
  if (currentEditingProviderIndexForEdit.value !== null) {
    provider.models = config.value.llm_providers[currentEditingProviderIndexForEdit.value].models;
    config.value.llm_providers[currentEditingProviderIndexForEdit.value] = provider;
  } else {
    config.value.llm_providers.push(provider);
  }
}

// Model actions
function openAddModel(providerIndex: number) {
  currentEditingProviderIndex.value = providerIndex;
  currentEditingModelIndex.value = null;
  modelDialogRef.value?.openCreate();
}

function openEditModel(providerIndex: number, modelIndex: number) {
  if (!config.value) return;
  currentEditingProviderIndex.value = providerIndex;
  currentEditingModelIndex.value = modelIndex;
  modelDialogRef.value?.openEdit(config.value.llm_providers[providerIndex].models[modelIndex]);
}

function requestDeleteModel(providerIndex: number, modelIndex: number) {
  deleteModelInfo.value = { providerIndex, modelIndex };
  deleteModelConfirmOpen.value = true;
}

function confirmDeleteModel() {
  if (!config.value) return;
  const { providerIndex, modelIndex } = deleteModelInfo.value;
  config.value.llm_providers[providerIndex].models.splice(modelIndex, 1);
  deleteModelConfirmOpen.value = false;
  showGlobalSuccessToast(t('settings.models.deletePending'));
}

function handleModelSave(model: LlmModelConfig) {
  if (!config.value || currentEditingProviderIndex.value === null) return;
  const pIndex = currentEditingProviderIndex.value;
  if (currentEditingModelIndex.value !== null) {
    config.value.llm_providers[pIndex].models[currentEditingModelIndex.value] = model;
  } else {
    config.value.llm_providers[pIndex].models.push(model);
  }
}

// Test connectivity
const modelTestDialog = ref<InstanceType<typeof ModelTestDialog> | null>(null);
function testProvider(providerIndex: number) {
  if (!config.value) return;
  modelTestDialog.value?.openFromProvider(config.value.llm_providers, providerIndex);
}
function testModel(providerIndex: number, modelIndex: number) {
  if (!config.value) return;
  modelTestDialog.value?.openFromModel(config.value.llm_providers, providerIndex, modelIndex);
}

onMounted(() => {
  void loadData();
});
</script>

<template>
  <section id="models" class="config-section">
    <SettingsBreadcrumb :items="breadcrumbItems" @navigate="emit('navigateBreadcrumb', $event)" />

    <div v-if="statusText" class="section-status-bar">
      <span class="section-status">{{ statusText }}</span>
    </div>

    <p v-if="isLoading" class="models-empty">{{ t('settings.models.loading', 'Loading configuration...') }}</p>

    <div v-else-if="config" class="config-content">
      
      <!-- Default Models Settings -->
      <DefaultModelsSection
        :default-models="config.default_models"
        :providers="config.llm_providers"
        @save="config.default_models = $event"
      />

      <!-- Context Config -->
      <ContextConfigSection
        v-if="frontendConfig"
        :config="config.context_config"
        :defaults="frontendConfig.context_config_defaults"
        @save="config.context_config = $event"
      />

      <!-- Providers View -->
      <section v-if="detailProviderIndex == null" class="providers-section">
        <div class="providers-header">
          <h4>{{ t('settings.models.providersTitle', 'LLM Providers') }}</h4>
          <button type="button" class="secondary-button" @click="openAddProvider">
            {{ t('settings.models.addProvider', 'Add Provider') }}
          </button>
        </div>

        <div class="models-table-wrap">
          <table class="settings-table models-table">
            <thead>
              <tr>
                <th>{{ t('settings.models.table.providerName', 'Provider Name') }}</th>
                <th>{{ t('settings.models.table.type', 'Type') }}</th>
                <th>{{ t('settings.models.table.modelCount', 'Model Count') }}</th>
                <th class="status-th">{{ t('settings.models.table.status', 'Status') }}</th>
                <th class="actions-th">{{ t('settings.models.table.actions', 'Actions') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(provider, pIndex) in config.llm_providers" :key="pIndex" :class="{'provider-disabled': !provider.enable}">
                <td><strong>{{ provider.name }}</strong></td>
                <td><span class="models-cell-type">{{ provider.type }}</span></td>
                <td class="models-cell-tags">
                  <div class="models-cell-tags-inner">
                    <UiTag v-for="(model, mIndex) in provider.models" :key="mIndex" shape="rounded" size="sm">
                      {{ model.name }}
                    </UiTag>
                  </div>
                </td>
                <td>
                  <UiTag :tone="provider.enable ? 'success' : 'muted'" size="sm">
                    {{ t(provider.enable ? 'settings.models.enabled' : 'settings.models.disabled', provider.enable ? 'Enabled' : 'Disabled') }}
                  </UiTag>
                </td>
                <td class="models-cell-actions">
                  <div class="models-cell-actions-inner">
                    <button type="button" class="ghost-button" @click="testProvider(pIndex)">{{ t('settings.models.table.testBtn', 'Test') }}</button>
                    <button type="button" class="ghost-button" @click="openEditProvider(pIndex)">{{ t('common.edit') }}</button>
                    <button type="button" class="ghost-button" @click="emit('openProviderModels', pIndex)">{{ t('settings.models.table.modelsBtn', 'Models') }}</button>
                    <button type="button" class="ghost-button text-danger" @click="requestDeleteProvider(pIndex)">{{ t('common.delete') }}</button>
                  </div>
                </td>
              </tr>
              <tr v-if="config.llm_providers.length === 0">
                <td colspan="5" class="models-empty">{{ t('settings.models.table.emptyProviders', 'No providers configured yet.') }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- Models View -->
      <ProviderModelsTable
        v-else-if="detailProviderIndex != null && config.llm_providers[detailProviderIndex]"
        :provider-name="config.llm_providers[detailProviderIndex].name"
        :models="config.llm_providers[detailProviderIndex].models"
        @back="emit('clearProviderModels')"
        @add="openAddModel(detailProviderIndex)"
        @test="(mIndex) => testModel(detailProviderIndex!, mIndex)"
        @edit="(mIndex) => openEditModel(detailProviderIndex!, mIndex)"
        @delete="(mIndex) => requestDeleteModel(detailProviderIndex!, mIndex)"
      />

    </div>

    <div class="section-footer">
      <button v-if="isDirty" type="button" class="secondary-button" @click="resetChanges">
        {{ t('settings.models.resetChanges', 'Reset Changes') }}
      </button>
      <button type="button" class="primary-button" :disabled="isSaving || !config || !isDirty" @click="requestSave">
        {{ isSaving ? t('settings.models.saving', 'Saving...') : t('settings.models.saveAllBtn', 'Save All Changes') }}
      </button>
    </div>

    <ConfirmDialog
      :open="saveConfirmOpen"
      :title="t('settings.models.saveConfirmTitle', 'Save Configuration')"
      :message="t('settings.models.saveConfirmMsg', 'Are you sure you want to save the current configuration? This will overwrite the existing settings.')"
      :confirm-label="t('common.confirm')"
      @close="closeSaveConfirm"
      @confirm="confirmSave"
    />

    <ConfirmDialog
      :open="deleteProviderConfirmOpen"
      :title="t('settings.models.deleteProviderTitle', 'Delete Provider')"
      :message="t('settings.models.deleteProviderMsg', 'Are you sure you want to delete this provider? This action cannot be undone.')"
      :confirm-label="t('common.confirm')"
      danger
      @close="deleteProviderConfirmOpen = false"
      @confirm="confirmDeleteProvider"
    />

    <ConfirmDialog
      :open="deleteModelConfirmOpen"
      :title="t('settings.models.deleteModelTitle', 'Delete Model')"
      :message="t('settings.models.deleteModelMsg', 'Are you sure you want to delete this model? This action cannot be undone.')"
      :confirm-label="t('common.confirm')"
      danger
      @close="deleteModelConfirmOpen = false"
      @confirm="confirmDeleteModel"
    />

    <ProviderEditorDialog ref="providerDialogRef" @save="handleProviderSave" />
    <ModelEditorDialog ref="modelDialogRef" @save="handleModelSave" />
    <ModelTestDialog ref="modelTestDialog" />
  </section>
</template>

<style scoped>
.config-section { padding: 12px 0 0; }
.section-status-bar { margin-bottom: 8px; }
.section-status, .models-empty { color: var(--muted); font-size: 0.86rem; }

.section-footer {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 12px;
  padding-top: 16px;
  margin-top: 16px;
  border-top: 1px solid color-mix(in srgb, var(--divider) 76%, transparent);
}

.config-content { display: grid; gap: 24px; margin-top: 10px; }

.providers-header {
  display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;
}
.providers-header h4 { margin: 0; color: var(--text-strong); font-size: 0.95rem; }

.provider-card {
  background: var(--settings-table-surface);
  border-radius: 16px;
  padding: 16px;
  margin-bottom: 16px;
  border: 1px solid var(--panel-border);
}
.provider-disabled { opacity: 0.7; }

.provider-head {
  display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;
}
.provider-title { display: flex; align-items: center; gap: 8px; }
.provider-title strong { color: var(--text-strong); font-size: 1.05rem; }
.provider-actions { display: flex; gap: 8px; }
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
.settings-table td:nth-child(1) { min-width: 120px; white-space: nowrap; }
.settings-table th:nth-child(2),
.settings-table td:nth-child(2) { min-width: 96px; white-space: nowrap; }
.settings-table th:nth-child(3),
.settings-table td:nth-child(3) { min-width: 140px; }

.models-cell-type { color: var(--muted); }
.models-cell-tags {
  min-width: 140px;
}
.models-cell-tags-inner {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.settings-table th.status-th,
.settings-table td:nth-child(4) { min-width: 76px; white-space: nowrap; }
.settings-table th.actions-th { min-width: 220px; text-align: right; }
.settings-table td.models-cell-actions {
  min-width: 220px;
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

.add-model-row {
  padding: 8px;
  background: var(--settings-table-head-bg);
  border-top: 1px solid var(--panel-border);
  text-align: center;
}

</style>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import type { LlmProviderConfig } from '../../types';
import { getProviderPresets } from '../../api';
import ToggleSwitch from '../ui/ToggleSwitch.vue';
import CustomSelect from '../ui/CustomSelect.vue';
import ModalDialog from '../ui/ModalDialog.vue';
import BaseUrlSection from './BaseUrlSection.vue';

type EditorMode = 'create' | 'edit';

const providerPresets = ref<Record<string, { label: string; [key: string]: string }>>({});
const SERVICE_TYPES = computed(() =>
  Object.entries(providerPresets.value).map(([value, v]) => ({ value, label: v.label }))
);
const URL_PROTOCOL_TYPES = [
  { value: 'openai', label: 'OpenAI' },
  { value: 'anthropic', label: 'Anthropic' },
];

const emit = defineEmits<{
  save: [provider: LlmProviderConfig];
}>();

const { t } = useI18n();

const visible = ref(false);
const mode = ref<EditorMode>('create');
const apiKeyVisible = ref(false);

const urlsForm = ref<{ type: string; url: string }[]>([]);

onMounted(async () => {
  await loadPresets();
});

async function loadPresets() {
  try {
    providerPresets.value = await getProviderPresets();
  } catch (e) {
    console.error('Failed to load provider presets:', e);
  }
}

const form = ref({
  name: '',
  enable: true,
  type: 'other',
  api_key: '',
});

function handleTypeChange() {
  // 不自动填充 URL，留空时后端会自动使用预设 URL
}

const isCreating = computed(() => mode.value === 'create');
const dialogTitle = computed(() => (
  isCreating.value ? t('settings.models.newProviderTitle', 'New Provider') : (form.value.name || t('settings.models.detailFallback'))
));
const dialogEyebrow = computed(() => (isCreating.value ? 'New Provider' : 'Provider Detail'));

const canSave = computed(() => {
  return form.value.name.trim().length > 0 && form.value.api_key.trim().length > 0;
});

const currentTypePresetUrls = computed(() => {
  const preset = providerPresets.value[form.value.type];
  if (!preset) return {};
  return Object.fromEntries(
    Object.entries(preset).filter(([k]) => k !== 'label')
  );
});

function closeDialog(): void {
  visible.value = false;
}

function openCreate(): void {
  mode.value = 'create';
  urlsForm.value = [{ type: 'openai', url: '' }];
  form.value = {
    name: '',
    enable: true,
    type: 'other',
    api_key: '',
  };
  apiKeyVisible.value = false;
  visible.value = true;
  void loadPresets();
}

function openEdit(provider: LlmProviderConfig): void {
  mode.value = 'edit';

  urlsForm.value = [];
  if (provider.urls) {
    for (const [k, v] of Object.entries(provider.urls)) {
      if (v) urlsForm.value.push({ type: k, url: v });
    }
  }

  form.value = {
    name: provider.name,
    enable: provider.enable ?? true,
    type: provider.type || 'other',
    api_key: provider.api_key || '',
  };
  apiKeyVisible.value = false;
  visible.value = true;
  void loadPresets();
}

function handleSave(): void {
  if (!canSave.value) return;

  try {
    const urlsToSave: Record<string, string> = {};
    urlsForm.value.forEach(item => {
      if (item.url.trim()) urlsToSave[item.type] = item.url.trim();
    });

    const provider: LlmProviderConfig = {
      name: form.value.name.trim(),
      enable: form.value.enable,
      type: form.value.type as any,
      api_key: form.value.api_key.trim(),
      urls: urlsToSave,
      models: [], // to be merged by parent
    };
    emit('save', provider);
    closeDialog();
  } catch (error) {
    alert(error instanceof Error ? error.message : 'Invalid parameters');
  }
}

defineExpose({ openCreate, openEdit });
</script>

<template>
  <ModalDialog
    :open="visible"
    :title="dialogTitle"
    :eyebrow="dialogEyebrow"
    :width="760"
    @close="closeDialog"
  >
    <template #head-extra>
      <div style="margin-top: 14px;">
        <ToggleSwitch variant="inline" :checked="form.enable" :label="form.enable ? t('settings.models.enabled') : t('settings.models.disabled')" @toggle="form.enable = $event" />
      </div>
    </template>

        <div class="svc-form-grid">
          <label class="svc-field">
            <span>{{ t('settings.models.typeLabel') }}</span>
            <CustomSelect
              v-model="form.type"
              :options="SERVICE_TYPES"
              class="svc-custom-select"
              @update:model-value="handleTypeChange"
            />
          </label>

          <label class="svc-field">
            <span>{{ t('settings.models.nameLabel') }}</span>
            <input v-model="form.name" type="text" class="svc-input" :readonly="!isCreating" :class="{ 'svc-input--readonly': !isCreating }" placeholder="e.g. OpenAI" />
          </label>

          <div class="svc-field svc-field--wide">
            <span>Base URLs</span>
            <BaseUrlSection
              :urls="urlsForm"
              :protocol-types="URL_PROTOCOL_TYPES"
              :preset-urls="currentTypePresetUrls"
              @save="urlsForm = $event"
            />
          </div>

          <label class="svc-field svc-field--wide">
            <span>API Key</span>
            <div class="svc-input-wrapper">
              <input v-model="form.api_key" :type="apiKeyVisible ? 'text' : 'password'" class="svc-input svc-input--flex" placeholder="sk-..." />
              <button type="button" class="eye-icon-btn" @click="apiKeyVisible = !apiKeyVisible">
                <svg v-if="!apiKeyVisible" xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>
                <svg v-else xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path><line x1="1" y1="1" x2="23" y2="23"></line></svg>
              </button>
            </div>
          </label>
        </div>

        <template #footer-trailing>
      <button type="button" class="secondary-button" @click="closeDialog">{{ t('common.cancel') }}</button>
      <button type="button" class="secondary-button" :disabled="!canSave" @click="handleSave">{{ t('common.confirm') }}</button>
    </template>
  </ModalDialog>
</template>

<style scoped>
/* Inherit from existing styles */
.svc-form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.svc-field { display: grid; gap: 6px; }
.svc-field--wide { grid-column: 1 / -1; }
.svc-field > span { color: var(--muted); font-size: 0.76rem; }
.svc-input, .svc-select { height: 40px; width: 100%; border: 1px solid var(--panel-border); border-radius: 12px; background: var(--panel-bg); color: var(--text-strong); padding: 0 12px; font: inherit; font-size: 0.88rem; box-sizing: border-box; }
:deep(.svc-custom-select .custom-select__button) { min-height: 40px; height: 40px; padding: 0 12px; border-radius: 12px; font-size: 0.88rem; border-color: var(--panel-border); background: var(--panel-bg); }
:deep(.svc-custom-select .custom-select__button:hover), :deep(.svc-custom-select.is-open .custom-select__button) { border-color: var(--focus-border); }
:deep(.svc-custom-select .custom-select__option) { min-height: 38px; padding: 0 12px; font-size: 0.88rem; border-radius: 8px; }
.svc-input-wrapper { position: relative; display: flex; align-items: center; }
.svc-input-wrapper .svc-input { padding-right: 40px; }
.eye-icon-btn {
  position: absolute; right: 8px; top: 50%; transform: translateY(-50%);
  display: flex; align-items: center; justify-content: center;
  width: 28px; height: 28px; padding: 0; border: none; border-radius: 6px;
  background: transparent; color: var(--muted); cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.eye-icon-btn:hover { background: color-mix(in srgb, var(--text-strong) 8%, transparent); color: var(--text-strong); }
.svc-input--flex { flex: 1; min-width: 0; }
</style>

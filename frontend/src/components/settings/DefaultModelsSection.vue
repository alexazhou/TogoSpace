<script setup lang="ts">
import { ref, computed } from 'vue';
import { useI18n } from 'vue-i18n';
import type { DefaultModelSlots, LlmProviderConfig } from '../../types';
import HoverTooltip from '../ui/HoverTooltip.vue';
import InfoTooltip from '../ui/InfoTooltip.vue';
import ModelSlotItem from './ModelSlotItem.vue';
import ModalDialog from '../ui/ModalDialog.vue';

const props = defineProps<{
  defaultModels: DefaultModelSlots;
  providers: LlmProviderConfig[];
}>();

const emit = defineEmits<{
  save: [models: DefaultModelSlots];
}>();

const { t } = useI18n();

const visible = ref(false);
const form = ref<DefaultModelSlots>({
  primary: '',
  lite: '',
  vision: '',
  advanced: '',
});

const enabledProviders = computed(() =>
  props.providers.filter(p => p.enable && p.models.length > 0)
);

const modelSlots = [
  { key: 'primary' as const, labelKey: 'settings.models.primaryModel', descKey: 'settings.models.primaryModelDesc', fallbackLabel: 'Primary Model', fallbackDesc: 'Default model for general tasks' },
  { key: 'lite' as const, labelKey: 'settings.models.lightweightModel', descKey: 'settings.models.lightweightModelDesc', fallbackLabel: 'Lightweight Model', fallbackDesc: 'Faster model for simple tasks' },
  { key: 'advanced' as const, labelKey: 'settings.models.advancedModel', descKey: 'settings.models.advancedModelDesc', fallbackLabel: 'Advanced Model', fallbackDesc: 'High-capability model for complex tasks' },
  { key: 'vision' as const, labelKey: 'settings.models.visionModel', descKey: 'settings.models.visionModelDesc', fallbackLabel: 'Vision Model', fallbackDesc: 'Model capable of processing images' },
];

function openDialog(): void {
  form.value = { ...props.defaultModels };
  visible.value = true;
}

function closeDialog(): void {
  visible.value = false;
}

function handleSave(): void {
  emit('save', { ...form.value });
  closeDialog();
}

function resolveModelName(value: string | null): string {
  if (!value) return t('common.notConfigured', '未配置');
  const atIndex = value.indexOf('@');
  return atIndex === -1 ? value : value.substring(0, atIndex);
}

function resolveProviderName(value: string | null): string {
  if (!value) return '';
  const atIndex = value.indexOf('@');
  return atIndex === -1 ? '' : value.substring(atIndex + 1);
}
</script>

<template>
  <section class="default-models-section">
    <div class="default-models-header">
      <h4>
        {{ t('settings.models.defaultModelsTitle', 'Model Config') }}
        <InfoTooltip position="right" :text="t('settings.models.defaultModelsDesc', 'Pre-defined model sets for different scenarios, assignable to agents as needed. If not specified, the primary model is used.')" />
      </h4>
      <button type="button" class="ghost-button" @click="openDialog">
        {{ t('common.edit') }}
      </button>
    </div>

    <div class="default-models-tags">
      <span v-for="slot in modelSlots" :key="slot.key" class="model-tag">
        <span class="model-tag-label">{{ t(slot.labelKey, slot.fallbackLabel) }}</span>
        <span class="model-tag-value">
          <span>{{ resolveModelName(defaultModels[slot.key]) }}</span>
          <HoverTooltip v-if="resolveProviderName(defaultModels[slot.key])" :text="t('settings.models.providerLabel', '供应商')">
            <span class="model-tag-provider">{{ resolveProviderName(defaultModels[slot.key]) }}</span>
          </HoverTooltip>
        </span>
      </span>
    </div>

    <!-- Edit Dialog -->
    <ModalDialog
      :open="visible"
      :title="t('settings.models.defaultModelsTitle', 'Model Config')"
      eyebrow="Model Config"
      :width="560"
      @close="closeDialog"
    >
      <div class="editor-form">
            <ModelSlotItem
              v-for="slot in modelSlots"
              :key="slot.key"
              :label="t(slot.labelKey, slot.fallbackLabel)"
              :description="t(slot.descKey, slot.fallbackDesc)"
              :model-value="form[slot.key]"
              :providers="enabledProviders"
              @update:model-value="form[slot.key] = $event"
            />
          </div>

          <template #footer-trailing>
        <button type="button" class="secondary-button" @click="closeDialog">{{ t('common.cancel') }}</button>
        <button type="button" class="secondary-button" @click="handleSave">{{ t('common.confirm') }}</button>
      </template>
    </ModalDialog>
  </section>
</template>

<style scoped>
.default-models-section {
  padding: 0;
}

.default-models-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.default-models-header h4 {
  margin: 0;
  color: var(--text-strong);
  font-size: 0.95rem;
}

.default-models-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.model-tag {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border-radius: 8px;
  background: var(--panel-bg);
  border: 1px solid var(--panel-border);
  font-size: 0.84rem;
}

.model-tag-label {
  color: var(--muted);
}

.model-tag-value {
  color: var(--text-strong);
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 6px;
}

.model-tag-provider {
  padding: 1px 6px;
  border-radius: 4px;
  background: color-mix(in srgb, var(--accent) 14%, transparent);
  border: 1px solid color-mix(in srgb, var(--accent) 28%, transparent);
  color: var(--accent);
  font-size: 0.72rem;
  font-weight: 500;
  white-space: nowrap;
}

/* Dialog styles */
.editor-form { display: grid; gap: 14px; }
</style>

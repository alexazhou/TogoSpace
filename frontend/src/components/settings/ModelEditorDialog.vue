<script setup lang="ts">
import { computed, ref } from 'vue';
import { useI18n } from 'vue-i18n';
import type { LlmModelConfig, LlmContextConfig } from '../../types';
import ContextConfigSection from './ContextConfigSection.vue';
import ExtraParamsConfigSection from './ExtraParamsConfigSection.vue';
import HeadersConfigSection from './HeadersConfigSection.vue';
import ModalDialog from '../ui/ModalDialog.vue';
import FormField from '../ui/FormField.vue';

type EditorMode = 'create' | 'edit';

const emit = defineEmits<{
  save: [model: LlmModelConfig];
}>();

const { t } = useI18n();

const visible = ref(false);
const mode = ref<EditorMode>('create');

const INPUT_OPTIONS = [
  { value: 'text', label: 'Text' },
  { value: 'image', label: 'Image' },
  { value: 'audio', label: 'Audio' },
  { value: 'video', label: 'Video' },
] as const;

const form = ref({
  name: '',
  protocol: 'openai',
  input: ['text'] as string[],
  context_window_tokens: null as number | null,
  reserve_output_tokens: null as number | null,
  compact_trigger_ratio: null as number | null,
  compact_summary_max_tokens: null as number | null,
  extra_headers: {} as Record<string, string>,
  extra_params: '',
});

function toggleInput(type: string): void {
  // text 是基线能力，不可取消
  if (type === 'text') return;
  const idx = form.value.input.indexOf(type);
  if (idx >= 0) {
    form.value.input.splice(idx, 1);
  } else {
    form.value.input.push(type);
  }
}

const isCreating = computed(() => mode.value === 'create');
const dialogTitle = computed(() => (
  isCreating.value ? t('settings.models.newModelTitle', 'New Model') : (form.value.name || t('settings.models.detailFallback'))
));
const dialogEyebrow = computed(() => (isCreating.value ? 'New Model' : 'Model Detail'));
const advancedOpen = ref(false);

const canSave = computed(() => {
  return form.value.name.trim().length > 0 && form.value.protocol.trim().length > 0;
});

const contextConfigForComponent = computed<LlmContextConfig>(() => ({
  context_window_tokens: form.value.context_window_tokens,
  reserve_output_tokens: form.value.reserve_output_tokens,
  compact_trigger_ratio: form.value.compact_trigger_ratio,
  compact_summary_max_tokens: form.value.compact_summary_max_tokens,
}));

function handleContextConfigSave(config: LlmContextConfig): void {
  form.value.context_window_tokens = config.context_window_tokens ?? null;
  form.value.reserve_output_tokens = config.reserve_output_tokens ?? null;
  form.value.compact_trigger_ratio = config.compact_trigger_ratio ?? null;
  form.value.compact_summary_max_tokens = config.compact_summary_max_tokens ?? null;
}

function handleHeadersConfigSave(headers: Record<string, string>): void {
  form.value.extra_headers = cloneHeaders(headers);
}

function handleExtraParamsSave(paramsText: string): void {
  form.value.extra_params = paramsText;
}

function closeDialog(): void {
  visible.value = false;
}

function openCreate(): void {
  mode.value = 'create';
  form.value = {
    name: '',
    protocol: 'openai',
    input: ['text'],
    context_window_tokens: null,
    reserve_output_tokens: null,
    compact_trigger_ratio: null,
    compact_summary_max_tokens: null,
    extra_headers: {},
    extra_params: '',
  };
  advancedOpen.value = false;
  visible.value = true;
}

function openEdit(model: LlmModelConfig): void {
  mode.value = 'edit';
  form.value = {
    name: model.name,
    protocol: model.protocol || 'openai',
    input: model.input && model.input.length > 0 ? [...model.input] : ['text'],
    context_window_tokens: model.context_config?.context_window_tokens ?? null,
    reserve_output_tokens: model.context_config?.reserve_output_tokens ?? null,
    compact_trigger_ratio: model.context_config?.compact_trigger_ratio ?? null,
    compact_summary_max_tokens: model.context_config?.compact_summary_max_tokens ?? null,
    extra_headers: cloneHeaders(model.extra_headers),
    extra_params: serializeProviderParams(model.extra_params),
  };
  advancedOpen.value = false;
  visible.value = true;
}

function cloneHeaders(headers: Record<string, string> | undefined | null): Record<string, string> {
  return { ...(headers ?? {}) };
}

function serializeProviderParams(params: Record<string, unknown> | undefined | null): string {
  if (!params || Object.keys(params).length === 0) return '';
  return JSON.stringify(params, null, 2);
}

function parseProviderParams(text: string): Record<string, unknown> {
  const trimmed = text.trim();
  if (!trimmed) return {};
  try {
    const parsed = JSON.parse(trimmed);
    if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
      throw new Error();
    }
    return parsed;
  } catch {
    throw new Error(t('settings.models.extraParamsInvalid'));
  }
}

function handleSave(): void {
  if (!canSave.value) return;

  try {
    const model: LlmModelConfig = {
      name: form.value.name.trim(),
      protocol: form.value.protocol.trim(),
      input: Array.from(new Set([...form.value.input, 'text'])),
      context_config: {
        context_window_tokens: form.value.context_window_tokens,
        reserve_output_tokens: form.value.reserve_output_tokens,
        compact_trigger_ratio: form.value.compact_trigger_ratio,
        compact_summary_max_tokens: form.value.compact_summary_max_tokens,
      },
      extra_headers: cloneHeaders(form.value.extra_headers),
      extra_params: parseProviderParams(form.value.extra_params),
    };
    emit('save', model);
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
    :width="600"
    @close="closeDialog"
  >
    <div class="svc-form-grid">
          <FormField :label="t('settings.models.modelNameLabel', 'Model Name')">
            <input
              id="model-editor-name"
              v-model="form.name"
              name="model_name"
              type="text"
              class="svc-input"
              placeholder="e.g. gpt-4o"
            />
          </FormField>

          <FormField :label="t('settings.models.protocolLabel', 'Protocol')">
            <select
              id="model-editor-protocol"
              v-model="form.protocol"
              name="protocol"
              class="svc-input svc-select"
            >
              <option value="openai">OpenAI</option>
              <option value="anthropic">Anthropic</option>
            </select>
          </FormField>
        </div>

        <FormField
          :label="t('settings.models.inputLabel', 'Supported Input Types')"
          :hint="t('settings.models.inputHint', 'Text is always enabled. Models declaring Image can receive image messages.')"
          wide
        >
          <div class="input-types">
            <label
              v-for="opt in INPUT_OPTIONS"
              :key="opt.value"
              class="input-type-chip"
              :class="{ checked: form.input.includes(opt.value), disabled: opt.value === 'text' }"
            >
              <input
                type="checkbox"
                :checked="form.input.includes(opt.value)"
                :disabled="opt.value === 'text'"
                @change="toggleInput(opt.value)"
              />
              <span>{{ t(`settings.models.inputTypes.${opt.value}`, opt.label) }}</span>
            </label>
          </div>
        </FormField>

        <section class="advanced-card">
          <button type="button" class="advanced-toggle" :aria-expanded="advancedOpen" @click="advancedOpen = !advancedOpen">
            <div>
              <p class="advanced-eyebrow">Advanced</p>
              <strong>{{ t('settings.models.advanced') }}</strong>
            </div>
            <span class="advanced-toggle__state">{{ advancedOpen ? t('common.collapse') : t('common.expand') }}</span>
          </button>

          <div v-if="advancedOpen" class="advanced-grid">
            <div class="svc-field--wide">
              <ContextConfigSection
                :config="contextConfigForComponent"
                @save="handleContextConfigSave"
              />
            </div>

            <div class="svc-field--wide">
              <HeadersConfigSection
                :headers="form.extra_headers"
                @save="handleHeadersConfigSave"
              />
            </div>

            <div class="svc-field--wide">
              <ExtraParamsConfigSection
                :model-name="form.name"
                :protocol="form.protocol"
                :params-text="form.extra_params"
                @save="handleExtraParamsSave"
              />
            </div>
          </div>
        </section>

        <template #footer-trailing>
      <button type="button" class="secondary-button" @click="closeDialog">{{ t('common.cancel') }}</button>
      <button type="button" class="secondary-button" :disabled="!canSave" @click="handleSave">{{ t('common.confirm') }}</button>
    </template>
  </ModalDialog>
</template>

<style scoped>
.advanced-eyebrow { margin: 0; color: var(--accent); text-transform: uppercase; letter-spacing: 0.14em; font-size: 0.68rem; }
.input-types { display: flex; flex-wrap: wrap; gap: 16px; }
.input-type-chip { display: inline-flex; align-items: center; gap: 6px; padding: 4px 0; color: var(--muted); font-size: 0.88rem; cursor: pointer; user-select: none; transition: color 0.2s; }
.input-type-chip:hover:not(.disabled) { color: var(--text-strong); }
.input-type-chip.checked { color: var(--text-strong); }
.input-type-chip.disabled { opacity: 0.5; cursor: not-allowed; }
.input-type-chip input { accent-color: var(--accent); width: 16px; height: 16px; cursor: pointer; margin: 0; }
.input-type-chip.disabled input { cursor: not-allowed; }
.advanced-card { border: 1px solid var(--panel-border); border-radius: 12px; padding: 12px; }
.advanced-toggle { display: flex; width: 100%; justify-content: space-between; align-items: center; background: transparent; border: none; padding: 0; cursor: pointer; text-align: left; }
.advanced-toggle strong { color: var(--text-strong); font-size: 0.9rem; }
.advanced-toggle__state { color: var(--muted); font-size: 0.8rem; }
.advanced-grid { margin-top: 14px; padding-top: 14px; border-top: 1px solid var(--panel-border); display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
@media (max-width: 640px) {
  .advanced-grid { grid-template-columns: 1fr; }
}
</style>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { useI18n } from 'vue-i18n';
import type { LlmModelConfig, LlmContextConfig } from '../../types';
import ContextConfigSection from './ContextConfigSection.vue';
import ExtraParamsConfigSection from './ExtraParamsConfigSection.vue';
import HeadersConfigSection from './HeadersConfigSection.vue';

type EditorMode = 'create' | 'edit';

const emit = defineEmits<{
  save: [model: LlmModelConfig];
}>();

const { t } = useI18n();

const visible = ref(false);
const mode = ref<EditorMode>('create');

const form = ref({
  name: '',
  protocol: 'openai',
  context_window_tokens: null as number | null,
  reserve_output_tokens: null as number | null,
  compact_trigger_ratio: null as number | null,
  compact_summary_max_tokens: null as number | null,
  extra_headers: {} as Record<string, string>,
  extra_params: '',
});

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
  <Teleport to="body">
    <div v-if="visible" class="editor-overlay" @click.self="closeDialog">
      <section class="editor-dialog panel scrollbar-thin">
        <header class="editor-head">
          <div class="editor-head-copy">
            <p class="editor-eyebrow">{{ dialogEyebrow }}</p>
            <h3>{{ dialogTitle }}</h3>
          </div>
          <div class="editor-head-actions">
            <button type="button" class="ghost-button editor-close" @click="closeDialog">×</button>
          </div>
        </header>

        <div class="svc-form-grid">
          <label class="svc-field">
            <span>{{ t('settings.models.modelNameLabel', 'Model Name') }}</span>
            <input
              id="model-editor-name"
              v-model="form.name"
              name="model_name"
              type="text"
              class="svc-input"
              placeholder="e.g. gpt-4o"
            />
          </label>

          <label class="svc-field">
            <span>{{ t('settings.models.protocolLabel', 'Protocol') }}</span>
            <select
              id="model-editor-protocol"
              v-model="form.protocol"
              name="protocol"
              class="svc-input svc-select"
            >
              <option value="openai">OpenAI</option>
              <option value="anthropic">Anthropic</option>
            </select>
          </label>
        </div>

        <section class="advanced-card">
          <button type="button" class="advanced-toggle" :aria-expanded="advancedOpen" @click="advancedOpen = !advancedOpen">
            <div>
              <p class="editor-eyebrow">Advanced</p>
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

        <footer class="editor-actions">
          <div class="editor-actions-leading"></div>
          <div class="editor-actions-trailing">
            <button type="button" class="secondary-button" @click="closeDialog">{{ t('common.cancel') }}</button>
            <button type="button" class="secondary-button" :disabled="!canSave" @click="handleSave">{{ t('common.confirm') }}</button>
          </div>
        </footer>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.editor-overlay { position: fixed; inset: 0; z-index: 80; display: grid; place-items: center; padding: 20px; background: rgba(6, 10, 16, 0.56); backdrop-filter: blur(10px); }
.editor-dialog { width: min(600px, calc(100vw - 40px)); max-height: calc(100vh - 40px); padding: 18px; display: grid; gap: 14px; overflow: auto; background: var(--panel-bg); border-radius: 12px; }
.editor-head, .editor-actions { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.editor-head-copy { min-width: 0; }
.editor-close { min-width: 32px; height: 32px; padding: 0; font-size: 1rem; }
.editor-eyebrow { margin: 0; color: var(--accent); text-transform: uppercase; letter-spacing: 0.14em; font-size: 0.68rem; }
.editor-head h3 { margin: 0; color: var(--text-strong); }
.svc-form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.svc-field { display: grid; gap: 6px; }
.svc-field--wide { grid-column: 1 / -1; }
.svc-field > span { color: var(--muted); font-size: 0.76rem; }
.svc-input, .svc-select { height: 40px; width: 100%; border: 1px solid var(--panel-border); border-radius: 12px; background: var(--panel-bg); color: var(--text-strong); padding: 0 12px; font: inherit; font-size: 0.88rem; box-sizing: border-box; }
.advanced-card { border: 1px solid var(--panel-border); border-radius: 12px; padding: 12px; }
.advanced-toggle { display: flex; width: 100%; justify-content: space-between; align-items: center; background: transparent; border: none; padding: 0; cursor: pointer; text-align: left; }
.advanced-toggle strong { color: var(--text-strong); font-size: 0.9rem; }
.advanced-toggle__state { color: var(--muted); font-size: 0.8rem; }
.advanced-grid { margin-top: 14px; padding-top: 14px; border-top: 1px solid var(--panel-border); display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.editor-actions-trailing { display: flex; gap: 8px; justify-content: flex-end; }
@media (max-width: 640px) {
  .svc-form-grid, .advanced-grid { grid-template-columns: 1fr; }
}
</style>

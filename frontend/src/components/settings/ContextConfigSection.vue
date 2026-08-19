<script setup lang="ts">
import { ref, computed } from 'vue';
import { useI18n } from 'vue-i18n';
import type { LlmContextConfig } from '../../types';
import InfoTooltip from '../ui/InfoTooltip.vue';
import ModalDialog from '../ui/ModalDialog.vue';
import FormField from '../ui/FormField.vue';

const props = withDefaults(defineProps<{
  config: LlmContextConfig;
  defaults?: LlmContextConfig;
}>(), {
  defaults: () => ({
    context_window_tokens: 131072,
    reserve_output_tokens: 16384,
    compact_trigger_ratio: 0.85,
    compact_summary_max_tokens: 6144,
  }),
});

const emit = defineEmits<{
  save: [config: LlmContextConfig];
}>();

const { t } = useI18n();

const visible = ref(false);
const form = ref<LlmContextConfig>({
  context_window_tokens: null,
  reserve_output_tokens: null,
  compact_trigger_ratio: null,
  compact_summary_max_tokens: null,
});

function openDialog(): void {
  form.value = { ...props.config };
  visible.value = true;
}

function handleReset(): void {
  form.value = {
    context_window_tokens: null,
    reserve_output_tokens: null,
    compact_trigger_ratio: null,
    compact_summary_max_tokens: null,
  };
}

function clearField(field: keyof LlmContextConfig): void {
  form.value[field] = null;
}

function closeDialog(): void {
  visible.value = false;
}

function handleSave(): void {
  emit('save', { ...form.value });
  closeDialog();
}

function formatNumber(n: number | null | undefined, defaultVal: number): string {
  if (n == null) return `自动 (${formatNum(defaultVal)})`;
  return formatNum(n);
}

function formatNum(n: number): string {
  if (n >= 1000000) return `${(n / 1000000).toFixed(1)}M`;
  if (n >= 1000) return `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}K`;
  return String(n);
}

function formatRatio(n: number | null | undefined): string {
  if (n == null) return `自动 (${((props.defaults.compact_trigger_ratio ?? 0.85) * 100).toFixed(0)}%)`;
  return `${(n * 100).toFixed(0)}%`;
}
</script>

<template>
  <section class="context-config-section">
    <div class="context-config-header">
      <h4>
        {{ t('settings.models.contextConfigTitle', 'Context Config') }}
        <InfoTooltip position="right" :text="t('settings.models.contextConfigDesc', 'Context window and compaction settings')" />
      </h4>
      <button type="button" class="ghost-button" @click="openDialog">
        {{ t('common.edit') }}
      </button>
    </div>

    <div class="context-config-tags">
      <span class="context-tag">
        <span class="context-tag-label">{{ t('settings.models.contextWindowTokens', 'Context Window Tokens') }}</span>
        <span class="context-tag-value">{{ formatNumber(config.context_window_tokens, defaults.context_window_tokens ?? 131072) }}</span>
      </span>
      <span class="context-tag">
        <span class="context-tag-label">{{ t('settings.models.reserveOutputTokens', 'Reserve Output Tokens') }}</span>
        <span class="context-tag-value">{{ formatNumber(config.reserve_output_tokens, defaults.reserve_output_tokens ?? 16384) }}</span>
      </span>
      <span class="context-tag">
        <span class="context-tag-label">{{ t('settings.models.compactTriggerRatio', 'Compact Trigger Ratio') }}</span>
        <span class="context-tag-value">{{ formatRatio(config.compact_trigger_ratio) }}</span>
      </span>
      <span class="context-tag">
        <span class="context-tag-label">{{ t('settings.models.compactSummaryMaxTokens', 'Compact Summary Max Tokens') }}</span>
        <span class="context-tag-value">{{ formatNumber(config.compact_summary_max_tokens, defaults.compact_summary_max_tokens ?? 6144) }}</span>
      </span>
    </div>

    <!-- Edit Dialog -->
    <ModalDialog
      :open="visible"
      :title="t('settings.models.contextConfigTitle', 'Context Config')"
      eyebrow="Context Config"
      :width="480"
      @close="closeDialog"
    >
      <div class="editor-form">
            <FormField>
              <template #label>
                {{ t('settings.models.contextWindowTokens', 'Context Window Tokens') }}
                <InfoTooltip position="right" :text="t('settings.models.contextWindowTokensDesc', 'Maximum context window size in tokens')" />
              </template>
              <div class="input-with-clear">
                <input v-model.number="form.context_window_tokens" type="number" class="gu-input number-input" min="0" step="1024" :placeholder="`自动 (${defaults.context_window_tokens ?? 131072})`" />
                <button v-if="form.context_window_tokens != null" type="button" class="clear-btn" @click="clearField('context_window_tokens')">⟳</button>
              </div>
            </FormField>

            <FormField>
              <template #label>
                {{ t('settings.models.reserveOutputTokens', 'Reserve Output Tokens') }}
                <InfoTooltip position="right" :text="t('settings.models.reserveOutputTokensDesc', 'Reserved tokens for model output')" />
              </template>
              <div class="input-with-clear">
                <input v-model.number="form.reserve_output_tokens" type="number" class="gu-input number-input" min="0" step="256" :placeholder="`自动 (${defaults.reserve_output_tokens ?? 16384})`" />
                <button v-if="form.reserve_output_tokens != null" type="button" class="clear-btn" @click="clearField('reserve_output_tokens')">⟳</button>
              </div>
            </FormField>

            <FormField>
              <template #label>
                {{ t('settings.models.compactTriggerRatio', 'Compact Trigger Ratio') }}
                <InfoTooltip position="right" :text="t('settings.models.compactTriggerRatioDesc', 'Ratio of context usage to trigger compaction (0-1)')" />
              </template>
              <div class="input-with-clear">
                <input v-model.number="form.compact_trigger_ratio" type="number" class="gu-input number-input" min="0" max="1" step="0.05" :placeholder="`自动 (${defaults.compact_trigger_ratio ?? 0.85})`" />
                <button v-if="form.compact_trigger_ratio != null" type="button" class="clear-btn" @click="clearField('compact_trigger_ratio')">⟳</button>
              </div>
            </FormField>

            <FormField>
              <template #label>
                {{ t('settings.models.compactSummaryMaxTokens', 'Compact Summary Max Tokens') }}
                <InfoTooltip position="right" :text="t('settings.models.compactSummaryMaxTokensDesc', 'Maximum tokens for compaction summary')" />
              </template>
              <div class="input-with-clear">
                <input v-model.number="form.compact_summary_max_tokens" type="number" class="gu-input number-input" min="0" step="256" :placeholder="`自动 (${defaults.compact_summary_max_tokens ?? 6144})`" />
                <button v-if="form.compact_summary_max_tokens != null" type="button" class="clear-btn" @click="clearField('compact_summary_max_tokens')">⟳</button>
              </div>
            </FormField>
          </div>

          <template #footer-trailing>
        <button type="button" class="secondary-button" @click="closeDialog">{{ t('common.cancel') }}</button>
        <button type="button" class="secondary-button" @click="handleSave">{{ t('common.confirm') }}</button>
      </template>
    </ModalDialog>
  </section>
</template>

<style scoped>
.context-config-section {
  padding: 0;
}

.context-config-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.context-config-header h4 {
  margin: 0;
  color: var(--text-strong);
  font-size: 0.95rem;
}

.context-config-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.context-tag {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border-radius: 8px;
  background: var(--panel-bg);
  border: 1px solid var(--panel-border);
  font-size: 0.84rem;
}

.context-tag-label {
  color: var(--muted);
}

.context-tag-value {
  color: var(--text-strong);
  font-weight: 600;
}

/* Dialog body */
.editor-form { display: grid; gap: 12px; }
/* 数字输入框局部修饰：叠加在全局 gu-input 之上 */
.number-input {
  height: auto;
  padding: 8px 12px;
}
.input-with-clear {
  position: relative;
}
.input-with-clear .number-input {
  padding-right: 32px;
}
.clear-btn {
  position: absolute;
  right: 8px;
  top: 50%;
  transform: translateY(-50%);
  border: none;
  background: none;
  color: var(--muted);
  cursor: pointer;
  padding: 2px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: color 0.15s ease;
}
.clear-btn:hover {
  color: var(--text-strong);
}
.number-input[type="number"] { -moz-appearance: textfield; }
.number-input[type="number"]::-webkit-outer-spin-button,
.number-input[type="number"]::-webkit-inner-spin-button { -webkit-appearance: none; margin: 0; }
</style>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import ToggleSwitch from '../ui/ToggleSwitch.vue';
import CustomSelect from '../ui/CustomSelect.vue';
import ModalDialog from '../ui/ModalDialog.vue';
import {
  extraParamsConfigTemplates,
  findExtraParamsConfigTemplateById,
  findExtraParamsConfigTemplateForModel,
} from './extraParamsConfig/templates';
import type {
  ExtraParamsConfigTemplate,
  JsonObject,
  VisualConfigField,
  VisualConfigOption,
  VisualConfigState,
  VisualFieldValue,
  VisualConfigSchema,
} from './extraParamsConfig/types';

const props = defineProps<{
  modelName: string;
  protocol: string;
  paramsText: string;
}>();

const emit = defineEmits<{
  save: [paramsText: string];
}>();

const { t } = useI18n();

const visible = ref(false);
const selectedTemplateId = ref<string>('other');
const jsonText = ref('{}');
const jsonError = ref('');
const visualState = ref<VisualConfigState>({});

const selectedTemplate = computed<ExtraParamsConfigTemplate>(() => {
  return extraParamsConfigTemplates.find(t => t.id === selectedTemplateId.value) || extraParamsConfigTemplates[extraParamsConfigTemplates.length - 1];
});

const visualConfigSchema = computed<VisualConfigSchema | null>(() => selectedTemplate.value.getVisualSchema());
const hasVisualConfig = computed(() => visualConfigSchema.value !== null && visualConfigSchema.value.fields.length > 0);
const visualFields = computed(() => visualConfigSchema.value?.fields || []);

watch(() => props.modelName, () => {
  const matched = extraParamsConfigTemplates.find(tmpl => tmpl.match(props.modelName));
  if (matched && matched.id !== 'other') {
    selectedTemplateId.value = matched.id;
  }
}, { immediate: true });

watch(() => props.paramsText, (newText) => {
  try {
    const params = newText ? JSON.parse(newText) : {};
    visualState.value = selectedTemplate.value.readVisualState(params, props.protocol);
  } catch (e) {
    visualState.value = selectedTemplate.value.getDefaultState();
  }
}, { immediate: true });

const displayText = computed(() => {
  const parsed = parseJsonObject(props.paramsText);
  if (!parsed || Object.keys(parsed).length === 0) {
    return '{}';
  }
  return JSON.stringify(parsed, null, 2);
});

const isDisplayEmpty = computed(() => {
  const parsed = parseJsonObject(props.paramsText);
  return !parsed || Object.keys(parsed).length === 0;
});

function isJsonObject(value: unknown): value is JsonObject {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function parseJsonObject(text: string): JsonObject | null {
  const trimmed = text.trim();
  if (!trimmed) {
    return {};
  }

  try {
    const parsed = JSON.parse(trimmed);
    return isJsonObject(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function readJsonForEdit(): JsonObject | null {
  const parsed = parseJsonObject(jsonText.value);
  if (!parsed) {
    jsonError.value = t('settings.models.extraParamsInvalid');
    return null;
  }
  jsonError.value = '';
  return parsed;
}

function formatJsonForEditor(params: JsonObject): string {
  return JSON.stringify(params, null, 2);
}

function syncVisualFromJson(params: JsonObject): void {
  visualState.value = selectedTemplate.value.readVisualState(params, props.protocol);
}

function writeVisualToJson(): void {
  const current = parseJsonObject(jsonText.value);
  if (!current) {
    jsonError.value = t('settings.models.extraParamsInvalid');
    return;
  }

  const next = selectedTemplate.value.writeVisualState(current, visualState.value, props.protocol);
  jsonText.value = formatJsonForEditor(next);
  jsonError.value = '';
}

function openDialog(): void {
  selectedTemplateId.value = findExtraParamsConfigTemplateForModel(props.modelName).id;
  const current = parseJsonObject(props.paramsText) ?? {};
  jsonText.value = formatJsonForEditor(current);
  jsonError.value = '';
  syncVisualFromJson(current);
  writeVisualToJson();
  visible.value = true;
}

function closeDialog(): void {
  visible.value = false;
}

function handleTemplateChange(): void {
  visualState.value = selectedTemplate.value.getDefaultState();
  const next = selectedTemplate.value.writeVisualState({}, visualState.value, props.protocol);
  jsonText.value = formatJsonForEditor(next);
  jsonError.value = '';
}

function isFieldVisible(field: VisualConfigField): boolean {
  if (!field.visibleWhen) {
    return true;
  }
  return visualState.value[field.visibleWhen.key] === field.visibleWhen.equals;
}

function getBooleanFieldValue(key: string): boolean {
  return visualState.value[key] === true;
}

function getStringFieldValue(key: string): string {
  const value = visualState.value[key];
  return typeof value === 'string' ? value : '';
}

function getNumberFieldValue(key: string): number | '' {
  const value = visualState.value[key];
  return typeof value === 'number' ? value : '';
}

function getSelectOptions(field: VisualConfigField): VisualConfigOption[] {
  return field.control === 'select' ? field.options : [];
}

function getNumberMin(field: VisualConfigField): number | undefined {
  return field.control === 'number' ? field.min : undefined;
}

function getNumberMax(field: VisualConfigField): number | undefined {
  return field.control === 'number' ? field.max : undefined;
}

function getNumberStep(field: VisualConfigField): number {
  return field.control === 'number' ? (field.step ?? 1) : 1;
}

function setVisualFieldValue(key: string, value: VisualFieldValue): void {
  visualState.value = {
    ...visualState.value,
    [key]: value,
  };
  writeVisualToJson();
}

function handleNumberFieldInput(key: string, event: Event): void {
  const target = event.target as HTMLInputElement;
  const nextValue = target.value.trim() ? Number(target.value) : null;
  setVisualFieldValue(key, typeof nextValue === 'number' && Number.isFinite(nextValue) ? nextValue : null);
}

function handleJsonInput(): void {
  const parsed = parseJsonObject(jsonText.value);
  if (!parsed) {
    jsonError.value = t('settings.models.extraParamsInvalid');
    return;
  }

  jsonError.value = '';
  syncVisualFromJson(parsed);
}

function handleSave(): void {
  const parsed = readJsonForEdit();
  if (!parsed) return;
  emit('save', formatJsonForEditor(parsed));
  closeDialog();
}
</script>

<template>
  <section class="extra-params-section">
    <div class="extra-params-header">
      <h4>{{ t('settings.extraParamsConfig.title') }}</h4>
      <button type="button" class="ghost-button" @click="openDialog">
        {{ t('common.edit') }}
      </button>
    </div>

    <pre class="extra-params-preview" :class="{ 'is-empty': isDisplayEmpty }">{{ displayText }}</pre>

    <ModalDialog
      :open="visible"
      :title="t('settings.extraParamsConfig.title')"
      :eyebrow="t('settings.extraParamsConfig.eyebrow')"
      :width="640"
      :z-index="90"
      @close="closeDialog"
    >
      <div class="extra-params-editor">
            <section class="editor-block">
              <div class="config-row">
                <span class="row-label">{{ t('settings.extraParamsConfig.modelTemplate') }}</span>
                <div class="input-wrap">
                  <!-- Model template names are API/product identifiers; keep them raw and do not localize. -->
                  <CustomSelect
                    v-model="selectedTemplateId"
                    :options="extraParamsConfigTemplates.map(template => ({ value: template.id, label: template.displayName }))"
                    compact
                    @update:modelValue="handleTemplateChange"
                  />
                </div>
              </div>
              <div class="config-row">
                <span class="row-label">{{ t('settings.models.protocolLabel', 'Protocol') }}</span>
                <div class="input-wrap">
                  <CustomSelect
                    :modelValue="props.protocol"
                    :options="[{ value: props.protocol, label: props.protocol === 'openai' ? 'OpenAI' : (props.protocol === 'anthropic' ? 'Anthropic' : props.protocol) }]"
                    compact
                    disabled
                  />
                </div>
              </div>
            </section>

            <section v-if="hasVisualConfig" class="editor-block">
              <template v-for="field in visualFields" :key="field.key">
                <div
                  v-if="isFieldVisible(field) && field.control === 'switch'"
                  class="config-row"
                >
                  <span class="row-label">{{ t(field.labelKey, field.fallbackLabel) }}</span>
                  <div class="input-wrap switch-wrap">
                    <ToggleSwitch
                      variant="inline"
                      :checked="getBooleanFieldValue(field.key)"
                      @toggle="setVisualFieldValue(field.key, $event)"
                    />
                  </div>
                </div>

                <div
                  v-else-if="isFieldVisible(field) && field.control === 'select'"
                  class="config-row"
                >
                  <span class="row-label">{{ t(field.labelKey, field.fallbackLabel) }}</span>
                  <div class="input-wrap">
                    <CustomSelect
                      :modelValue="getStringFieldValue(field.key)"
                      :options="getSelectOptions(field).map(opt => ({ value: opt.value, label: opt.labelKey ? t(opt.labelKey, opt.fallbackLabel) : opt.fallbackLabel }))"
                      compact
                      @update:modelValue="setVisualFieldValue(field.key, $event)"
                    />
                  </div>
                </div>

                <div
                  v-else-if="isFieldVisible(field) && field.control === 'number'"
                  class="config-row"
                >
                  <span class="row-label">{{ t(field.labelKey, field.fallbackLabel) }}</span>
                  <div class="input-wrap">
                    <input
                      type="number"
                      class="gu-input compact-input"
                      :value="getNumberFieldValue(field.key)"
                      :min="getNumberMin(field)"
                      :max="getNumberMax(field)"
                      :step="getNumberStep(field)"
                      @input="handleNumberFieldInput(field.key, $event)"
                    />
                  </div>
                </div>
              </template>
            </section>

            <section class="editor-block">
              <h4>{{ t('settings.extraParamsConfig.value') }}</h4>
              <textarea
                id="extra-params-json"
                v-model="jsonText"
                name="extra_params_json"
                class="gu-textarea gu-textarea--code"
                rows="8"
                spellcheck="false"
                @input="handleJsonInput"
              ></textarea>
              <p v-if="jsonError" class="editor-error">{{ jsonError }}</p>
            </section>
          </div>

          <template #footer-trailing>
        <button type="button" class="secondary-button" @click="closeDialog">{{ t('common.cancel') }}</button>
        <button type="button" class="secondary-button" :disabled="!!jsonError" @click="handleSave">
          {{ t('common.confirm') }}
        </button>
      </template>
    </ModalDialog>
  </section>
</template>

<style scoped>
.extra-params-section {
  padding: 0;
}

.extra-params-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}

.extra-params-header h4 {
  margin: 0;
  color: var(--muted);
  font-size: 0.76rem;
  font-weight: 400;
  line-height: 1.35;
}

.extra-params-preview {
  width: 100%;
  min-height: 116px;
  max-height: 220px;
  overflow: auto;
  margin: 0;
  padding: 12px;
  border: 1px solid var(--panel-border);
  border-radius: 12px;
  background: var(--panel-bg);
  color: var(--text-strong);
  font-family: monospace;
  font-size: 0.8rem;
  line-height: 1.5;
  white-space: pre-wrap;
}

.extra-params-preview.is-empty {
  color: var(--muted);
}


.extra-params-editor {
  display: grid;
  gap: 14px;
}

.editor-block {
  display: grid;
  gap: 10px;
}

.editor-block h4 {
  margin: 0;
  color: var(--muted);
  font-size: 0.76rem;
  font-weight: 400;
}

.compact-input {
  height: 32px;
  width: 100%;
  border-radius: 6px;
  font-size: 13px;
  padding: 4px 10px;
  text-align: right;
}

.editor-note {
  margin: 0;
  color: var(--muted);
  font-size: 0.76rem;
  line-height: 1.45;
}

.editor-error {
  margin: 0;
  color: var(--danger);
  font-size: 0.76rem;
  line-height: 1.45;
}

.config-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 4px 0;
}

.row-label {
  font-size: 13px;
  color: var(--text-strong);
  width: 120px;
  flex-shrink: 0;
}

.input-wrap {
  flex: 1;
  min-width: 0;
}

.switch-wrap {
  display: flex;
  align-items: center;
  height: 32px;
}

.readonly-text {
  font-size: 13px;
  color: var(--text-strong);
  display: flex;
  align-items: center;
  justify-content: flex-end;
  height: 32px;
  padding: 0 10px;
}

</style>

<script setup lang="ts">
import { useI18n } from 'vue-i18n';

const props = withDefaults(defineProps<{
  name: string;
  workingDirectory: string;
  slogan: string;
  rules: string;
  readonly?: boolean;
  editableName?: boolean;
  showWorkingDirectory?: boolean;
}>(), {
  readonly: false,
  editableName: true,
  showWorkingDirectory: true,
});

const emit = defineEmits<{
  'update:name': [value: string];
  'update:workingDirectory': [value: string];
  'update:slogan': [value: string];
  'update:rules': [value: string];
}>();

const { t } = useI18n();

function displayValue(value: string, fallback?: string): string {
  return value.trim() || (fallback ?? t('common.notSet'));
}
</script>

<template>
  <section class="name-panel" :class="{ readonly: props.readonly }">
    <div class="panel-head">
      <span class="panel-title">{{ t('team.info') }}</span>
      <span v-if="props.readonly" class="panel-badge">{{ t('team.readonly') }}</span>
    </div>

    <template v-if="props.readonly">
      <div class="info-table">
        <div class="info-row">
          <span class="info-key">{{ t('team.name') }}</span>
          <div class="info-value">{{ displayValue(name) }}</div>
        </div>

        <div v-if="props.showWorkingDirectory" class="info-row">
          <span class="info-key">{{ t('team.workDir') }}</span>
          <div class="info-value info-value-path">{{ displayValue(workingDirectory) }}</div>
        </div>

        <div class="info-row">
          <span class="info-key">{{ t('team.slogan') }}</span>
          <div class="info-value">{{ displayValue(slogan) }}</div>
        </div>

        <div class="info-row info-row-multiline">
          <span class="info-key">{{ t('team.responsibility') }}</span>
          <div class="info-value info-value-multiline">{{ displayValue(rules) }}</div>
        </div>
      </div>
    </template>

    <template v-else>
      <div class="edit-grid">
        <label class="edit-field">
          <span class="field-label">{{ t('team.name') }}</span>
          <input
            :value="name"
            type="text"
            :placeholder="t('team.namePlaceholder')"
            :disabled="!props.editableName"
            @input="emit('update:name', ($event.target as HTMLInputElement).value)"
          />
        </label>

        <label v-if="props.showWorkingDirectory" class="edit-field">
          <span class="field-label">{{ t('team.workDir') }}</span>
          <input
            :value="workingDirectory"
            type="text"
            :placeholder="t('team.workDirPlaceholder')"
            @input="emit('update:workingDirectory', ($event.target as HTMLInputElement).value)"
          />
        </label>

        <label class="edit-field">
          <span class="field-label">{{ t('team.slogan') }}</span>
          <input
            :value="slogan"
            type="text"
            :placeholder="t('team.sloganPlaceholder')"
            @input="emit('update:slogan', ($event.target as HTMLInputElement).value)"
          />
        </label>

        <label class="edit-field edit-field-wide">
          <span class="field-label">{{ t('team.responsibility') }}</span>
          <textarea
            :value="rules"
            rows="3"
            :placeholder="t('team.responsibilityPlaceholder')"
            @input="emit('update:rules', ($event.target as HTMLTextAreaElement).value)"
          ></textarea>
        </label>
      </div>
    </template>

    <div v-if="$slots.actions" class="panel-actions">
      <slot name="actions"></slot>
    </div>
  </section>
</template>

<style scoped>
.name-panel {
  display: grid;
  gap: 8px;
  border: 1px solid var(--team-create-panel-border);
  border-radius: 20px;
  background: var(--panel-bg);
  box-shadow: var(--panel-shadow);
  padding: 10px 12px;
  align-content: start;
}

.panel-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 8px;
}

.panel-actions :deep(.ghost-button),
.panel-actions :deep(.secondary-button) {
  height: 34px;
  padding: 0 16px;
  justify-content: center;
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.panel-title {
  color: var(--text-strong);
  font-size: 0.96rem;
  font-weight: 700;
  letter-spacing: 0.01em;
}

.panel-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 48px;
  height: 22px;
  padding: 0 8px;
  border: 1px solid color-mix(in srgb, var(--focus-border) 26%, var(--panel-border) 74%);
  border-radius: 999px;
  background: color-mix(in srgb, var(--selected) 48%, var(--panel-bg) 52%);
  color: var(--muted);
  font-size: 0.68rem;
  font-weight: 600;
}

.field-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin-top: 2px;
}

.edit-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px 14px;
  margin-top: 2px;
}

.edit-field {
  display: grid;
  gap: 6px;
  min-width: 0;
}

.edit-field-wide {
  grid-column: 1 / -1;
}

.field-label {
  color: var(--muted);
  font-size: 0.75rem;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.info-table {
  margin-top: 4px;
  border: 1px solid color-mix(in srgb, var(--focus-border) 16%, var(--panel-border) 84%);
  border-radius: 14px;
  background: color-mix(in srgb, var(--surface-soft) 72%, var(--panel-bg) 28%);
  overflow: hidden;
}

.info-row {
  display: grid;
  grid-template-columns: 112px minmax(0, 1fr);
  gap: 12px;
  align-items: center;
  padding: 10px 12px;
  border-top: 1px solid color-mix(in srgb, var(--focus-border) 10%, var(--panel-border) 90%);
}

.info-row:first-child {
  border-top: none;
}

.info-row-multiline {
  align-items: flex-start;
}

.info-key {
  color: var(--muted);
  font-size: 0.75rem;
  font-weight: 700;
  letter-spacing: 0.04em;
}

.info-value {
  min-width: 0;
  color: var(--text-strong);
  font-size: 0.9rem;
  line-height: 1.45;
  word-break: break-word;
}

.info-value-path {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, Liberation Mono, monospace;
  font-size: 0.83rem;
  color: color-mix(in srgb, var(--text-strong) 88%, var(--accent) 12%);
}

.info-value-multiline {
  white-space: pre-wrap;
}

input,
textarea {
  border: 1px solid var(--team-create-control-border);
  border-radius: 12px;
  background: var(--surface-soft);
  color: var(--text-strong);
  padding: 0 12px;
  outline: none;
  box-shadow: none;
  font-size: 0.9rem;
  transition:
    border-color 0.18s ease,
    box-shadow 0.18s ease,
    background 0.18s ease;
}

input {
  height: 36px;
}

textarea {
  min-height: 84px;
  resize: vertical;
  padding: 10px 12px;
  line-height: 1.45;
}

input:disabled,
textarea:disabled {
  opacity: 0.72;
  cursor: not-allowed;
}

input:focus,
textarea:focus {
  border-color: var(--focus-border);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--focus-border) 18%, transparent);
  background: var(--panel-bg);
}

@media (max-width: 780px) {
  .edit-grid,
  .info-row {
    grid-template-columns: 1fr;
  }

  .info-row {
    gap: 6px;
    padding: 10px;
  }
}
</style>

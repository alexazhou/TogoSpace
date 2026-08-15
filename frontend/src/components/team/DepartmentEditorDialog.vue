<script setup lang="ts">
import { useI18n } from 'vue-i18n';

defineProps<{
  open: boolean;
  editable?: boolean;
  memberName: string;
  departmentName: string;
  departmentResponsibility: string;
}>();

const emit = defineEmits<{
  close: [];
  save: [];
  'update:departmentName': [value: string];
  'update:departmentResponsibility': [value: string];
}>();

const { t } = useI18n();
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="department-editor-overlay" @click.self="emit('close')">
      <section
        class="department-editor-dialog panel"
        :class="{ 'department-editor-dialog--readonly': !editable }"
      >
        <div class="department-editor-head">
          <div class="department-editor-title-row">
            <h2 class="department-editor-title">{{ editable ? t('dept.editTitle') : t('dept.viewTitle') }}</h2>
            <p class="section-eyebrow">{{ t('dept.eyebrow') }}</p>
          </div>
          <p class="department-editor-meta">{{ t('dept.manager', { name: memberName }) }}</p>
        </div>

        <label class="department-editor-field">
          <span>{{ t('dept.name') }}</span>
          <input
            :value="departmentName"
            class="department-editor-input"
            :class="{ 'department-editor-input--readonly': !editable }"
            type="text"
            :placeholder="editable ? t('dept.namePlaceholderEditable') : ''"
            :readonly="!editable"
            @input="emit('update:departmentName', ($event.target as HTMLInputElement).value)"
          />
        </label>

        <label class="department-editor-field">
          <span>{{ t('dept.responsibility') }}</span>
          <textarea
            :value="departmentResponsibility"
            class="department-editor-input department-editor-textarea"
            :class="{ 'department-editor-input--readonly': !editable }"
            rows="4"
            :placeholder="editable ? t('dept.responsibilityPlaceholderEditable') : ''"
            :readonly="!editable"
            @input="emit('update:departmentResponsibility', ($event.target as HTMLTextAreaElement).value)"
          ></textarea>
        </label>

        <div class="department-editor-actions">
          <button type="button" class="ghost-button" @click="emit('close')">{{ editable ? t('common.cancel') : t('common.close') }}</button>
          <button v-if="editable" type="button" class="secondary-button" @click="emit('save')">{{ t('common.save') }}</button>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.department-editor-overlay {
  position: fixed;
  inset: 0;
  z-index: 50;
  display: grid;
  place-items: center;
  padding: 28px;
  background: rgba(6, 10, 16, 0.44);
  backdrop-filter: blur(8px);
}

.department-editor-dialog {
  width: min(520px, 100%);
  display: grid;
  gap: 14px;
  padding: 16px;
  border-radius: 20px;
}

.department-editor-head {
  display: grid;
  gap: 6px;
}

.department-editor-title-row {
  display: flex;
  align-items: baseline;
  gap: 12px;
}

.department-editor-title {
  margin: 0;
  color: var(--text-strong);
  font-size: 1.52rem;
  line-height: 1.04;
}

.section-eyebrow {
  margin: 0;
  color: var(--accent);
  text-transform: uppercase;
  letter-spacing: 0.14em;
  font-size: 0.68rem;
}

.department-editor-meta {
  margin: 0;
  color: var(--muted);
  font-size: 0.82rem;
}

.department-editor-field {
  display: grid;
  gap: 8px;
}

.department-editor-field > span {
  color: var(--muted);
  font-size: 0.74rem;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.department-editor-input {
  width: 100%;
  border: 1px solid var(--form-input-border);
  border-radius: 12px;
  background: var(--form-input-bg);
  color: var(--text-strong);
  padding: 14px 18px;
  font-size: 0.98rem;
  outline: none;
  box-shadow: inset 0 0 0 1px var(--form-input-selected-inner);
  transition:
    border-color 0.18s ease,
    background 0.18s ease,
    box-shadow 0.18s ease;
}

.department-editor-input::placeholder {
  color: var(--hint-text);
}

.department-editor-input:focus {
  border-color: var(--focus-border);
  box-shadow:
    inset 0 0 0 1px var(--form-input-focus-inner),
    0 0 0 3px var(--form-input-focus-ring);
}

.department-editor-input--readonly {
  border: 1px dashed var(--form-input-readonly-border);
  background: var(--form-input-readonly-bg);
  color: var(--form-input-readonly-text);
  -webkit-text-fill-color: var(--form-input-readonly-text);
  box-shadow: none;
}

.department-editor-textarea {
  min-height: 112px;
  resize: vertical;
  line-height: 1.5;
  font-family: inherit;
}

.department-editor-dialog--readonly .department-editor-input[readonly] {
  cursor: default;
}

.department-editor-dialog--readonly .department-editor-input:focus {
  border: 1px dashed var(--form-input-readonly-border);
  background: var(--form-input-readonly-bg);
  box-shadow: none;
}

.department-editor-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

.department-editor-actions > button {
  width: 112px;
  min-width: 112px;
  height: 32px;
  padding: 0 14px;
  font-size: 0.84rem;
}
</style>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { useI18n } from 'vue-i18n';
import ModalDialog from '../ui/ModalDialog.vue';

interface HeaderRow {
  id: number;
  key: string;
  value: string;
}

const props = defineProps<{
  headers: Record<string, string> | null;
}>();

const emit = defineEmits<{
  save: [headers: Record<string, string>];
}>();

const { t } = useI18n();

const visible = ref(false);
const nextRowId = ref(1);
const rows = ref<HeaderRow[]>([]);

const headerEntries = computed(() => Object.entries(props.headers ?? {})
  .filter(([key, value]) => key.trim() && value.trim()));

function cloneHeaders(headers: Record<string, string> | null | undefined): Record<string, string> {
  return { ...(headers ?? {}) };
}

function createRow(key = '', value = ''): HeaderRow {
  const row = {
    id: nextRowId.value,
    key,
    value,
  };
  nextRowId.value += 1;
  return row;
}

function openDialog(): void {
  const entries = Object.entries(cloneHeaders(props.headers))
    .filter(([key, value]) => key.trim() && value.trim());
  rows.value = entries.length > 0
    ? entries.map(([key, value]) => createRow(key, value))
    : [];
  visible.value = true;
}

function closeDialog(): void {
  visible.value = false;
}

function addRow(): void {
  rows.value.push(createRow());
}

function removeRow(id: number): void {
  rows.value = rows.value.filter((row) => row.id !== id);
}

function clearRows(): void {
  rows.value = [];
}

function handleSave(): void {
  const headers: Record<string, string> = {};
  for (const row of rows.value) {
    const key = row.key.trim();
    const value = row.value.trim();
    if (key && value) {
      headers[key] = value;
    }
  }
  emit('save', headers);
  closeDialog();
}

function isSensitiveHeader(key: string): boolean {
  const lowerKey = key.toLowerCase();
  return lowerKey.includes('authorization')
    || lowerKey.includes('api-key')
    || lowerKey.includes('apikey')
    || lowerKey.includes('token')
    || lowerKey.includes('secret');
}

function formatHeaderValue(key: string, value: string): string {
  if (isSensitiveHeader(key)) {
    return '••••••';
  }
  if (value.length <= 28) {
    return value;
  }
  return `${value.slice(0, 25)}...`;
}
</script>

<template>
  <section class="headers-config-section">
    <div class="headers-config-header">
      <h4>{{ t('settings.headersConfig.title') }}</h4>
      <button type="button" class="ghost-button" @click="openDialog">
        {{ t('common.edit') }}
      </button>
    </div>

    <div class="headers-config-tags">
      <span v-if="headerEntries.length === 0" class="header-tag header-tag--empty">
        {{ t('settings.headersConfig.empty') }}
      </span>
      <template v-else>
        <span
          v-for="[key, value] in headerEntries"
          :key="key"
          class="header-tag"
        >
          <span class="header-tag-label">{{ key }}</span>
          <span class="header-tag-value">{{ formatHeaderValue(key, value) }}</span>
        </span>
      </template>
    </div>

    <ModalDialog
      :open="visible"
      :title="t('settings.headersConfig.title')"
      :eyebrow="t('settings.headersConfig.eyebrow')"
      :width="620"
      @close="closeDialog"
    >
      <div class="headers-editor">
            <div v-if="rows.length > 0" class="headers-editor-head">
              <span>{{ t('settings.headersConfig.keyLabel') }}</span>
              <span>{{ t('settings.headersConfig.valueLabel') }}</span>
              <span></span>
            </div>

            <div v-else class="headers-editor-empty">
              {{ t('settings.headersConfig.empty') }}
            </div>

            <div
              v-for="row in rows"
              :key="row.id"
              class="headers-editor-row"
            >
              <input
                v-model="row.key"
                type="text"
                class="gu-input"
                :placeholder="t('settings.headersConfig.keyPlaceholder')"
              />
              <input
                v-model="row.value"
                type="text"
                class="gu-input"
                :placeholder="t('settings.headersConfig.valuePlaceholder')"
              />
              <button type="button" class="ghost-button row-remove" @click="removeRow(row.id)">
                {{ t('common.remove') }}
              </button>
            </div>

            <button type="button" class="secondary-button add-header-button" @click="addRow">
              {{ t('settings.headersConfig.add') }}
            </button>
          </div>

          <template #footer-leading>
        <button type="button" class="ghost-button" @click="clearRows">
          {{ t('common.reset') }}
        </button>
      </template>
      <template #footer-trailing>
        <button type="button" class="secondary-button" @click="closeDialog">{{ t('common.cancel') }}</button>
        <button type="button" class="secondary-button" @click="handleSave">{{ t('common.confirm') }}</button>
      </template>
    </ModalDialog>
  </section>
</template>

<style scoped>
.headers-config-section {
  padding: 0;
}

.headers-config-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}

.headers-config-header h4 {
  margin: 0;
  color: var(--muted);
  font-size: 0.76rem;
  font-weight: 400;
  line-height: 1.35;
}

.headers-config-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.header-tag {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  max-width: 100%;
  padding: 6px 12px;
  border-radius: 8px;
  background: var(--panel-bg);
  border: 1px solid var(--panel-border);
  font-size: 0.84rem;
}

.header-tag--empty {
  color: var(--muted);
}

.header-tag-label {
  min-width: 0;
  color: var(--muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.header-tag-value {
  min-width: 0;
  color: var(--text-strong);
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.headers-editor {
  display: grid;
  gap: 10px;
}

.headers-editor-head,
.headers-editor-row {
  display: grid;
  grid-template-columns: minmax(0, 0.9fr) minmax(0, 1.2fr) auto;
  gap: 8px;
  align-items: center;
}

.headers-editor-head {
  color: var(--muted);
  font-size: 0.76rem;
}

.headers-editor-empty {
  min-height: 44px;
  display: grid;
  place-items: center;
  border: 1px dashed var(--panel-border);
  border-radius: 12px;
  color: var(--muted);
  font-size: 0.82rem;
}

.row-remove {
  height: 34px;
  padding: 0 10px;
  white-space: nowrap;
}

.add-header-button {
  width: 100%;
  border-style: dashed;
}

@media (max-width: 640px) {
  .headers-editor-head {
    display: none;
  }

  .headers-editor-row {
    grid-template-columns: 1fr;
    padding: 10px;
    border: 1px solid var(--panel-border);
    border-radius: 12px;
  }

  .row-remove {
    width: 100%;
  }
}
</style>

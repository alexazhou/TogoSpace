<script setup lang="ts">
import { computed, ref } from 'vue';
import { useI18n } from 'vue-i18n';

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

    <Teleport to="body">
      <div v-if="visible" class="editor-overlay" @click.self="closeDialog">
        <section class="editor-dialog panel scrollbar-thin">
          <header class="editor-head">
            <div class="editor-head-copy">
              <p class="editor-eyebrow">{{ t('settings.headersConfig.eyebrow') }}</p>
              <h3>{{ t('settings.headersConfig.title') }}</h3>
            </div>
            <div class="editor-head-actions">
              <button type="button" class="ghost-button editor-close" @click="closeDialog">×</button>
            </div>
          </header>

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
                class="svc-input"
                :placeholder="t('settings.headersConfig.keyPlaceholder')"
              />
              <input
                v-model="row.value"
                type="text"
                class="svc-input"
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

          <footer class="editor-actions">
            <div class="editor-actions-leading">
              <button type="button" class="ghost-button" @click="clearRows">
                {{ t('common.reset') }}
              </button>
            </div>
            <div class="editor-actions-trailing">
              <button type="button" class="secondary-button" @click="closeDialog">{{ t('common.cancel') }}</button>
              <button type="button" class="secondary-button" @click="handleSave">{{ t('common.confirm') }}</button>
            </div>
          </footer>
        </section>
      </div>
    </Teleport>
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

.editor-overlay {
  position: fixed;
  inset: 0;
  z-index: 80;
  display: grid;
  place-items: center;
  padding: 20px;
  background: rgba(6, 10, 16, 0.56);
  backdrop-filter: blur(10px);
}

.editor-dialog {
  width: min(620px, calc(100vw - 40px));
  max-height: calc(100vh - 40px);
  padding: 18px;
  display: grid;
  gap: 14px;
  overflow: auto;
  background: var(--panel-bg);
  border-radius: 12px;
}

.editor-head,
.editor-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.editor-head-copy {
  min-width: 0;
}

.editor-close {
  min-width: 32px;
  height: 32px;
  padding: 0;
  font-size: 1rem;
}

.editor-eyebrow {
  margin: 0;
  color: var(--accent);
  text-transform: uppercase;
  letter-spacing: 0.14em;
  font-size: 0.68rem;
}

.editor-head h3 {
  margin: 0;
  color: var(--text-strong);
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

.svc-input {
  height: 40px;
  width: 100%;
  border: 1px solid var(--panel-border);
  border-radius: 12px;
  background: var(--panel-bg);
  color: var(--text-strong);
  padding: 0 12px;
  font: inherit;
  font-size: 0.88rem;
  box-sizing: border-box;
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

.editor-actions-leading,
.editor-actions-trailing {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
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

  .editor-actions {
    align-items: stretch;
    flex-direction: column;
  }
}
</style>

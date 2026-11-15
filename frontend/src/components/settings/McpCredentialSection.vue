<script setup lang="ts">
import { computed, ref } from 'vue';
import { useI18n } from 'vue-i18n';
import ModalDialog from '../ui/ModalDialog.vue';

/** 凭证层：headers（HTTP 传输）或 env（stdio 子进程环境变量），与后端两层结构对应。 */
type CredentialLayer = 'headers' | 'env';

interface CredentialRow {
  id: number;
  key: string;
  value: string;
}

const props = defineProps<{
  layer: CredentialLayer;
  entries: Record<string, string>;
}>();

const emit = defineEmits<{
  save: [entries: Record<string, string>];
}>();

const { t } = useI18n();

const visible = ref(false);
const nextRowId = ref(1);
const rows = ref<CredentialRow[]>([]);

const entryItems = computed(() => Object.entries(props.entries ?? {})
  .filter(([key, value]) => key.trim() && value.trim()));

const layerTitle = computed(() => (props.layer === 'env'
  ? t('settings.mcpServers.mcpServersEnvTitle')
  : t('settings.mcpServers.mcpServersCredentials')));

const keyLabel = computed(() => (props.layer === 'env'
  ? t('settings.mcpServers.mcpServersEnvKeyLabel')
  : t('settings.mcpServers.mcpServersHeaderKeyLabel')));

/** 与后端 `mask_config_value` 同口径：首个空格/冒号前 token（≤8 字符）+ `***`。 */
function maskValue(value: string): string {
  if (!value) {
    return '***';
  }
  const token = value.split(/[\s:]/, 1)[0] || '';
  const prefix = token.slice(0, 8);
  if (value.length <= prefix.length) {
    return '***';
  }
  return `${prefix}***`;
}

function createRow(key = '', value = ''): CredentialRow {
  const row = { id: nextRowId.value, key, value };
  nextRowId.value += 1;
  return row;
}

function openDialog(): void {
  const items = entryItems.value;
  rows.value = items.map(([key, value]) => createRow(key, value));
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
  const entries: Record<string, string> = {};
  for (const row of rows.value) {
    const key = row.key.trim();
    const value = row.value.trim();
    if (key && value) {
      entries[key] = value;
    }
  }
  emit('save', entries);
  closeDialog();
}
</script>

<template>
  <section class="mcp-credential-section">
    <div class="mcp-credential-head">
      <h4>{{ layerTitle }}</h4>
      <button type="button" class="ghost-button" @click="openDialog">
        {{ t('common.edit') }}
      </button>
    </div>

    <div class="mcp-credential-tags">
      <span v-if="entryItems.length === 0" class="credential-tag credential-tag--empty">
        {{ t('settings.mcpServers.mcpServersCredentialsEmpty') }}
      </span>
      <template v-else>
        <span
          v-for="[key, value] in entryItems"
          :key="key"
          class="credential-tag"
        >
          <span class="credential-tag-label">{{ key }}</span>
          <span class="credential-tag-value">{{ maskValue(value) }}</span>
        </span>
      </template>
    </div>

    <ModalDialog
      :open="visible"
      :title="layerTitle"
      :eyebrow="t('settings.mcpServers.mcpServersCredentialsEyebrow')"
      :width="620"
      :z-index="90"
      @close="closeDialog"
    >
      <div class="mcp-credential-editor">
        <div v-if="rows.length > 0" class="mcp-credential-editor-head">
          <span>{{ keyLabel }}</span>
          <span>{{ t('settings.mcpServers.mcpServersValueLabel') }}</span>
          <span></span>
        </div>

        <div v-else class="mcp-credential-editor-empty">
          {{ t('settings.mcpServers.mcpServersCredentialsEmpty') }}
        </div>

        <div
          v-for="row in rows"
          :key="row.id"
          class="mcp-credential-editor-row"
        >
          <input
            v-model="row.key"
            type="text"
            class="gu-input"
            :placeholder="keyLabel"
            spellcheck="false"
          />
          <input
            v-model="row.value"
            type="text"
            class="gu-input"
            :placeholder="t('settings.mcpServers.mcpServersValueLabel')"
            spellcheck="false"
          />
          <button type="button" class="ghost-button row-remove" @click="removeRow(row.id)">
            {{ t('common.remove') }}
          </button>
        </div>

        <button type="button" class="secondary-button add-row-button" @click="addRow">
          {{ t('settings.mcpServers.mcpServersCredentialsAdd') }}
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
.mcp-credential-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
}

.mcp-credential-head h4 {
  margin: 0;
  color: var(--muted);
  font-size: 0.76rem;
  font-weight: 400;
  line-height: 1.35;
}

.mcp-credential-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.credential-tag {
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

.credential-tag--empty {
  color: var(--muted);
}

.credential-tag-label {
  min-width: 0;
  color: var(--muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.credential-tag-value {
  min-width: 0;
  color: var(--text-strong);
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mcp-credential-editor {
  display: grid;
  gap: 10px;
}

.mcp-credential-editor-head,
.mcp-credential-editor-row {
  display: grid;
  grid-template-columns: minmax(0, 0.9fr) minmax(0, 1.2fr) auto;
  gap: 8px;
  align-items: center;
}

.mcp-credential-editor-head {
  color: var(--muted);
  font-size: 0.76rem;
}

.mcp-credential-editor-empty {
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

.add-row-button {
  width: 100%;
  border-style: dashed;
}

@media (max-width: 640px) {
  .mcp-credential-editor-head {
    display: none;
  }

  .mcp-credential-editor-row {
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

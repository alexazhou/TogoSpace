<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useI18n } from 'vue-i18n';
import {
  listMcpServers,
  saveMcpServer,
  deleteMcpServer,
  setMcpServerEnabled,
  type McpServerEntry,
} from '../../api';
import { showGlobalSuccessToast, showGlobalRequestError } from '../../appUiState';
import ToggleSwitch from '../ui/ToggleSwitch.vue';
import FormField from '../ui/FormField.vue';
import SettingsBreadcrumb from './SettingsBreadcrumb.vue';
import type { SettingsBreadcrumbItem } from './types';

const KIND_OPTIONS = ['mcp', 'openapi', 'mcp_stdio', 'graphql'] as const;

const props = defineProps<{
  breadcrumbItems: SettingsBreadcrumbItem[];
}>();

const emit = defineEmits<{
  navigateBreadcrumb: [key: string];
}>();

const { t } = useI18n();

const servers = ref<McpServerEntry[]>([]);
const isLoading = ref(false);
const loadFailed = ref(false);
const isSubmitting = ref(false);
const showForm = ref(false);
const isEditing = ref(false);
const showFullCredentials = ref(false);
const deleteTargetName = ref<string | null>(null);

type FormDraft = {
  name: string;
  kind: string;
  target: string;
  note: string;
  credentialsText: string;
  overwrite: boolean;
};

const emptyDraft = (): FormDraft => ({
  name: '',
  kind: 'mcp',
  target: '',
  note: '',
  credentialsText: '',
  overwrite: false,
});

const formDraft = ref<FormDraft>(emptyDraft());
const isNameEditable = computed(() => !isEditing.value);

function credentialsToString(entry: McpServerEntry | null): string {
  const creds = entry?.credentials;
  if (!creds || Object.keys(creds).length === 0) {
    return '';
  }
  return JSON.stringify(creds, null, 2);
}

function openCreate(): void {
  isEditing.value = false;
  formDraft.value = emptyDraft();
  showForm.value = true;
}

function openEdit(entry: McpServerEntry): void {
  isEditing.value = true;
  formDraft.value = {
    name: entry.name,
    kind: entry.kind,
    target: entry.target,
    note: entry.note,
    credentialsText: credentialsToString(entry),
    overwrite: true,
  };
  showForm.value = true;
}

function closeForm(): void {
  showForm.value = false;
  formDraft.value = emptyDraft();
}

async function load(): Promise<void> {
  isLoading.value = true;
  loadFailed.value = false;
  try {
    servers.value = await listMcpServers();
  } catch {
    loadFailed.value = true;
    showGlobalRequestError({ title: t('settings.mcpServers.mcpServersLoadFailed'), path: '/mcp/servers.json' });
  } finally {
    isLoading.value = false;
  }
}

async function submitForm(): Promise<void> {
  let credentials: Record<string, Record<string, string>> | null = null;
  const text = formDraft.value.credentialsText.trim();
  if (text) {
    try {
      credentials = JSON.parse(text);
    } catch {
      showGlobalRequestError({ title: t('settings.mcpServers.mcpServersFormTitle'), path: '/mcp/servers/save.json', detail: 'credentials JSON invalid' });
      return;
    }
  }
  isSubmitting.value = true;
  try {
    await saveMcpServer({
      name: formDraft.value.name.trim(),
      kind: formDraft.value.kind,
      target: formDraft.value.target.trim(),
      note: formDraft.value.note,
      credentials,
      overwrite: isEditing.value || formDraft.value.overwrite,
    });
    showGlobalSuccessToast(t('settings.mcpServers.mcpServersSaved'));
    closeForm();
    await load();
  } catch {
    showGlobalRequestError({ title: t('settings.mcpServers.mcpServersFormTitle'), path: '/mcp/servers/save.json' });
  } finally {
    isSubmitting.value = false;
  }
}

async function confirmDelete(): Promise<void> {
  const name = deleteTargetName.value;
  if (!name) {
    return;
  }
  deleteTargetName.value = null;
  try {
    await deleteMcpServer(name);
    showGlobalSuccessToast(t('settings.mcpServers.mcpServersDeleted'));
    await load();
  } catch {
    showGlobalRequestError({ title: t('settings.mcpServers.mcpServersDelete'), path: '/mcp/servers/delete.json' });
  }
}

async function toggleEnabled(entry: McpServerEntry, next: boolean): Promise<void> {
  try {
    await setMcpServerEnabled(entry.name, next);
    showGlobalSuccessToast(t('settings.mcpServers.mcpServersEnabledToggled'));
    await load();
  } catch {
    showGlobalRequestError({ title: t('settings.mcpServers.mcpServersEnable'), path: '/mcp/servers/enable.json' });
  }
}

/** 与后端 `mask_credential_value` 同口径：首个空格/冒号前 token（≤8 字符）+ `***`。 */
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

function maskedCredentials(entry: McpServerEntry): string {
  const creds = entry.credentials;
  if (!creds || Object.keys(creds).length === 0) {
    return '—';
  }
  const masked: Record<string, Record<string, string>> = {};
  for (const [layer, values] of Object.entries(creds)) {
    masked[layer] = Object.fromEntries(
      Object.entries(values ?? {}).map(([key, value]) => [key, maskValue(String(value))]),
    );
  }
  return JSON.stringify(masked);
}

function fullCredentials(entry: McpServerEntry): string {
  const creds = entry.credentials;
  if (!creds || Object.keys(creds).length === 0) {
    return '—';
  }
  return JSON.stringify(creds, null, 2);
}

onMounted(() => {
  void load();
});
</script>

<template>
  <section id="mcpServers" class="config-section">
    <SettingsBreadcrumb :items="props.breadcrumbItems" @navigate="emit('navigateBreadcrumb', $event)" />

    <div class="section-head section-head--compact">
      <div class="section-actions">
        <button type="button" class="btn btn--primary" @click="openCreate">
          {{ t('settings.mcpServers.mcpServersAdd') }}
        </button>
      </div>
    </div>

    <section class="roles-table-section">
      <div class="skills-info-banner">
        <span class="info-icon">ⓘ</span>
        <span>{{ t('settings.mcpServers.mcpServersSubtitle') }}</span>
      </div>

      <p v-if="isLoading" class="roles-empty">…</p>
      <p v-else-if="loadFailed" class="roles-empty">{{ t('settings.mcpServers.mcpServersLoadFailed') }}</p>
      <p v-else-if="servers.length === 0" class="roles-empty">
        {{ t('settings.mcpServers.mcpServersEmpty') }}
      </p>

      <div v-else class="ui-table-wrap">
        <table class="ui-table roles-table">
          <thead>
            <tr>
              <th>{{ t('settings.mcpServers.mcpServersName') }}</th>
              <th>{{ t('settings.mcpServers.mcpServersKind') }}</th>
              <th>{{ t('settings.mcpServers.mcpServersTarget') }}</th>
              <th>{{ t('settings.mcpServers.mcpServersCredentials') }}</th>
              <th>{{ t('settings.mcpServers.mcpServersEnabled') }}</th>
              <th>—</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="entry in servers" :key="entry.name">
              <td class="mono">{{ entry.name }}</td>
              <td>{{ entry.kind }}</td>
              <td class="mono">{{ entry.target }}</td>
              <td class="mono">
                <pre class="mcp-credentials">{{ showFullCredentials ? fullCredentials(entry) : maskedCredentials(entry) }}</pre>
              </td>
              <td>
                <ToggleSwitch
                  :checked="entry.enable"
                  size="sm"
                  @toggle="(next: boolean) => toggleEnabled(entry, next)"
                />
                <span class="mcp-state-label">
                  {{ entry.enable ? t('settings.mcpServers.mcpServersEnabled') : t('settings.mcpServers.mcpServersDisabled') }}
                </span>
              </td>
              <td>
                <div class="section-actions">
                  <button type="button" class="btn btn--ghost" @click="openEdit(entry)">
                    {{ t('settings.mcpServers.mcpServersEdit') }}
                  </button>
                  <button type="button" class="btn btn--danger" @click="deleteTargetName = entry.name">
                    {{ t('settings.mcpServers.mcpServersDelete') }}
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <label class="mcp-show-full">
        <input v-model="showFullCredentials" type="checkbox" />
        {{ t('settings.mcpServers.mcpServersShowCredentials') }}
      </label>
    </section>

    <section v-if="showForm" class="mcp-form">
      <h4>{{ t('settings.mcpServers.mcpServersFormTitle') }}</h4>
      <div class="mcp-form-grid">
        <FormField :label="t('settings.mcpServers.mcpServersName')">
          <input
            v-model="formDraft.name"
            type="text"
            class="text-input"
            :disabled="!isNameEditable"
            spellcheck="false"
          />
        </FormField>
        <FormField :label="t('settings.mcpServers.mcpServersKind')">
          <select v-model="formDraft.kind" class="text-input">
            <option v-for="kind in KIND_OPTIONS" :key="kind" :value="kind">{{ kind }}</option>
          </select>
        </FormField>
        <FormField :label="t('settings.mcpServers.mcpServersTarget')" wide>
          <input v-model="formDraft.target" type="text" class="text-input" spellcheck="false" />
        </FormField>
        <FormField :label="t('settings.mcpServers.mcpServersNoteLabel')" wide>
          <input v-model="formDraft.note" type="text" class="text-input" />
        </FormField>
        <FormField
          :label="t('settings.mcpServers.mcpServersCredentials')"
          wide
          :hint="t('settings.mcpServers.mcpServersCredentialsHint')"
        >
          <textarea v-model="formDraft.credentialsText" class="text-input mono" rows="4" spellcheck="false" />
        </FormField>
        <label v-if="!isEditing" class="mcp-show-full">
          <input v-model="formDraft.overwrite" type="checkbox" />
          {{ t('settings.mcpServers.mcpServersOverwrite') }}
        </label>
      </div>
      <div class="section-actions">
        <button type="button" class="btn btn--ghost" @click="closeForm">
          {{ t('settings.mcpServers.mcpServersCancel') }}
        </button>
        <button type="button" class="btn btn--primary" :disabled="isSubmitting" @click="submitForm">
          {{ t('settings.mcpServers.mcpServersSave') }}
        </button>
      </div>
    </section>

    <div v-if="deleteTargetName" class="mcp-delete-confirm">
      <span>{{ t('settings.mcpServers.mcpServersDeleteConfirm', { name: deleteTargetName }) }}</span>
      <div class="section-actions">
        <button type="button" class="btn btn--ghost" @click="deleteTargetName = null">
          {{ t('settings.mcpServers.mcpServersCancel') }}
        </button>
        <button type="button" class="btn btn--danger" @click="confirmDelete">
          {{ t('settings.mcpServers.mcpServersDelete') }}
        </button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.mcp-credentials {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 12px;
}

.mcp-state-label {
  margin-left: 8px;
  font-size: 12px;
  opacity: 0.75;
}

.mcp-show-full {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 12px;
  font-size: 12px;
  cursor: pointer;
}

.mcp-form {
  margin-top: 16px;
  padding: 16px;
  border: 1px solid var(--panel-border);
  border-radius: 8px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.mcp-form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.mcp-delete-confirm {
  margin-top: 16px;
  padding: 12px 16px;
  border: 1px solid var(--panel-border);
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
</style>

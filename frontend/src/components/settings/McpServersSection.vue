<script setup lang="ts">
import { onMounted, ref } from 'vue';
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
import ConfirmDialog from '../ui/ConfirmDialog.vue';
import SettingsBreadcrumb from './SettingsBreadcrumb.vue';
import McpServerEditorDialog from './McpServerEditorDialog.vue';
import type { SettingsBreadcrumbItem } from './types';

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
const showFullCredentials = ref(false);
const deleteTargetName = ref<string | null>(null);

const editorDialogRef = ref<InstanceType<typeof McpServerEditorDialog> | null>(null);

function openCreate(): void {
  editorDialogRef.value?.openCreate();
}

function openEdit(entry: McpServerEntry): void {
  editorDialogRef.value?.openEdit(entry);
}

function closeEditor(): void {
  editorDialogRef.value?.close();
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

async function handleSave(payload: {
  name: string;
  kind: string;
  protocol: string | null;
  target: string;
  note: string;
  headers: Record<string, string>;
  env: Record<string, string>;
  overwrite: boolean;
}): Promise<void> {
  const config: Record<string, Record<string, string>> = {};
  if (Object.keys(payload.headers).length > 0) {
    config.headers = payload.headers;
  }
  if (Object.keys(payload.env).length > 0) {
    config.env = payload.env;
  }
  isSubmitting.value = true;
  try {
    await saveMcpServer({
      name: payload.name,
      kind: payload.kind,
      protocol: payload.protocol,
      target: payload.target,
      note: payload.note,
      config: Object.keys(config).length > 0 ? config : null,
      overwrite: payload.overwrite,
    });
    showGlobalSuccessToast(t('settings.mcpServers.mcpServersSaved'));
    closeEditor();
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

function maskedCredentials(entry: McpServerEntry): string {
  const creds = entry.config;
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
  const creds = entry.config;
  if (!creds || Object.keys(creds).length === 0) {
    return '—';
  }
  return JSON.stringify(creds, null, 2);
}

/** kind/protocol 不裸显（Operator 要求）：列表里也一律给说明标签。 */
function kindLabel(entry: McpServerEntry): string {
  const kindText = entry.kind === 'mcp'
    ? t('settings.mcpServers.mcpServersKindMcp')
    : entry.kind === 'openapi'
      ? t('settings.mcpServers.mcpServersKindOpenapi')
      : entry.kind === 'graphql'
        ? t('settings.mcpServers.mcpServersKindGraphql')
        : entry.kind;
  if (entry.kind !== 'mcp' || !entry.protocol) {
    return kindText;
  }
  const protocolText = entry.protocol === 'stdio'
    ? t('settings.mcpServers.mcpServersProtocolStdio')
    : entry.protocol === 'sse'
      ? t('settings.mcpServers.mcpServersProtocolSse')
      : entry.protocol === 'streamable_http'
        ? t('settings.mcpServers.mcpServersProtocolStreamable')
        : entry.protocol;
  return `${kindText} · ${protocolText}`;
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
        <button type="button" class="secondary-button" @click="openCreate">
          {{ t('settings.mcpServers.mcpServersAdd') }}
        </button>
      </div>
    </div>

    <section class="roles-table-section">
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
              <th>{{ t('settings.mcpServers.mcpServersActions') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="entry in servers" :key="entry.name">
              <td class="mono">{{ entry.name }}</td>
              <td>{{ kindLabel(entry) }}</td>
              <td class="mono">{{ entry.target }}</td>
              <td class="mono">
                <pre class="mcp-config">{{ showFullCredentials ? fullCredentials(entry) : maskedCredentials(entry) }}</pre>
              </td>
              <td>
                <ToggleSwitch
                  :checked="entry.enable"
                  :label="entry.enable ? t('settings.mcpServers.mcpServersEnabled') : t('settings.mcpServers.mcpServersDisabled')"
                  @toggle="(next: boolean) => toggleEnabled(entry, next)"
                />
              </td>
              <td>
                <div class="row-actions">
                  <button type="button" class="ghost-button" @click="openEdit(entry)">
                    {{ t('settings.mcpServers.mcpServersEdit') }}
                  </button>
                  <button type="button" class="ghost-button text-danger" @click="deleteTargetName = entry.name">
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

    <McpServerEditorDialog ref="editorDialogRef" :submitting="isSubmitting" @save="handleSave" />

    <ConfirmDialog
      :open="deleteTargetName !== null"
      :title="t('settings.mcpServers.mcpServersDeleteTitle')"
      :message="t('settings.mcpServers.mcpServersDeleteConfirm', { name: deleteTargetName ?? '' })"
      :confirm-label="t('settings.mcpServers.mcpServersDelete')"
      danger
      @close="deleteTargetName = null"
      @confirm="confirmDelete"
    />
  </section>
</template>

<style scoped>
.section-head,
.section-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.section-head {
  margin-bottom: 8px;
}

.section-head--compact {
  justify-content: flex-end;
}

.row-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  /* 按钮文字必须单行：本页 6 列（含长 URL 列）在窄内容区里会把「操作」列压到 72px 上下，
     两个按钮作为 flex 项默认 `flex-shrink: 1` + `min-width: auto`，会被压到「一个字宽」
     ⇒ 「编辑」断成「编/辑」。参照页的做法（RolesSettingsSection / SkillsSettingsSection）
     是给按钮 `white-space: nowrap`；这里写在容器上让两个按钮一起继承，语义相同、少一处重复。 */
  white-space: nowrap;
}

.row-actions .ghost-button {
  /* 有了 nowrap 之后 min-content 已经是整词宽度，理论上不会再被压缩；
     `flex: 0 0 auto` 把「不许压」写成显式约束，避免以后有人给容器加宽度限制时又退回去。 */
  flex: 0 0 auto;
}

.text-danger {
  color: #e5484d;
}

.mcp-config {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 12px;
}

.mcp-show-full {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 12px;
  font-size: 12px;
  cursor: pointer;
}

</style>

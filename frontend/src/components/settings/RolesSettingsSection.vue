<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { useI18n } from 'vue-i18n';
import { getRoleTemplates } from '../../api';
import type { RoleTemplateSummary } from '../../types';
import { displayName } from '../../utils';
import RoleTemplateEditorDialog from './RoleTemplateEditorDialog.vue';
import SettingsBreadcrumb from './SettingsBreadcrumb.vue';
import type { SettingsBreadcrumbItem } from './types';

defineProps<{
  breadcrumbItems: SettingsBreadcrumbItem[];
}>();

const emit = defineEmits<{
  navigateBreadcrumb: [key: string];
}>();

const { t } = useI18n();

const templates = ref<RoleTemplateSummary[]>([]);
const selectedTemplateId = ref<number | null>(null);
const isLoading = ref(false);
const statusText = ref('');
const editorDialogRef = ref<InstanceType<typeof RoleTemplateEditorDialog> | null>(null);

function equalsIgnoreCase(value: string | null | undefined, expected: string): boolean {
  return String(value ?? '').trim().toLowerCase() === expected.toLowerCase();
}

function isSystemType(type: string | null | undefined): boolean {
  return equalsIgnoreCase(type, 'system');
}

function buildSoulPreview(soul: string | undefined): string {
  const normalized = String(soul ?? '')
    .replace(/\s+/g, ' ')
    .trim();
  if (!normalized) {
    return t('settings.roles.noSoul');
  }
  return normalized.length > 72 ? `${normalized.slice(0, 72)}...` : normalized;
}

async function loadRoleSettings(preferredId?: number | null): Promise<void> {
  isLoading.value = true;
  statusText.value = '';

  try {
    const nextTemplates = await getRoleTemplates();
    templates.value = nextTemplates;

    if (preferredId !== null && preferredId !== undefined && nextTemplates.some((template) => template.id === preferredId)) {
      selectedTemplateId.value = preferredId;
    } else if (selectedTemplateId.value !== null && nextTemplates.some((template) => template.id === selectedTemplateId.value)) {
      return;
    } else {
      selectedTemplateId.value = nextTemplates[0]?.id ?? null;
    }
  } catch (error) {
    console.error(error);
    statusText.value = t('settings.roles.loadFailed');
  } finally {
    isLoading.value = false;
  }
}

function openCreate(): void {
  selectedTemplateId.value = null;
  editorDialogRef.value?.openCreate();
}

function openEdit(templateId: number): void {
  selectedTemplateId.value = templateId;
  void editorDialogRef.value?.openEdit(templateId);
}

function handleDialogChanged(payload: { preferredId: number | null }): void {
  void loadRoleSettings(payload.preferredId);
}

onMounted(() => {
  void loadRoleSettings();
});
</script>

<template>
  <section id="roles" class="config-section">
    <SettingsBreadcrumb :items="breadcrumbItems" @navigate="emit('navigateBreadcrumb', $event)" />

    <div class="section-head section-head--compact">
      <div class="section-actions">
        <span v-if="statusText" class="section-status">{{ statusText }}</span>
        <button type="button" class="secondary-button" @click="openCreate">
          {{ t('settings.roles.newTemplate') }}
        </button>
      </div>
    </div>

    <section class="roles-table-section">
      <p v-if="isLoading" class="roles-empty">{{ t('settings.roles.loading') }}</p>

      <div v-else-if="templates.length" class="settings-table-wrap">
        <table class="settings-table roles-table">
          <thead>
            <tr>
              <th>{{ t('settings.roles.table.id') }}</th>
              <th>{{ t('settings.roles.nameLabel') }}</th>
              <th>{{ t('settings.roles.table.type') }}</th>
              <th class="roles-table-actions-head">{{ t('settings.roles.table.actions') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="template in templates"
              :key="template.id"
              :class="{ active: selectedTemplateId === template.id }"
              @click="selectedTemplateId = template.id"
            >
              <td class="roles-cell-id">#{{ template.id }}</td>
              <td class="roles-cell-name">
                <strong>{{ displayName(template) }}</strong>
              </td>
              <td>
                <span
                  class="role-chip"
                  :class="isSystemType(template.type) ? 'role-chip--system' : 'role-chip--user'"
                >
                  {{ isSystemType(template.type) ? t('settings.roles.systemTemplate') : t('settings.roles.userTemplate') }}
                </span>
              </td>
              <td class="roles-cell-actions">
                <button type="button" class="ghost-button" @click.stop="openEdit(template.id)">
                  {{ t('common.edit') }}
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <p v-else class="roles-empty">{{ t('settings.roles.empty') }}</p>
    </section>

    <RoleTemplateEditorDialog ref="editorDialogRef" @changed="handleDialogChanged" />
  </section>
</template>

<style scoped>
.config-section {
  padding: 12px 0 0;
}

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

.section-head h3 {
  margin: 0;
  color: var(--text-strong);
}

.section-status,
.roles-empty {
  color: var(--muted);
}

.roles-table-section {
  margin-top: 10px;
  padding: 0 10px;
}

.settings-table-wrap {
  margin-top: 10px;
  overflow-x: auto;
  padding: 10px 12px 12px;
  border-radius: 16px;
  background: var(--settings-table-surface);
}

.roles-empty {
  margin-top: 10px;
  font-size: 0.86rem;
}

.settings-table {
  width: 100%;
  min-width: 0;
  border-collapse: separate;
  border-spacing: 0;
  table-layout: fixed;
}

.settings-table th,
.settings-table td {
  padding: 12px 14px;
  text-align: left;
  vertical-align: top;
}

.settings-table thead th {
  position: relative;
  padding-top: 16px;
  padding-bottom: 16px;
  border-bottom: 1px solid color-mix(in srgb, var(--divider) 86%, transparent);
  background: var(--settings-table-head-bg);
  color: var(--text-strong);
  font-size: 0.84rem;
  font-weight: 700;
  letter-spacing: 0.01em;
  white-space: nowrap;
}

.settings-table thead th:not(:last-child)::after {
  content: '';
  position: absolute;
  top: 14px;
  right: 0;
  width: 1px;
  height: calc(100% - 28px);
  background: color-mix(in srgb, var(--divider) 88%, transparent);
}

.settings-table tbody td {
  border-bottom: 1px solid color-mix(in srgb, var(--divider) 76%, transparent);
  color: var(--text-strong);
  font-size: 0.84rem;
  transition:
    background 140ms ease,
    box-shadow 140ms ease;
}

.settings-table tbody tr:hover td,
.settings-table tbody tr.active td {
  background: var(--settings-table-row-hover);
}

.settings-table tbody tr.active td {
  background: var(--settings-table-row-active);
  box-shadow: none;
}

.settings-table tbody tr:last-child td {
  border-bottom: none;
}

.settings-table tbody tr:first-child td {
  padding-top: 18px;
}

.roles-cell-id {
  color: var(--muted);
}

.roles-cell-id {
  white-space: nowrap;
}

.roles-cell-id {
  width: 72px;
}

.roles-cell-name strong {
  color: var(--text-strong);
  font-size: 0.96rem;
}



.roles-cell-actions,
.roles-table-actions-head {
  width: 88px;
  text-align: right;
}

.roles-cell-actions :deep(.ghost-button) {
  white-space: nowrap;
}

.role-chip {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 24px;
  padding: 0 10px;
  border-radius: 999px;
  border: 1px solid var(--panel-border);
  background: var(--panel-bg);
  color: var(--muted);
  font-size: 0.74rem;
}

.role-chip--system {
  border-color: color-mix(in srgb, var(--focus-border) 26%, var(--panel-border) 74%);
  background: var(--backend-selected-strong, color-mix(in srgb, var(--selected) 72%, var(--panel-bg) 28%));
  color: color-mix(in srgb, var(--text-strong) 82%, var(--accent) 18%);
}

.role-chip--user {
  border-color: color-mix(in srgb, var(--state-success) 30%, var(--panel-border) 70%);
  background: color-mix(in srgb, var(--state-success) 12%, var(--panel-bg) 88%);
  color: color-mix(in srgb, var(--state-success) 90%, var(--text-strong) 10%);
}

@media (max-width: 780px) {
  .section-head,
  .section-actions {
    align-items: flex-start;
    flex-direction: column;
  }

  .section-actions {
    width: 100%;
  }

  .settings-table {
    min-width: 720px;
  }
}
</style>

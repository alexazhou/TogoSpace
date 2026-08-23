<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { useI18n } from 'vue-i18n';
import { getRoleTemplates } from '../../api';
import type { RoleTemplateSummary } from '../../types';
import { displayName } from '../../utils';
import RoleTemplateEditorDialog from './RoleTemplateEditorDialog.vue';
import SettingsBreadcrumb from './SettingsBreadcrumb.vue';
import type { SettingsBreadcrumbItem } from './types';
import UiTag from '../ui/UiTag.vue';

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

      <div v-else-if="templates.length" class="ui-table-wrap">
        <table class="ui-table roles-table">
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
                <UiTag :tone="isSystemType(template.type) ? 'info' : 'success'">
                  {{ isSystemType(template.type) ? t('settings.roles.systemTemplate') : t('settings.roles.userTemplate') }}
                </UiTag>
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
}

.roles-cell-id {
  width: 72px;
  color: var(--muted);
  white-space: nowrap;
}

.roles-cell-name strong {
  color: var(--text-strong);
  font-size: 0.84rem;
  font-weight: 600;
}

.roles-cell-actions,
.roles-table-actions-head {
  width: 88px;
  text-align: right;
}

.roles-cell-actions :deep(.ghost-button) {
  white-space: nowrap;
}

.ui-table tbody tr.active td {
  background: var(--settings-table-row-active);
  box-shadow: none;
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

  .ui-table {
    min-width: 720px;
  }
}
</style>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { useI18n } from 'vue-i18n';
import {
  createRoleTemplate,
  deleteRoleTemplate,
  getRoleTemplateDetail,
  updateRoleTemplate,
} from '../../api';
import { showGlobalSuccessToast } from '../../appUiState';
import type { RoleTemplateDetail } from '../../types';
import { displayName } from '../../utils';
import ConfirmDialog from '../ui/ConfirmDialog.vue';
import UiTag from '../ui/UiTag.vue';
import ModalDialog from '../ui/ModalDialog.vue';

type EditorMode = 'create' | 'edit';

type RoleTemplateFormSnapshot = {
  name: string;
  soul: string;
};

const emit = defineEmits<{
  changed: [payload: { preferredId: number | null }];
}>();

const { t } = useI18n();

const visible = ref(false);
const mode = ref<EditorMode>('create');
const selectedTemplateId = ref<number | null>(null);
const currentDetail = ref<RoleTemplateDetail | null>(null);
const editorLoading = ref(false);
const isSaving = ref(false);
const isDeleting = ref(false);
const advancedOpen = ref(false);
const deleteConfirmOpen = ref(false);
const saveConfirmOpen = ref(false);
const cancelConfirmOpen = ref(false);
const statusText = ref('');
const form = ref({
  name: '',
  soul: '',
});

const isCreating = computed(() => mode.value === 'create');

function equalsIgnoreCase(value: string | null | undefined, expected: string): boolean {
  return String(value ?? '').trim().toLowerCase() === expected.toLowerCase();
}

function isSystemType(type: string | null | undefined): boolean {
  return equalsIgnoreCase(type, 'system');
}

const currentTypeLabel = computed(() => {
  if (isSystemType(currentDetail.value?.type)) {
    return t('settings.roles.systemTemplate');
  }
  if (equalsIgnoreCase(currentDetail.value?.type, 'user')) {
    return t('settings.roles.userTemplate');
  }
  return t('settings.roles.undefined');
});

const dialogTitle = computed(() => {
  if (isCreating.value) {
    return t('settings.roles.newTitle');
  }
  if (currentDetail.value) {
    return displayName(currentDetail.value);
  }
  return form.value.name || t('settings.roles.detailFallback');
});
const dialogEyebrow = computed(() => (isCreating.value ? 'Create Template' : 'Template Detail'));
const isSystemTemplate = computed(() => isSystemType(currentDetail.value?.type));
const canDelete = computed(() => !isCreating.value && !!currentDetail.value && !isSystemTemplate.value && !isDeleting.value);
const isNameReadonly = computed(() => !isCreating.value && isSystemTemplate.value);
const isSystemReadonlyFields = computed(() => !isCreating.value && isSystemTemplate.value);
const formSnapshot = computed<RoleTemplateFormSnapshot>(() => ({
  name: form.value.name.trim(),
  soul: form.value.soul,
}));
const currentDetailSnapshot = computed<RoleTemplateFormSnapshot | null>(() => {
  if (!currentDetail.value) {
    return null;
  }
  return {
    name: currentDetail.value.name.trim(),
    soul: currentDetail.value.soul,
  };
});
const isDirty = computed(() => {
  if (isCreating.value) {
    return true;
  }
  if (!currentDetailSnapshot.value) {
    return false;
  }
  return JSON.stringify(formSnapshot.value) !== JSON.stringify(currentDetailSnapshot.value);
});
const canSave = computed(() => {
  if (isSaving.value || editorLoading.value) {
    return false;
  }
  if (isCreating.value) {
    return form.value.name.trim().length > 0;
  }
  return !!currentDetail.value && !isSystemTemplate.value && isDirty.value;
});



function resetForm(detail?: RoleTemplateDetail | null): void {
  form.value = {
    name: detail?.name || '',
    soul: detail?.soul || '',
  };
  advancedOpen.value = false;
  statusText.value = '';
}

function closeDialog(): void {
  visible.value = false;
  mode.value = 'create';
  selectedTemplateId.value = null;
  currentDetail.value = null;
  editorLoading.value = false;
  deleteConfirmOpen.value = false;
  saveConfirmOpen.value = false;
  cancelConfirmOpen.value = false;
  resetForm(null);
}

function requestClose(): void {
  if (isDirty.value) {
    cancelConfirmOpen.value = true;
  } else {
    closeDialog();
  }
}

function confirmCancel(): void {
  cancelConfirmOpen.value = false;
  closeDialog();
}

function openCreate(): void {
  mode.value = 'create';
  selectedTemplateId.value = null;
  currentDetail.value = null;
  resetForm(null);
  visible.value = true;
}

async function openEdit(templateId: number): Promise<void> {
  mode.value = 'edit';
  selectedTemplateId.value = templateId;
  currentDetail.value = null;
  editorLoading.value = true;
  resetForm(null);
  visible.value = true;

  try {
    const detail = await getRoleTemplateDetail(templateId);
    currentDetail.value = detail;
    resetForm(detail);
  } catch (error) {
    console.error(error);
    statusText.value = t('settings.roles.loadFailed');
  } finally {
    editorLoading.value = false;
  }
}

function requestSave(): void {
  if (!canSave.value) {
    return;
  }
  saveConfirmOpen.value = true;
}

async function confirmSave(): Promise<void> {
  saveConfirmOpen.value = false;
  await saveTemplate();
}

async function saveTemplate(): Promise<void> {
  if (!canSave.value) {
    return;
  }

  isSaving.value = true;
  statusText.value = '';

  try {
    if (isCreating.value) {
      const created = await createRoleTemplate({
        name: form.value.name.trim(),
        soul: form.value.soul,
      });
      showGlobalSuccessToast(t('settings.roles.createSuccess'));
      emit('changed', { preferredId: created.id });
      closeDialog();
      return;
    }

    if (selectedTemplateId.value === null) {
      return;
    }

    const updated = await updateRoleTemplate(selectedTemplateId.value, {
      name: form.value.name.trim(),
      soul: form.value.soul,
    });
    showGlobalSuccessToast(t('settings.roles.saveSuccess'));
    emit('changed', { preferredId: updated.id });
    closeDialog();
  } catch (error) {
    console.error(error);
    statusText.value = t('settings.roles.saveFailed');
  } finally {
    isSaving.value = false;
  }
}

function requestDelete(): void {
  if (!canDelete.value) {
    return;
  }
  deleteConfirmOpen.value = true;
}

async function confirmDelete(): Promise<void> {
  if (selectedTemplateId.value === null) {
    deleteConfirmOpen.value = false;
    return;
  }

  isDeleting.value = true;
  statusText.value = '';

  try {
    await deleteRoleTemplate(selectedTemplateId.value);
    showGlobalSuccessToast(t('settings.roles.deleteSuccess'));
    emit('changed', { preferredId: null });
    closeDialog();
  } catch (error) {
    console.error(error);
    statusText.value = t('settings.roles.deleteFailed');
  } finally {
    isDeleting.value = false;
  }
}

defineExpose({
  openCreate,
  openEdit,
});
</script>

<template>
  <ModalDialog
    :open="visible"
    :title="dialogTitle"
    :eyebrow="dialogEyebrow"
    :width="760"
    :close-label="t('common.close')"
    @close="requestClose"
  >
    <div class="role-editor-meta">
          <UiTag :tone="isCreating ? 'muted' : (isSystemTemplate ? 'info' : 'success')">
            {{ isCreating ? t('settings.roles.unsaved') : currentTypeLabel }}
          </UiTag>
        </div>

        <div v-if="editorLoading" class="dialog-empty">
          {{ t('settings.roles.loading') }}
        </div>

        <template v-else>
          <div class="role-form-grid">
            <label class="role-field">
              <span>{{ t('settings.roles.nameLabel') }}</span>
              <input
                v-model="form.name"
                type="text"
                class="role-input"
                :class="{ 'role-input--readonly': isNameReadonly }"
                :placeholder="t('settings.roles.namePlaceholder')"
                :readonly="isNameReadonly"
              />
            </label>

            <label class="role-field role-field--wide">
              <span>Soul</span>
              <textarea
                v-model="form.soul"
                class="role-textarea"
                :class="{ 'role-input--readonly': isSystemReadonlyFields }"
                rows="12"
                :placeholder="t('settings.roles.soulPlaceholder')"
                :readonly="isSystemReadonlyFields"
              ></textarea>
            </label>
          </div>


        </template>

        <p v-if="statusText" class="editor-status">{{ statusText }}</p>

        <template #footer-trailing>
      <button
        v-if="canDelete"
        type="button"
        class="secondary-button secondary-button--danger"
        :disabled="isDeleting"
        @click="requestDelete"
      >
        {{ isDeleting ? t('settings.roles.deleting') : t('settings.roles.deleteBtn') }}
      </button>
      <button type="button" class="secondary-button" @click="requestClose">
        {{ t('common.cancel') }}
      </button>
      <button type="button" class="secondary-button" :disabled="!canSave" @click="requestSave">
        {{ isSaving ? t('settings.roles.saving') : (isCreating ? t('settings.roles.createBtn') : t('settings.roles.saveBtn')) }}
      </button>
    </template>
  </ModalDialog>

    <ConfirmDialog
        :open="deleteConfirmOpen"
        :title="t('settings.roles.deleteConfirmTitle')"
        :message="t('settings.roles.deleteConfirmMsg', { name: currentDetail?.name || '' })"
        :confirm-label="t('settings.roles.deleteConfirmBtn')"
        danger
        @close="deleteConfirmOpen = false"
        @confirm="confirmDelete"
      />

      <ConfirmDialog
        :open="saveConfirmOpen"
        :title="t('settings.roles.saveConfirmTitle')"
        :message="t('settings.roles.saveConfirmMsg')"
        :confirm-label="t('common.save')"
        @close="saveConfirmOpen = false"
        @confirm="confirmSave"
      />

      <ConfirmDialog
        :open="cancelConfirmOpen"
        :title="t('settings.roles.cancelConfirmTitle')"
        :message="t('settings.roles.cancelConfirmMsg')"
        :confirm-label="t('common.confirm')"
        danger
        @close="cancelConfirmOpen = false"
        @confirm="confirmCancel"
      />
</template>

<style scoped>
.role-editor-meta {
  display: flex;
  align-items: center;
  gap: 10px;
}

.dialog-empty,
.editor-status,
.role-field span,
.advanced-toggle__state {
  color: var(--muted);
  font-size: 0.76rem;
}

.dialog-empty {
  padding: 12px;
  border-radius: 12px;
  border: 1px solid var(--panel-border);
  background: color-mix(in srgb, var(--surface-soft) 82%, var(--panel-bg) 18%);
}

.role-form-grid,
.advanced-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 10px;
}

.role-field {
  display: grid;
  gap: 6px;
}

.role-field--wide {
  grid-column: 1 / -1;
}

.role-input,
.role-textarea {
  width: 100%;
  border: 1px solid var(--panel-border);
  border-radius: 12px;
  background: var(--panel-bg);
  color: var(--text-strong);
  padding: 10px 12px;
  font: inherit;
  box-sizing: border-box;
}

.role-input--readonly {
  border: 1px dashed color-mix(in srgb, var(--focus-border) 18%, var(--panel-border) 82%);
  background: color-mix(in srgb, var(--surface-soft) 86%, var(--panel-bg) 14%);
  color: color-mix(in srgb, var(--muted) 84%, var(--text-strong) 16%);
  -webkit-text-fill-color: color-mix(in srgb, var(--muted) 84%, var(--text-strong) 16%);
  box-shadow: none;
}

.role-input[readonly],
.role-textarea[readonly] {
  cursor: default;
}

.role-textarea {
  resize: vertical;
  min-height: 220px;
}

.advanced-card {
  border: 1px solid var(--panel-border);
  border-radius: 14px;
  background: color-mix(in srgb, var(--surface-soft) 84%, var(--panel-bg) 16%);
  overflow: hidden;
}

.advanced-toggle {
  width: 100%;
  min-height: 56px;
  padding: 10px 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  border: none;
  background: transparent;
  color: inherit;
  cursor: pointer;
  text-align: left;
}

.advanced-toggle strong {
  color: var(--text-strong);
  font-size: 0.96rem;
}

.advanced-grid {
  padding: 0 14px 14px;
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.secondary-button--danger {
  border-color: color-mix(in srgb, var(--state-danger) 38%, var(--panel-border) 62%);
  background: color-mix(in srgb, var(--state-danger) 10%, var(--panel-bg) 90%);
  color: color-mix(in srgb, var(--state-danger) 88%, var(--text-strong) 12%);
}

.secondary-button--danger:hover:not(:disabled) {
  border-color: color-mix(in srgb, var(--state-danger) 62%, var(--focus-border) 38%);
  background: color-mix(in srgb, var(--state-danger) 18%, var(--surface-soft) 82%);
  color: color-mix(in srgb, var(--state-danger) 92%, var(--text-strong) 8%);
  transform: translateY(-1px);
}

@media (max-width: 780px) {
  .advanced-grid {
    grid-template-columns: 1fr;
  }
}
</style>

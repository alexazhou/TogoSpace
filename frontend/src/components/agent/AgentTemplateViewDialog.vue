<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import { getRoleTemplateDetail } from '../../api';
import type { RoleTemplateDetail } from '../../types';
import { displayName } from '../../utils';
import MarkdownContent from '../ui/MarkdownContent.vue';
import ModalDialog from '../ui/ModalDialog.vue';
import UiTag from '../ui/UiTag.vue';

const props = defineProps<{
  open: boolean;
  templateId: number | null;
  templateName?: string | null;
}>();

const emit = defineEmits<{
  close: [];
}>();

const { t } = useI18n();

const detail = ref<RoleTemplateDetail | null>(null);
const loading = ref(false);
const errorMessage = ref('');

function normalizeType(value: string | null | undefined): string {
  return String(value ?? '').trim().toLowerCase();
}

const isSystemType = computed(() => normalizeType(detail.value?.type) === 'system');

const typeLabel = computed(() => {
  const type = normalizeType(detail.value?.type);
  if (type === 'system') {
    return t('settings.roles.systemTemplate');
  }
  if (type === 'user') {
    return t('settings.roles.userTemplate');
  }
  return t('settings.roles.undefined');
});

/** 标题优先用接口回来的本地化名称，接口未回来时退回调用方传入的展示名。 */
const dialogTitle = computed(() => {
  if (detail.value) {
    return displayName(detail.value) || props.templateName?.trim() || t('agent.template.detailFallback');
  }
  return props.templateName?.trim() || t('agent.template.detailFallback');
});

const soul = computed(() => (detail.value?.soul ?? '').trim());

async function loadDetail(): Promise<void> {
  if (!props.open || props.templateId === null) {
    detail.value = null;
    errorMessage.value = '';
    loading.value = false;
    return;
  }

  loading.value = true;
  errorMessage.value = '';

  try {
    detail.value = await getRoleTemplateDetail(props.templateId);
  } catch (error) {
    console.error(error);
    detail.value = null;
    errorMessage.value = t('agent.template.loadFailed');
  } finally {
    loading.value = false;
  }
}

watch(
  () => [props.open, props.templateId],
  () => {
    void loadDetail();
  },
  { immediate: true },
);
</script>

<template>
  <ModalDialog
    :open="open"
    :title="dialogTitle"
    :eyebrow="t('agent.template.eyebrow')"
    :width="720"
    :close-label="t('common.close')"
    @close="emit('close')"
  >
    <p v-if="loading" class="template-dialog__hint">{{ t('common.loading') }}</p>
    <p v-else-if="errorMessage" class="template-dialog__hint template-dialog__hint--error">{{ errorMessage }}</p>
    <template v-else>
      <div class="template-dialog__meta">
        <UiTag :tone="isSystemType ? 'info' : 'success'" size="sm">{{ typeLabel }}</UiTag>
      </div>
      <p v-if="!soul" class="template-dialog__hint">{{ t('agent.template.noSoul') }}</p>
      <div v-else class="template-dialog__soul">
        <MarkdownContent :content="soul" />
      </div>
    </template>

    <template #footer-trailing>
      <button type="button" class="secondary-button" @click="emit('close')">
        {{ t('common.close') }}
      </button>
    </template>
  </ModalDialog>
</template>

<style scoped>
.template-dialog__meta {
  display: flex;
  align-items: center;
  gap: 8px;
}

.template-dialog__hint {
  margin: 0;
  color: var(--text-tertiary);
  font-size: 13px;
}

.template-dialog__hint--error {
  color: var(--danger, #f85149);
}

.template-dialog__soul {
  padding: 12px 14px;
  border: 1px solid var(--border-default);
  border-radius: 10px;
  background: var(--surface-panel);
  font-size: 13px;
  color: var(--text-primary);
}
</style>

<script setup lang="ts">
import { useI18n } from 'vue-i18n';
import type { LlmProviderConfig } from '../../types';
import InfoTooltip from '../ui/InfoTooltip.vue';
import ModelSelect from '../ui/ModelSelect.vue';

defineProps<{
  label: string;
  description: string;
  modelValue: string | null;
  providers: LlmProviderConfig[];
}>();

const emit = defineEmits<{
  'update:modelValue': [value: string];
}>();

const { t } = useI18n();
</script>

<template>
  <div class="svc-field">
    <span>
      {{ label }}
      <InfoTooltip :text="description" />
    </span>
    <ModelSelect
      :model-value="modelValue"
      :providers="providers"
      :placeholder="t('common.notConfigured', '未配置')"
      @update:model-value="emit('update:modelValue', $event)"
    />
  </div>
</template>

<style scoped>
.svc-field { display: grid; gap: 6px; }
.svc-field > span { color: var(--muted); font-size: 0.76rem; }
</style>

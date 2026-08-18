<script setup lang="ts">
import { useI18n } from 'vue-i18n';
import type { LlmProviderConfig } from '../../types';
import InfoTooltip from '../ui/InfoTooltip.vue';
import ModelSelect from '../ui/ModelSelect.vue';
import FormField from '../ui/FormField.vue';

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
  <FormField>
    <template #label>
      {{ label }}
      <InfoTooltip :text="description" />
    </template>
    <ModelSelect
      :model-value="modelValue"
      :providers="providers"
      :placeholder="t('common.notConfigured', '未配置')"
      @update:model-value="emit('update:modelValue', $event)"
    />
  </FormField>
</template>

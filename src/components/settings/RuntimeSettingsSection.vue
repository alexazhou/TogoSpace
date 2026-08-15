<script setup lang="ts">
import SettingsBreadcrumb from './SettingsBreadcrumb.vue';
import type { SettingsBreadcrumbItem } from './types';
import type { DirectoriesConfig } from '../../types';

defineProps<{
  breadcrumbItems: SettingsBreadcrumbItem[];
  directories: DirectoriesConfig;
}>();

const emit = defineEmits<{
  navigateBreadcrumb: [key: string];
}>();
</script>

<template>
  <section id="runtime" class="config-section">
    <SettingsBreadcrumb :items="breadcrumbItems" @navigate="emit('navigateBreadcrumb', $event)" />

    <div class="form-grid">
      <label class="field-card">
        <span>配置目录</span>
        <input :value="directories.config_dir" type="text" readonly />
      </label>
      <label class="field-card">
        <span>工作目录</span>
        <input :value="directories.workspace_dir" type="text" readonly />
      </label>
      <label class="field-card">
        <span>数据目录</span>
        <input :value="directories.data_dir" type="text" readonly />
      </label>
      <label class="field-card">
        <span>日志目录</span>
        <input :value="directories.log_dir" type="text" readonly />
      </label>
    </div>
  </section>
</template>

<style scoped>
.config-section {
  padding: 12px 0 0;
}

.section-status,
.field-card span {
  color: var(--muted);
}

.form-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 8px;
  margin-top: 14px;
}

.field-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 0;
  border: none;
  border-radius: 0;
  background: transparent;
}

.field-card input,
.field-card textarea {
  width: 100%;
  border: 1px solid var(--panel-border);
  border-radius: 10px;
  background: var(--panel-bg);
  color: var(--text-strong);
  padding: 8px 10px;
  outline: none;
}

.field-card input:focus,
.field-card textarea:focus {
  border-color: var(--focus-border);
  box-shadow: 0 0 0 2px var(--focus-glow);
}

.field-card input[readonly] {
  color: var(--muted);
  cursor: default;
}

@media (max-width: 780px) {
  .form-grid {
    grid-template-columns: 1fr;
  }
}
</style>

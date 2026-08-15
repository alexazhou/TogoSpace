<script setup lang="ts">
import type { SettingsBreadcrumbItem } from './types';

defineProps<{
  items: SettingsBreadcrumbItem[];
}>();

const emit = defineEmits<{
  navigate: [key: string];
}>();
</script>

<template>
  <nav class="settings-breadcrumb" aria-label="当前位置">
    <template v-for="(item, index) in items" :key="item.key">
      <button
        type="button"
        class="breadcrumb-link"
        :class="{ current: item.current }"
        @click="!item.current && emit('navigate', item.key)"
      >
        {{ item.label }}
      </button>
      <span v-if="index < items.length - 1" class="breadcrumb-separator" aria-hidden="true">/</span>
    </template>
  </nav>
</template>

<style scoped>
.settings-breadcrumb {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0;
  margin-bottom: 8px;
}

.breadcrumb-link {
  border: none;
  background: transparent;
  color: var(--text-secondary);
  padding: 0;
  cursor: pointer;
  font-size: 0.98rem;
  line-height: 1.35;
  font-weight: 500;
}

.breadcrumb-separator {
  margin: 0 10px;
  color: var(--accent);
  font-size: 0.98rem;
  line-height: 1.35;
}

.breadcrumb-link.current {
  color: var(--text-strong);
  cursor: default;
  font-weight: 600;
}

.breadcrumb-link:hover:not(.current) {
  color: var(--accent);
}
</style>

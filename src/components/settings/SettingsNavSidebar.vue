<script setup lang="ts">
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';

export interface SettingsNavItem {
  id: string;
  label: string;
  note: string;
  groupLabel?: string;
}

const props = defineProps<{
  items: SettingsNavItem[];
  activeId: string;
  countLabel?: string;
}>();

const emit = defineEmits<{
  select: [sectionId: string];
}>();

const { t } = useI18n();

const navEntries = computed(() =>
  props.items.map((item, index) => ({
    item,
    showGroupLabel: Boolean(item.groupLabel && item.groupLabel !== props.items[index - 1]?.groupLabel),
  })),
);
</script>

<template>
  <aside class="settings-sidebar">
    <div class="sidebar-head">
      <span>{{ t('settings.nav.label') }}</span>
      <small v-if="countLabel">{{ countLabel }}</small>
    </div>

    <nav class="settings-nav" :aria-label="t('settings.nav.ariaLabel')">
      <template v-for="entry in navEntries" :key="entry.item.id">
        <p v-if="entry.showGroupLabel" class="nav-group-label">{{ entry.item.groupLabel }}</p>
        <button
          type="button"
          class="nav-link"
          :class="{ active: entry.item.id === activeId }"
          @click="emit('select', entry.item.id)"
        >
          <strong>{{ entry.item.label }}</strong>
          <span>{{ entry.item.note }}</span>
        </button>
      </template>
    </nav>
  </aside>
</template>

<style scoped>
.settings-sidebar {
  min-height: 0;
  height: 100%;
  padding: 10px 18px 0 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  border-right: 1px solid var(--divider);
}

.sidebar-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 0 4px;
}

.sidebar-head span,
.sidebar-head small {
  color: var(--muted);
}

.settings-nav {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.nav-group-label {
  margin: 10px 4px 2px;
  color: var(--muted);
  font-size: 0.68rem;
  font-weight: 700;
}

.nav-link {
  width: 100%;
  border: 1px solid transparent;
  border-radius: 12px;
  background: transparent;
  color: inherit;
  padding: 10px 12px;
  text-align: left;
  cursor: pointer;
  transition:
    border-color 140ms ease,
    background 140ms ease,
    transform 140ms ease;
}

.nav-link strong {
  display: block;
  color: var(--text-strong);
  font-size: 0.82rem;
}

.nav-link span {
  display: block;
  margin-top: 2px;
  color: var(--muted);
  font-size: 0.7rem;
}

.nav-link:hover {
  border-color: color-mix(in srgb, var(--focus-border) 18%, transparent);
  background: var(--backend-selected-hover, color-mix(in srgb, var(--selected) 28%, transparent));
  transform: translateX(2px);
}

.nav-link.active {
  border-color: var(--focus-border);
  background: var(--backend-selected-active, color-mix(in srgb, var(--selected) 44%, var(--panel-bg) 56%));
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--focus-border) 26%, transparent);
}

</style>

<script setup lang="ts">
import { ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import type { SkillInfo } from '../../types';

const props = defineProps<{
  open: boolean;
  skill: SkillInfo | null;
}>();

const emit = defineEmits<{
  close: [];
}>();

const { t } = useI18n();

const activeTab = ref<'desc' | 'files'>('desc');

watch(() => props.open, (newVal) => {
  if (newVal) {
    activeTab.value = 'desc';
  }
});
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="confirm-overlay" @click.self="emit('close')">
      <section class="confirm-dialog panel">
        <div class="confirm-head">
          <p class="confirm-eyebrow">{{ t('settings.skills.detailsTitle') }}</p>
          <h3>{{ skill?.name }}</h3>
        </div>

        <div class="dialog-tabs">
          <button
            type="button"
            class="tab-btn"
            :class="{ active: activeTab === 'desc' }"
            @click="activeTab = 'desc'"
          >
            {{ t('settings.skills.table.description') }}
          </button>
          <button
            type="button"
            class="tab-btn"
            :class="{ active: activeTab === 'files' }"
            @click="activeTab = 'files'"
          >
            {{ t('settings.skills.files') }}
          </button>
        </div>

        <div class="dialog-body">
          <div v-if="activeTab === 'desc'" class="tab-content">
            <p class="desc-text">{{ skill?.description || t('common.none') }}</p>
          </div>

          <div v-else-if="activeTab === 'files'" class="tab-content">
            <ul v-if="skill?.files && skill.files.length" class="file-list">
              <li v-for="file in skill.files" :key="file" class="file-item">
                <span class="file-icon">📄</span> {{ file }}
              </li>
            </ul>
            <p v-else class="desc-text">{{ t('common.none') }}</p>
          </div>
        </div>

        <div class="confirm-actions">
          <button type="button" class="secondary-button" @click="emit('close')">
            {{ t('common.close') }}
          </button>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.confirm-overlay {
  position: fixed;
  inset: 0;
  z-index: 120;
  display: grid;
  place-items: center;
  padding: 28px;
  background: rgba(6, 10, 16, 0.52);
  backdrop-filter: blur(8px);
}

.confirm-dialog {
  width: min(560px, 100%);
  padding: 0;
  display: flex;
  flex-direction: column;
  border-radius: 18px;
  border: 1px solid color-mix(in srgb, var(--interactive-focus-border) 26%, var(--border-default) 74%);
  background:
    linear-gradient(
      180deg,
      color-mix(in srgb, var(--surface-panel) 95%, transparent) 0%,
      color-mix(in srgb, var(--surface-panel-muted) 92%, transparent) 100%
    );
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.34);
  overflow: hidden;
}

.confirm-head {
  padding: 20px 20px 10px;
  display: grid;
  gap: 4px;
}

.confirm-eyebrow {
  margin: 0;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.14em;
  font-size: 0.68rem;
}

.confirm-head h3 {
  margin: 0;
  color: var(--text-primary);
  font-size: 1.24rem;
}

.dialog-tabs {
  display: flex;
  gap: 20px;
  padding: 0 20px;
  border-bottom: 1px solid var(--border-default);
}

.tab-btn {
  background: none;
  border: none;
  padding: 10px 0;
  color: var(--text-secondary);
  font-size: 0.9rem;
  font-weight: 500;
  cursor: pointer;
  position: relative;
  transition: color 150ms ease;
}

.tab-btn:hover {
  color: var(--text-primary);
}

.tab-btn.active {
  color: var(--accent);
}

.tab-btn.active::after {
  content: '';
  position: absolute;
  bottom: -1px;
  left: 0;
  right: 0;
  height: 2px;
  background: var(--accent);
  border-radius: 2px 2px 0 0;
}

.dialog-body {
  padding: 16px 20px;
  display: flex;
  flex-direction: column;
  max-height: 50vh;
  overflow-y: auto;
  background: color-mix(in srgb, var(--surface-soft) 40%, transparent);
}

.tab-content {
  min-height: 120px;
}

.desc-text {
  margin: 0;
  font-size: 0.92rem;
  color: var(--text-primary);
  line-height: 1.6;
  white-space: pre-wrap;
}

.file-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.file-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 0.88rem;
  color: var(--text-primary);
  background: var(--surface-panel);
  border: 1px solid var(--border-default);
  padding: 8px 12px;
  border-radius: 8px;
  font-family: var(--font-mono, monospace);
}

.file-icon {
  font-size: 1.1em;
}

.confirm-actions {
  padding: 12px 20px;
  display: flex;
  justify-content: flex-end;
  border-top: 1px solid var(--border-default);
}

.confirm-actions > .secondary-button {
  min-width: 88px;
  height: 32px;
  padding: 0 14px;
}
</style>

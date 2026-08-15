<script setup lang="ts">
import { useI18n } from 'vue-i18n';

defineProps<{
  open: boolean;
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
}>();

const emit = defineEmits<{
  close: [];
  confirm: [];
}>();

const { t } = useI18n();
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="confirm-overlay" @click.self="emit('close')">
      <section class="confirm-dialog panel">
        <div class="confirm-head">
          <p class="confirm-eyebrow">Confirm Action</p>
          <h3>{{ title }}</h3>
        </div>

        <p class="confirm-message">{{ message }}</p>

        <div class="confirm-actions">
          <button type="button" class="ghost-button" @click="emit('close')">
            {{ cancelLabel || t('common.cancel') }}
          </button>
          <button
            type="button"
            class="secondary-button"
            :class="{ 'secondary-button--danger': danger }"
            @click="emit('confirm')"
          >
            {{ confirmLabel || t('common.confirm') }}
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
  width: min(420px, 100%);
  padding: 18px;
  display: grid;
  gap: 14px;
  border-radius: 18px;
  border: 1px solid color-mix(in srgb, var(--interactive-focus-border) 26%, var(--border-default) 74%);
  background:
    linear-gradient(
      180deg,
      color-mix(in srgb, var(--surface-panel) 95%, transparent) 0%,
      color-mix(in srgb, var(--surface-panel-muted) 92%, transparent) 100%
    );
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.34);
}

.confirm-head {
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
  font-size: 1.12rem;
}

.confirm-message {
  margin: 0;
  color: var(--text-secondary);
  font-size: 0.9rem;
  line-height: 1.55;
}

.confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

.confirm-actions > .ghost-button,
.confirm-actions > .secondary-button {
  min-width: 88px;
  height: 32px;
  padding: 0 14px;
}

.secondary-button--danger {
  border-color: color-mix(in srgb, var(--state-danger) 30%, var(--border-default) 70%);
  background: color-mix(in srgb, var(--state-danger) 16%, var(--surface-panel) 84%);
}

.secondary-button--danger:hover {
  border-color: color-mix(in srgb, var(--state-danger) 62%, var(--interactive-focus-border) 38%);
  background: color-mix(in srgb, var(--state-danger) 26%, var(--surface-elevated) 74%);
}
</style>

<script setup lang="ts">
const props = withDefaults(defineProps<{
  checked: boolean;
  label: string;
  disabled?: boolean;
  ariaLabel?: string;
  title?: string;
}>(), {
  disabled: false,
  ariaLabel: '',
  title: '',
});

const emit = defineEmits<{
  toggle: [checked: boolean];
}>();

function handleClick(): void {
  if (props.disabled) {
    return;
  }

  emit('toggle', !props.checked);
}
</script>

<template>
  <button
    type="button"
    class="labeled-switch"
    :class="{ 'is-checked': checked }"
    :disabled="disabled"
    :aria-pressed="checked"
    :aria-label="ariaLabel"
    :title="title"
    @click="handleClick"
  >
    <span class="labeled-switch__label">{{ label }}</span>
    <span class="labeled-switch__track">
      <span class="labeled-switch__thumb" :class="{ 'is-checked': checked }"></span>
    </span>
  </button>
</template>

<style scoped>
.labeled-switch {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 28px;
  padding: 0 6px 0 6px;
  border: 1px solid var(--room-card-border);
  border-radius: 8px;
  background: var(--surface-pill);
  color: var(--text-primary);
  cursor: pointer;
  transition:
    border-color 0.18s ease,
    background 0.18s ease,
    color 0.18s ease,
    box-shadow 0.18s ease;
}

.labeled-switch:hover:not(:disabled) {
  border-color: var(--interactive-focus-border);
  background: color-mix(in srgb, var(--interactive-selected) 40%, var(--surface-panel) 60%);
}

.labeled-switch:disabled {
  opacity: 0.62;
  cursor: not-allowed;
}

.labeled-switch:focus-visible {
  border-color: var(--interactive-focus-border);
  box-shadow: 0 0 0 2px var(--interactive-focus-ring);
  outline: none;
}

.labeled-switch.is-checked {
  color: var(--state-success);
}

.labeled-switch__label {
  font-size: 0.72rem;
  font-weight: 500;
  white-space: nowrap;
}

.labeled-switch__track {
  position: relative;
  width: 30px;
  height: 16px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--state-danger) 16%, var(--room-card-border) 84%);
  transition: background 0.18s ease;
}

.labeled-switch.is-checked .labeled-switch__track {
  background: color-mix(in srgb, var(--state-success) 24%, var(--room-card-border) 76%);
}

.labeled-switch__thumb {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: color-mix(in srgb, var(--surface-panel) 88%, white 12%);
  box-shadow: 0 1px 1px rgba(0, 0, 0, 0.12);
  transition:
    transform 0.18s ease,
    background 0.18s ease;
}

.labeled-switch__thumb.is-checked {
  transform: translateX(14px);
  background: color-mix(in srgb, var(--surface-panel) 72%, white 28%);
}
</style>

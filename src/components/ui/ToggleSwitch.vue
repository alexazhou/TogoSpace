<script setup lang="ts">
const props = withDefaults(defineProps<{
  checked: boolean;
  label?: string;
  disabled?: boolean;
  variant?: 'pill' | 'inline';
  size?: 'sm' | 'md';
}>(), {
  label: '',
  disabled: false,
  variant: 'pill',
  size: 'md',
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
    class="ui-toggle"
    :class="[
      `ui-toggle--${variant}`,
      `ui-toggle--${size}`,
      { 'is-checked': checked },
    ]"
    :disabled="disabled"
    role="switch"
    :aria-checked="checked"
    @click="handleClick"
  >
    <span v-if="label" class="ui-toggle__label">{{ label }}</span>
    <span class="ui-toggle__track">
      <span class="ui-toggle__thumb"></span>
    </span>
  </button>
</template>

<style scoped>
.ui-toggle {
  --toggle-gap: 8px;
  --toggle-height: 26px;
  --toggle-padding-inline-start: 10px;
  --toggle-padding-inline-end: 4px;
  --toggle-label-size: 0.66rem;
  --toggle-label-weight: 600;
  --toggle-track-width: 34px;
  --toggle-track-height: 18px;
  --toggle-thumb-size: 14px;
  --toggle-thumb-offset: 16px;
  display: inline-flex;
  align-items: center;
  gap: var(--toggle-gap);
  min-height: var(--toggle-height);
  font: inherit;
  cursor: pointer;
  outline: none;
  box-shadow: none;
  transition:
    border-color 0.18s ease,
    background 0.18s ease,
    color 0.18s ease,
    box-shadow 0.18s ease;
}

.ui-toggle:disabled {
  opacity: 0.62;
  cursor: not-allowed;
}

.ui-toggle:focus-visible {
  box-shadow: 0 0 0 2px var(--focus-glow);
}

.ui-toggle__label {
  font-size: var(--toggle-label-size);
  font-weight: var(--toggle-label-weight);
  white-space: nowrap;
}

.ui-toggle__track {
  position: relative;
  width: var(--toggle-track-width);
  height: var(--toggle-track-height);
  flex: 0 0 auto;
  border-radius: 999px;
  background: color-mix(in srgb, var(--danger) 18%, var(--panel-border) 82%);
  transition: background 0.18s ease;
}

.ui-toggle__thumb {
  position: absolute;
  top: 2px;
  left: 2px;
  width: var(--toggle-thumb-size);
  height: var(--toggle-thumb-size);
  border-radius: 50%;
  background: color-mix(in srgb, var(--panel-bg) 84%, white 16%);
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.16);
  transition:
    transform 0.18s ease,
    background 0.18s ease;
}

.ui-toggle.is-checked .ui-toggle__track {
  background: color-mix(in srgb, var(--good) 24%, var(--panel-border) 76%);
}

.ui-toggle.is-checked .ui-toggle__thumb {
  transform: translateX(var(--toggle-thumb-offset));
  background: color-mix(in srgb, var(--panel-bg) 66%, white 34%);
}

.ui-toggle--pill {
  padding: 0 var(--toggle-padding-inline-end) 0 var(--toggle-padding-inline-start);
  border: 1px solid color-mix(in srgb, var(--focus-border) 26%, var(--panel-border) 74%);
  border-radius: 999px;
  background: color-mix(in srgb, var(--panel-bg) 86%, var(--surface-soft) 14%);
  color: var(--muted);
}

.ui-toggle--pill:hover:not(:disabled) {
  border-color: var(--focus-border);
  background: color-mix(in srgb, var(--selected) 40%, var(--panel-bg) 60%);
}

.ui-toggle--pill:focus-visible {
  border-color: var(--focus-border);
}

.ui-toggle--pill.is-checked {
  color: var(--good);
}

.ui-toggle--inline {
  padding: 0;
  border: none;
  background: transparent;
  color: var(--muted);
  gap: 10px;
}

.ui-toggle--inline:hover:not(:disabled) {
  color: var(--text-strong);
}

.ui-toggle--inline .ui-toggle__label {
  font-size: 0.82rem;
  font-weight: 400;
}

.ui-toggle--inline .ui-toggle__track {
  width: 36px;
  height: 20px;
  background: var(--panel-border);
}

.ui-toggle--inline .ui-toggle__thumb {
  width: 16px;
  height: 16px;
  background: #fff;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.2);
}

.ui-toggle--inline.is-checked .ui-toggle__track {
  background: var(--good);
}

.ui-toggle--inline.is-checked .ui-toggle__thumb {
  transform: translateX(16px);
  background: #fff;
}

.ui-toggle--sm {
  --toggle-gap: 6px;
  --toggle-height: 24px;
  --toggle-padding-inline-start: 9px;
  --toggle-padding-inline-end: 6px;
  --toggle-label-size: 0.72rem;
  --toggle-label-weight: 500;
  --toggle-track-width: 30px;
  --toggle-track-height: 16px;
  --toggle-thumb-size: 12px;
  --toggle-thumb-offset: 14px;
}
</style>

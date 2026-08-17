<script setup lang="ts">
withDefaults(
  defineProps<{
    tone?: 'default' | 'muted' | 'success' | 'danger' | 'info';
    size?: 'xs' | 'sm' | 'md';
    shape?: 'pill' | 'rounded';
  }>(),
  {
    tone: 'default',
    size: 'md',
    shape: 'pill',
  },
);
</script>

<template>
  <span
    class="ui-tag"
    :class="[
      `ui-tag--tone-${tone}`,
      `ui-tag--size-${size}`,
      `ui-tag--shape-${shape}`,
    ]"
  >
    <slot />
  </span>
</template>

<style scoped>
.ui-tag {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  white-space: nowrap;
  line-height: 1;
  border: 1px solid var(--panel-border);
  background: var(--panel-bg);
  color: var(--text-strong);
}

.ui-tag--size-xs {
  min-height: 18px;
  padding: 0 6px;
  font-size: 0.66rem;
}

.ui-tag--size-sm {
  min-height: 20px;
  padding: 0 8px;
  font-size: 0.7rem;
}

.ui-tag--size-md {
  min-height: 24px;
  padding: 0 10px;
  font-size: 0.74rem;
}

.ui-tag--shape-pill {
  border-radius: 999px;
}

.ui-tag--shape-rounded {
  border-radius: 6px;
}

.ui-tag--tone-muted {
  color: var(--muted);
}

.ui-tag--tone-success {
  border-color: color-mix(in srgb, var(--state-success) 30%, var(--panel-border) 70%);
  background: color-mix(in srgb, var(--state-success) 12%, var(--panel-bg) 88%);
  color: var(--good);
}

.ui-tag--tone-danger {
  border-color: color-mix(in srgb, var(--danger) 30%, var(--panel-border) 70%);
  background: color-mix(in srgb, var(--danger) 12%, var(--panel-bg) 88%);
  color: var(--danger);
}

.ui-tag--tone-info {
  border-color: color-mix(in srgb, var(--state-info) 26%, var(--panel-border) 74%);
  background: color-mix(in srgb, var(--state-info) 12%, var(--panel-bg) 88%);
  color: var(--accent);
}
</style>
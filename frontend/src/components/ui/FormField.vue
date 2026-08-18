<script setup lang="ts">
withDefaults(
  defineProps<{
    label?: string;
    wide?: boolean;
    hint?: string;
  }>(),
  {
    label: '',
    wide: false,
    hint: '',
  },
);
</script>

<template>
  <label class="ui-field" :class="{ 'ui-field--wide': wide }">
    <span v-if="label || $slots.label" class="ui-field__label">
      <slot name="label">{{ label }}</slot>
    </span>
    <slot />
    <p v-if="hint || $slots.hint" class="ui-field__hint">
      <slot name="hint">{{ hint }}</slot>
    </p>
  </label>
</template>

<style scoped>
.ui-field {
  display: grid;
  gap: 6px;
}

.ui-field--wide {
  grid-column: 1 / -1;
}

.ui-field__label {
  color: var(--muted);
  font-size: 0.76rem;
}

.ui-field__hint {
  margin: 0;
  color: var(--muted);
  font-size: 0.74rem;
}
</style>
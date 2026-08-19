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
  <label class="form-field" :class="{ 'form-field--wide': wide }">
    <span v-if="label || $slots.label" class="form-field__label">
      <slot name="label">{{ label }}</slot>
    </span>
    <slot />
    <p v-if="hint || $slots.hint" class="form-field__hint">
      <slot name="hint">{{ hint }}</slot>
    </p>
  </label>
</template>

<style scoped>
.form-field {
  display: grid;
  gap: 6px;
}

.form-field--wide {
  grid-column: 1 / -1;
}

.form-field__label {
  color: var(--muted);
  font-size: 0.76rem;
}

.form-field__hint {
  margin: 0;
  color: var(--muted);
  font-size: 0.74rem;
}
</style>
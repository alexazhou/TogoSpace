<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';

type CustomSelectOption = {
  value: string;
  label: string;
  category?: string;
  categoryType?: 'info' | 'success' | 'warning' | 'danger';
};

const props = defineProps<{
  modelValue: string[];
  options: CustomSelectOption[];
  placeholder?: string;
  disabled?: boolean;
}>();

const emit = defineEmits<{
  'update:modelValue': [value: string[]];
}>();

const rootRef = ref<HTMLElement | null>(null);
const open = ref(false);

const selectedItems = computed(() => {
  return props.modelValue.map(val => {
    const opt = props.options.find(o => o.value === val);
    return opt ? opt : { value: val, label: val };
  });
});

function closeMenu(): void {
  open.value = false;
}

function toggleMenu(): void {
  if (props.disabled) {
    return;
  }
  open.value = !open.value;
}

function selectOption(value: string): void {
  const newSet = new Set(props.modelValue);
  if (newSet.has(value)) {
    newSet.delete(value);
  } else {
    newSet.add(value);
  }
  emit('update:modelValue', Array.from(newSet));
  // Don't close menu on multi-select so user can select multiple
}

function handleDocumentPointerDown(event: PointerEvent): void {
  if (!open.value) {
    return;
  }

  const root = rootRef.value;
  if (!root) {
    return;
  }

  const target = event.target;
  if (target instanceof Node && !root.contains(target)) {
    closeMenu();
  }
}

function handleEscape(event: KeyboardEvent): void {
  if (event.key === 'Escape' && open.value) {
    closeMenu();
  }
}

onMounted(() => {
  document.addEventListener('pointerdown', handleDocumentPointerDown);
  document.addEventListener('keydown', handleEscape);
});

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', handleDocumentPointerDown);
  document.removeEventListener('keydown', handleEscape);
});
</script>

<template>
  <div ref="rootRef" class="custom-select" :class="{ 'is-open': open, 'is-disabled': disabled }">
    <button
      type="button"
      class="custom-select__button"
      :disabled="disabled"
      :aria-expanded="open"
      aria-haspopup="listbox"
      @click="toggleMenu"
    >
      <div class="custom-select__tags" v-if="selectedItems.length > 0">
        <span v-for="item in selectedItems" :key="item.value" class="custom-select__tag">
          {{ item.label }}
        </span>
      </div>
      <span v-else class="custom-select__placeholder">{{ placeholder || '请选择' }}</span>
      <svg class="custom-select__icon" viewBox="0 0 16 16" aria-hidden="true">
        <path d="m4 6 4 4 4-4" />
      </svg>
    </button>

    <div v-if="open" class="custom-select__menu" role="listbox" aria-multiselectable="true">
      <button
        v-for="option in options"
        :key="option.value"
        type="button"
        class="custom-select__option"
        :class="{ 'is-selected': modelValue.includes(option.value) }"
        role="option"
        :aria-selected="modelValue.includes(option.value)"
        @click="selectOption(option.value)"
      >
        <span class="custom-select__option-text">
          {{ option.label }}
          <span v-if="option.category" :class="['custom-select__category', option.categoryType ? `is-${option.categoryType}` : '']">{{ option.category }}</span>
        </span>
        <span v-if="modelValue.includes(option.value)" class="custom-select__check">✓</span>
      </button>
      <div v-if="options.length === 0" class="custom-select__empty">
        暂无选项
      </div>
    </div>
  </div>
</template>

<style scoped>
.custom-select {
  position: relative;
}

.custom-select__button {
  width: 100%;
  min-height: 48px;
  padding: 10px 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  border: 1px solid var(--panel-border);
  border-radius: 12px;
  background: var(--panel-bg);
  color: var(--text-strong);
  cursor: pointer;
  font: inherit;
  text-align: left;
}

.custom-select__button:hover,
.custom-select__button:focus-visible,
.custom-select.is-open .custom-select__button {
  border-color: var(--focus-border);
}

.custom-select__button:disabled {
  cursor: not-allowed;
  opacity: 0.7;
}

.custom-select__placeholder {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-secondary);
}

.custom-select__tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  min-width: 0;
}

.custom-select__tag {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border-radius: 6px;
  background: color-mix(in srgb, var(--interactive-selected) 75%, transparent);
  border: 1px solid var(--interactive-focus-border);
  color: var(--text-primary);
  font-size: 0.8rem;
  line-height: 1.4;
  white-space: nowrap;
}

.custom-select__icon {
  width: 12px;
  height: 12px;
  flex: 0 0 auto;
  fill: none;
  stroke: var(--accent);
  stroke-width: 1.8;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.custom-select__menu {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  right: 0;
  z-index: 24;
  max-height: 240px;
  overflow: auto;
  padding: 6px;
  display: grid;
  gap: 4px;
  border: 1px solid var(--panel-border);
  border-radius: 12px;
  background: color-mix(in srgb, var(--panel-bg) 96%, var(--surface-soft) 4%);
  box-shadow: 0 10px 24px rgba(0, 0, 0, 0.14);
}

:root[data-theme='light'] .custom-select__menu {
  background: #ffffff;
}

.custom-select__option {
  width: 100%;
  min-height: 38px;
  padding: 0 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  border: 1px solid transparent;
  border-radius: 10px;
  background: transparent;
  color: var(--text-strong);
  cursor: pointer;
  font: inherit;
  text-align: left;
}

.custom-select__option-text {
  display: flex;
  align-items: center;
  gap: 8px;
}

.custom-select__category {
  font-size: 0.75rem;
  padding: 2px 8px;
  border-radius: 12px;
  background: color-mix(in srgb, var(--text-primary) 12%, transparent);
  color: var(--text-primary);
  border: 1px solid color-mix(in srgb, var(--text-primary) 20%, transparent);
}

.custom-select__category.is-info {
  background: color-mix(in srgb, var(--state-info) 15%, transparent);
  color: var(--state-info);
  border-color: color-mix(in srgb, var(--state-info) 50%, transparent);
}

.custom-select__category.is-success {
  background: color-mix(in srgb, var(--state-success) 15%, transparent);
  color: var(--state-success);
  border-color: color-mix(in srgb, var(--state-success) 50%, transparent);
}

.custom-select__category.is-warning {
  background: color-mix(in srgb, var(--state-warning) 15%, transparent);
  color: var(--state-warning);
  border-color: color-mix(in srgb, var(--state-warning) 50%, transparent);
}

.custom-select__category.is-danger {
  background: color-mix(in srgb, var(--state-danger) 15%, transparent);
  color: var(--state-danger);
  border-color: color-mix(in srgb, var(--state-danger) 50%, transparent);
}

.custom-select__option:hover,
.custom-select__option:focus-visible,
.custom-select__option.is-selected {
  border-color: color-mix(in srgb, var(--focus-border) 42%, transparent);
  background: color-mix(in srgb, var(--selected) 72%, var(--surface-soft) 28%);
}

.custom-select__check {
  color: var(--accent);
  font-size: 0.9rem;
}

.custom-select__empty {
  padding: 12px;
  text-align: center;
  color: var(--text-secondary);
  font-size: 0.85rem;
}
</style>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, type CSSProperties } from 'vue';
import { useI18n } from 'vue-i18n';
import type { LlmProviderConfig } from '../../types';
import HoverTooltip from './HoverTooltip.vue';

type ModelOption = {
  provider: string;
  model: string;
};

const props = defineProps<{
  modelValue: string | null;
  providers: LlmProviderConfig[];
  placeholder?: string;
  disabled?: boolean;
}>();

const emit = defineEmits<{
  'update:modelValue': [value: string];
}>();

const { t } = useI18n();

const rootRef = ref<HTMLElement | null>(null);
const buttonRef = ref<HTMLElement | null>(null);
const dropdownRef = ref<HTMLElement | null>(null);
const open = ref(false);
const hoveredProvider = ref('');

// 当前选中的服务商（从 modelValue 解析）
const actualSelectedProvider = computed(() => {
  if (!props.modelValue) return '';
  const atIndex = props.modelValue.indexOf('@');
  return atIndex === -1 ? '' : props.modelValue.substring(atIndex + 1);
});

// 右侧显示的模型列表基于 hover 的服务商
const displayProvider = computed(() => hoveredProvider.value || actualSelectedProvider.value);

const dropdownStyle = computed<CSSProperties>(() => {
  if (!buttonRef.value) return {};
  const rect = buttonRef.value.getBoundingClientRect();
  return {
    position: 'fixed',
    top: `${rect.bottom + 4}px`,
    left: `${rect.left}px`,
  };
});

const enabledProviders = computed(() =>
  props.providers.filter(p => p.enable && p.models.length > 0)
);

const modelsForDisplayProvider = computed(() => {
  const provider = enabledProviders.value.find(p => p.name === displayProvider.value);
  return provider?.models.map(m => m.name) ?? [];
});

const displayModel = computed(() => {
  if (!props.modelValue) return '';
  const atIndex = props.modelValue.indexOf('@');
  return atIndex === -1 ? props.modelValue : props.modelValue.substring(0, atIndex);
});

const displayProviderName = computed(() => {
  if (!props.modelValue) return '';
  const atIndex = props.modelValue.indexOf('@');
  return atIndex === -1 ? '' : props.modelValue.substring(atIndex + 1);
});

function toggleMenu(): void {
  if (props.disabled) return;
  if (!open.value) {
    hoveredProvider.value = actualSelectedProvider.value || (enabledProviders.value[0]?.name ?? '');
  }
  open.value = !open.value;
}

function closeMenu(): void {
  open.value = false;
}

function selectModel(modelName: string): void {
  emit('update:modelValue', `${modelName}@${displayProvider.value}`);
  closeMenu();
}

function handleDocumentPointerDown(event: PointerEvent): void {
  if (!open.value) return;
  const target = event.target;
  if (!(target instanceof Node)) return;
  const inRoot = rootRef.value?.contains(target);
  const inDropdown = dropdownRef.value?.contains(target);
  if (!inRoot && !inDropdown) {
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
  <div ref="rootRef" class="model-select" :class="{ 'is-open': open, 'is-disabled': disabled }">
    <button
      ref="buttonRef"
      type="button"
      class="model-select__button"
      :disabled="disabled"
      :aria-expanded="open"
      aria-haspopup="listbox"
      @click="toggleMenu"
    >
      <span class="model-select__label">
        <template v-if="modelValue">
          <span class="model-select__label-text">{{ displayModel }}</span>
          <HoverTooltip v-if="displayProviderName" :text="t('settings.models.providerLabel', '供应商')" position="bottom">
            <span class="model-select__label-tag">{{ displayProviderName }}</span>
          </HoverTooltip>
        </template>
        <template v-else>{{ placeholder || t('common.notConfigured', '未配置') }}</template>
      </span>
      <svg class="model-select__icon" viewBox="0 0 16 16" aria-hidden="true">
        <path d="m4 6 4 4 4-4" />
      </svg>
    </button>

    <Teleport to="body">
    <div v-if="open" ref="dropdownRef" class="model-select__dropdown" :style="dropdownStyle">
      <!-- 左侧：供应商 -->
      <div class="model-select__panel model-select__panel--providers">
        <div class="model-select__panel-header">{{ t('settings.models.providerLabel', 'Provider') }}</div>
        <button
          v-for="p in enabledProviders"
          :key="p.name"
          type="button"
          class="model-select__option"
          :class="{ 'is-selected': p.name === displayProvider }"
          @mouseenter="hoveredProvider = p.name"
        >
          <span>{{ p.name }}</span>
          <span class="model-select__indicators">
            <span v-if="p.name === actualSelectedProvider" class="model-select__check">✓</span>
            <span v-if="p.name === displayProvider" class="model-select__arrow">›</span>
          </span>
        </button>
      </div>

      <!-- 右侧：模型 -->
      <div class="model-select__panel model-select__panel--models">
        <div class="model-select__panel-header">{{ t('settings.models.modelLabel', 'Model') }}</div>
        <template v-if="modelsForDisplayProvider.length">
          <button
            v-for="m in modelsForDisplayProvider"
            :key="m"
            type="button"
            class="model-select__option"
            :class="{ 'is-selected': `${m}@${displayProvider}` === modelValue }"
            @click="selectModel(m)"
          >
            <span>{{ m }}</span>
            <span v-if="`${m}@${displayProvider}` === modelValue" class="model-select__check">✓</span>
          </button>
        </template>
        <p v-else class="model-select__empty">{{ t('common.notConfigured', '未配置') }}</p>
      </div>
    </div>
    </Teleport>
  </div>
</template>

<style scoped>
.model-select {
  position: relative;
}

.model-select__button {
  width: 100%;
  height: 38px;
  padding: 0 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  border: 1px solid var(--panel-border);
  border-radius: 12px;
  background: var(--panel-bg);
  color: var(--text-strong);
  cursor: pointer;
  font: inherit;
  text-align: left;
  box-sizing: border-box;
}

.model-select__button:hover,
.model-select__button:focus-visible,
.model-select.is-open .model-select__button {
  border-color: var(--focus-border);
}

.model-select__button:disabled {
  cursor: not-allowed;
  opacity: 0.7;
}

.model-select__label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  display: flex;
  align-items: center;
  gap: 6px;
}

.model-select__label-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.model-select__label-tag {
  flex: 0 0 auto;
  padding: 2px 8px;
  border-radius: 6px;
  background: color-mix(in srgb, var(--accent) 16%, transparent);
  border: 1px solid color-mix(in srgb, var(--accent) 30%, transparent);
  color: var(--accent);
  font-size: 0.72rem;
  font-weight: 500;
  white-space: nowrap;
}

.model-select__icon {
  width: 12px;
  height: 12px;
  flex: 0 0 auto;
  fill: none;
  stroke: var(--accent);
  stroke-width: 1.8;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.model-select__dropdown {
  z-index: 9999;
  display: flex;
  min-width: 360px;
  max-width: 480px;
  border: 1px solid var(--panel-border);
  border-radius: 12px;
  background: color-mix(in srgb, var(--panel-bg) 96%, var(--surface-soft) 4%);
  box-shadow: 0 10px 24px rgba(0, 0, 0, 0.14);
  overflow: hidden;
}

:root[data-theme='light'] .model-select__dropdown {
  background: #ffffff;
}

.model-select__panel {
  flex: 1;
  min-width: 0;
  max-height: 240px;
  overflow: auto;
  padding: 4px;
}

.model-select__panel--providers {
  flex: 0 0 180px;
  border-right: 1px solid var(--panel-border);
}

.model-select__panel--models {
  flex: 1;
}

.model-select__panel-header {
  padding: 6px 10px;
  color: var(--muted);
  font-size: 0.68rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.model-select__option {
  width: 100%;
  min-height: 34px;
  padding: 0 10px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  color: var(--text-strong);
  cursor: pointer;
  font: inherit;
  font-size: 0.82rem;
  text-align: left;
  overflow: hidden;
}

.model-select__option > span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.model-select__option:hover,
.model-select__option.is-selected {
  border-color: color-mix(in srgb, var(--focus-border) 42%, transparent);
  background: color-mix(in srgb, var(--selected) 72%, var(--surface-soft) 28%);
}

.model-select__indicators {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  flex: 0 0 auto;
}

.model-select__check {
  color: var(--accent);
  font-size: 0.8rem;
}

.model-select__arrow {
  color: var(--muted);
  font-size: 1rem;
}

.model-select__empty {
  padding: 8px 10px;
  margin: 0;
  color: var(--muted);
  font-size: 0.82rem;
}
</style>

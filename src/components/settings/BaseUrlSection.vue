<script setup lang="ts">
import { ref, computed } from 'vue';
import { useI18n } from 'vue-i18n';

interface UrlItem {
  type: string;
  url: string;
}

const props = defineProps<{
  urls: UrlItem[];
  protocolTypes: { value: string; label: string }[];
  presetUrls?: Record<string, string>;
}>();

const emit = defineEmits<{
  save: [urls: UrlItem[]];
}>();

const { t } = useI18n();

const urlsMode = ref<'view' | 'edit'>('view');
const editForm = ref<UrlItem[]>([]);

const mergedUrls = computed(() => {
  const preset = props.presetUrls ?? {};
  const customMap: Record<string, string> = {};
  props.urls.forEach(item => {
    if (item.url) customMap[item.type] = item.url;
  });
  const keys = new Set([...Object.keys(preset), ...Object.keys(customMap)]);
  return Array.from(keys).map(key => ({
    key,
    url: customMap[key] ?? preset[key] ?? '',
    isCustom: !!customMap[key],
  }));
});

function startEdit(): void {
  editForm.value = props.urls.map(u => ({ ...u }));
  if (editForm.value.length === 0) {
    editForm.value.push({ type: 'openai', url: '' });
  }
  urlsMode.value = 'edit';
}

function cancelEdit(): void {
  urlsMode.value = 'view';
}

function addUrl(): void {
  const usedTypes = editForm.value.map(u => u.type);
  const available = props.protocolTypes.find(t => !usedTypes.includes(t.value));
  if (available) {
    editForm.value.push({ type: available.value, url: '' });
  }
}

function removeUrl(index: number): void {
  editForm.value.splice(index, 1);
}

function saveEdit(): void {
  emit('save', editForm.value.filter(u => u.url.trim()));
  urlsMode.value = 'view';
}

function getProtocolLabel(type: string): string {
  return props.protocolTypes.find(t => t.value === type)?.label || type;
}
</script>

<template>
  <div class="base-url-section">
    <!-- View mode -->
    <template v-if="urlsMode === 'view'">
      <div class="urls-view-header">
        <div class="urls-view">
          <div v-for="item in mergedUrls" :key="item.key" class="url-view-item">
            <span class="url-key">{{ getProtocolLabel(item.key) }}:</span>
            <span class="url-value">{{ item.url }}</span>
            <span v-if="item.isCustom" class="custom-tag">自定义</span>
          </div>
          <div v-if="!mergedUrls.length" class="urls-empty">No URLs configured</div>
        </div>
        <button type="button" class="ghost-button toggle-btn" @click="startEdit">
          {{ t('common.edit') }}
        </button>
      </div>
    </template>

    <!-- Edit mode -->
    <template v-else>
      <div class="urls-edit">
        <div v-for="(item, index) in editForm" :key="index" class="url-edit-item">
          <select v-model="item.type" class="svc-input svc-select" style="width: 140px; flex-shrink: 0;">
            <option v-for="pt in protocolTypes" :key="pt.value" :value="pt.value" :disabled="editForm.some((u, i) => u.type === pt.value && i !== index)">
              {{ pt.label }}
            </option>
          </select>
          <input v-model="item.url" type="text" class="svc-input svc-input--flex" placeholder="https://..." />
          <button type="button" class="ghost-button" @click="removeUrl(index)" style="padding: 0 8px; color: var(--error);">
            ×
          </button>
        </div>
        <button type="button" class="secondary-button" @click="addUrl" :disabled="editForm.length >= protocolTypes.length" style="width: 100%; border-style: dashed; justify-content: center;">
          + Add URL
        </button>
        <div class="urls-edit-actions">
          <button type="button" class="secondary-button" @click="cancelEdit">{{ t('common.cancel') }}</button>
          <button type="button" class="secondary-button" @click="saveEdit">{{ t('common.confirm') }}</button>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.base-url-section {
  border: 1px solid var(--panel-border);
  border-radius: 12px;
  padding: 12px;
  margin-bottom: 4px;
  background: color-mix(in srgb, var(--panel-bg) 60%, var(--surface-panel) 40%);
}

.urls-view-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
}

.urls-view {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.url-view-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.84rem;
}

.url-key {
  color: var(--muted);
  flex-shrink: 0;
}

.url-value {
  color: var(--text-strong);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.custom-tag {
  display: inline-flex;
  align-items: center;
  padding: 0 6px;
  height: 18px;
  border-radius: 4px;
  background: color-mix(in srgb, var(--state-info) 12%, transparent);
  color: var(--state-info);
  font-size: 0.68rem;
  flex-shrink: 0;
}

.urls-empty {
  color: var(--muted);
  font-size: 0.84rem;
  font-style: italic;
}

.toggle-btn {
  font-size: 0.8rem;
  flex-shrink: 0;
}

.urls-edit {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.url-edit-item {
  display: flex;
  gap: 8px;
  align-items: center;
}

.urls-edit-actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
  margin-top: 8px;
}

.svc-input {
  height: 40px; width: 100%; border: 1px solid var(--panel-border); border-radius: 12px;
  background: var(--panel-bg); color: var(--text-strong); padding: 0 12px;
  font: inherit; font-size: 0.88rem; box-sizing: border-box;
}
.svc-input--flex { flex: 1; min-width: 0; }
</style>

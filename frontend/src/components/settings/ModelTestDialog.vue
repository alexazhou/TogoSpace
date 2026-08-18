<script setup lang="ts">
import { ref, computed } from 'vue';
import { useI18n } from 'vue-i18n';
import { testLlmProvider } from '../../api';
import type { LlmProviderConfig, LlmModelConfig, LlmTestResult } from '../../types';
import CustomSelect from '../ui/CustomSelect.vue';
import ModalDialog from '../ui/ModalDialog.vue';

const { t } = useI18n();

const visible = ref(false);
const testing = ref(false);
const result = ref<LlmTestResult | null>(null);

const providers = ref<LlmProviderConfig[]>([]);
const selectedProviderKey = ref('0');
const selectedModelKey = ref('0');
const providerLocked = ref(false);
const modelLocked = ref(false);

const providerOptions = computed(() =>
  providers.value.map((p, idx) => ({ value: String(idx), label: p.name || p.type }))
);

const currentProvider = computed(() => {
  const idx = Number(selectedProviderKey.value);
  return providers.value[idx] || null;
});

const currentModels = computed(() => currentProvider.value?.models || []);

const modelOptions = computed(() =>
  currentModels.value.map((m, idx) => ({ value: String(idx), label: m.name }))
);

const currentModel = computed(() => {
  const idx = Number(selectedModelKey.value);
  return currentProvider.value?.models[idx] || null;
});

const protocolDisplay = computed(() => currentModel.value?.protocol || 'openai');

function onProviderChange() {
  selectedModelKey.value = '0';
}

function openFromModel(allProviders: LlmProviderConfig[], providerIdx: number, modelIdx: number) {
  providers.value = allProviders;
  selectedProviderKey.value = String(providerIdx);
  selectedModelKey.value = String(modelIdx);
  providerLocked.value = true;
  modelLocked.value = true;
  result.value = null;
  visible.value = true;
}

function openFromProvider(allProviders: LlmProviderConfig[], providerIdx: number) {
  providers.value = allProviders;
  selectedProviderKey.value = String(providerIdx);
  selectedModelKey.value = '0';
  providerLocked.value = true;
  modelLocked.value = false;
  result.value = null;
  visible.value = true;
}

function close() {
  visible.value = false;
}

async function runTest() {
  if (!currentProvider.value || !currentModel.value) return;
  testing.value = true;
  result.value = null;

  try {
    const res = await testLlmProvider({
      provider: currentProvider.value,
      model: currentModel.value,
      protocol: protocolDisplay.value,
    });
    result.value = res;
  } catch (e) {
    result.value = { status: 'error', message: String(e) };
  } finally {
    testing.value = false;
  }
}

defineExpose({ openFromModel, openFromProvider });
</script>

<template>
  <ModalDialog
    :open="visible"
    :title="t('settings.models.testDialog.title', '模型可用性测试')"
    :width="500"
    :scrollbar="false"
    @close="close"
  >
    <div class="config-mode-row">
          <span class="row-label">供应商</span>
          <div class="select-wrap">
            <CustomSelect
              v-model="selectedProviderKey"
              :options="providerOptions"
              :disabled="providerLocked"
              placeholder="请选择"
              :compact="true"
              @update:model-value="onProviderChange"
            />
          </div>
        </div>

        <div class="config-mode-row">
          <span class="row-label">模型</span>
          <div v-if="currentModels.length === 0" class="no-model-hint">请先添加模型</div>
          <div v-else class="select-wrap">
            <CustomSelect
              v-model="selectedModelKey"
              :options="modelOptions"
              :disabled="modelLocked"
              placeholder="请选择"
              :compact="true"
            />
          </div>
        </div>

        <div class="config-mode-row">
          <span class="row-label">协议</span>
          <span class="protocol-text">{{ protocolDisplay }}</span>
        </div>

        <button
          type="button"
          class="test-start-btn secondary-button"
          :disabled="testing || !currentModel"
          @click="runTest"
        >
          {{ testing ? t('settings.models.testDialog.testing', '测试中...') : t('settings.models.testDialog.start', '开始测试') }}
        </button>

        <div v-if="result" class="test-result">
          <div class="config-mode-row">
            <span class="row-label">测试结果</span>
            <span class="status-dot" :class="result.status === 'ok' ? 'status-dot--success' : 'status-dot--error'"></span>
            <span class="status-text" :class="result.status === 'ok' ? 'status-success' : 'status-error'">
              {{ result.status === 'ok' ? '测试成功' : '测试失败' }}
            </span>
            <span v-if="result.status === 'ok' && result.detail" class="status-detail">
              耗时: {{ result.detail.duration_ms }}ms
            </span>
          </div>

          <div v-if="result.status !== 'ok'" class="response-section">
            <p class="section-label">错误详情</p>
            <pre class="response-display scrollbar-thin">{{ result.message }}</pre>
          </div>
        </div>

        <template #footer-trailing>
      <button type="button" class="secondary-button" @click="close">{{ t('common.close', '关闭') }}</button>
    </template>
  </ModalDialog>
</template>

<style scoped>

.config-mode-row { display: flex; align-items: center; gap: 12px; }
.row-label { font-size: 13px; color: var(--text-strong); width: 56px; flex-shrink: 0; }
.select-wrap { flex: 1; min-width: 0; }
.no-model-hint { font-size: 13px; color: var(--text-secondary); }
.protocol-text { font-size: 13px; color: var(--text); }

.section-label { margin: 0 0 8px; font-size: 0.8rem; font-weight: 600; color: var(--text-strong); }

.test-start-btn { width: 100%; padding: 10px; font-size: 0.9rem; }

.test-result { display: grid; gap: 8px; }
.status-dot { width: 8px; height: 8px; border-radius: 50%; }
.status-dot--success { background: var(--green, #22c55e); }
.status-dot--error { background: var(--red, #ef4444); }
.status-text { font-weight: 600; font-size: 0.88rem; }
.status-success { color: var(--green, #22c55e); }
.status-error { color: var(--red, #ef4444); }
.status-detail { color: var(--text-secondary); font-size: 0.82rem; }

.response-display {
  margin: 0; padding: 12px;
  background: var(--chat-bubble-left-bg, #3d454d);
  color: var(--text-primary);
  border-radius: 8px; font-size: 0.82rem; line-height: 1.5;
  max-height: 200px; overflow: auto; white-space: pre-wrap; word-break: break-all;
  font-family: 'SF Mono', 'Fira Code', monospace;
}
</style>

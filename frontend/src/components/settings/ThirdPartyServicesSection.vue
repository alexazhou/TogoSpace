<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useI18n } from 'vue-i18n';
import {
  getThirdPartyServicesConfig,
  saveThirdPartyServicesConfig,
  testDeepSeekSearchService,
  testXiaomiMimoSearchService,
} from '../../api';
import { showGlobalSuccessToast, showGlobalRequestError } from '../../appUiState';
import type {
  ThirdPartySearchResult,
  ThirdPartySearchService,
  ThirdPartyServicesConfigPayload,
} from '../../types';
import ToggleSwitch from '../ui/ToggleSwitch.vue';
import ConfirmDialog from '../ui/ConfirmDialog.vue';
import UiTag from '../ui/UiTag.vue';
import SettingsBreadcrumb from './SettingsBreadcrumb.vue';
import type { SettingsBreadcrumbItem } from './types';

type ServiceForm = {
  enabled: boolean;
  api_key: string;
};

const SERVICE_NAMES: ThirdPartySearchService[] = ['deepseek', 'xiaomi_mimo'];

const props = defineProps<{
  breadcrumbItems: SettingsBreadcrumbItem[];
  detailService?: string | null;
}>();

const emit = defineEmits<{
  navigateBreadcrumb: [key: string];
  openService: [serviceName: string];
  clearService: [];
}>();

const { t } = useI18n();

const isLoading = ref(false);
const isSaving = ref(false);
const statusText = ref('');
const showApiKey = ref(false);
const initialSnapshot = ref('');
const defaultSearchService = ref<ThirdPartySearchService>('deepseek');
const serviceForms = ref<Record<ThirdPartySearchService, ServiceForm>>({
  deepseek: { enabled: false, api_key: '' },
  xiaomi_mimo: { enabled: false, api_key: '' },
});

const showTestDialog = ref(false);
const testService = ref<ThirdPartySearchService>('deepseek');
const testQuery = ref('');
const isTesting = ref(false);
const testResult = ref<ThirdPartySearchResult | null>(null);

const showSaveConfirm = ref(false);
const showListToggleConfirm = ref(false);
const pendingService = ref<ThirdPartySearchService>('deepseek');
const pendingListToggleValue = ref(false);

const detailService = computed<ThirdPartySearchService | null>(() =>
  SERVICE_NAMES.includes(props.detailService as ThirdPartySearchService)
    ? props.detailService as ThirdPartySearchService
    : null,
);

const isDirty = computed(() => JSON.stringify(toPayload()) !== initialSnapshot.value);

function serviceLabel(service: ThirdPartySearchService): string {
  return service === 'deepseek'
    ? t('settings.thirdParty.deepseekTitle')
    : t('settings.thirdParty.mimoTitle');
}

function serviceSubtitle(service: ThirdPartySearchService): string {
  return service === 'deepseek'
    ? t('settings.thirdParty.deepseekSubtitle')
    : t('settings.thirdParty.mimoSubtitle');
}

function apiKeyPlaceholder(service: ThirdPartySearchService): string {
  return service === 'deepseek'
    ? t('settings.thirdParty.deepseekApiKeyPlaceholder')
    : t('settings.thirdParty.mimoApiKeyPlaceholder');
}

function serviceForm(service: ThirdPartySearchService): ServiceForm {
  return serviceForms.value[service];
}

function toPayload(): ThirdPartyServicesConfigPayload {
  return {
    third_party_services: {
      default_service: { search: defaultSearchService.value },
      deepseek: { ...serviceForms.value.deepseek },
      xiaomi_mimo: { ...serviceForms.value.xiaomi_mimo },
    },
  };
}

function takeSnapshot(): void {
  initialSnapshot.value = JSON.stringify(toPayload());
}

function handleConfirmSave(): void {
  showSaveConfirm.value = false;
  void saveConfig();
}

function handleConfirmListToggle(): void {
  showListToggleConfirm.value = false;
  serviceForm(pendingService.value).enabled = pendingListToggleValue.value;
  void saveConfig();
}

function openTestDialog(service: ThirdPartySearchService): void {
  testService.value = service;
  showTestDialog.value = true;
  const d = new Date();
  const dateStr = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  testQuery.value = `今天 ${dateStr} 天气怎么样`;
  testResult.value = null;
  isTesting.value = false;
}

function closeTestDialog(): void {
  showTestDialog.value = false;
  testResult.value = null;
  isTesting.value = false;
}

async function runTest(): Promise<void> {
  if (!testQuery.value.trim() || isTesting.value) {
    return;
  }
  isTesting.value = true;
  testResult.value = null;
  const form = serviceForm(testService.value);
  const payload = {
    enabled: form.enabled,
    api_key: form.api_key,
    query: testQuery.value.trim(),
  };
  try {
    testResult.value = testService.value === 'deepseek'
      ? await testDeepSeekSearchService(payload)
      : await testXiaomiMimoSearchService(payload);
  } catch (error: any) {
    console.error(error);
    testResult.value = {
      success: false,
      service: testService.value,
      error_message: error?.message || t('settings.thirdParty.testFailed'),
    };
  } finally {
    isTesting.value = false;
  }
}

async function loadData(): Promise<void> {
  isLoading.value = true;
  statusText.value = '';
  try {
    const config = await getThirdPartyServicesConfig();
    const services = config.third_party_services;
    defaultSearchService.value = services.default_service.search;
    serviceForms.value = {
      deepseek: {
        enabled: Boolean(services.deepseek.enabled),
        api_key: services.deepseek.api_key || '',
      },
      xiaomi_mimo: {
        enabled: Boolean(services.xiaomi_mimo.enabled),
        api_key: services.xiaomi_mimo.api_key || '',
      },
    };
    takeSnapshot();
  } catch (error) {
    console.error(error);
    statusText.value = t('settings.thirdParty.loadFailed');
  } finally {
    isLoading.value = false;
  }
}

function resetChanges(): void {
  if (!initialSnapshot.value) {
    return;
  }
  const snapshot = JSON.parse(initialSnapshot.value) as ThirdPartyServicesConfigPayload;
  const services = snapshot.third_party_services;
  defaultSearchService.value = services.default_service.search;
  serviceForms.value = {
    deepseek: { ...services.deepseek },
    xiaomi_mimo: { ...services.xiaomi_mimo },
  };
}

async function saveConfig(): Promise<void> {
  if (isSaving.value) {
    return;
  }
  isSaving.value = true;
  statusText.value = '';
  try {
    await saveThirdPartyServicesConfig(toPayload());
    takeSnapshot();
    showGlobalSuccessToast(t('settings.thirdParty.saveSuccess'));
  } catch (error) {
    console.error(error);
    statusText.value = t('settings.thirdParty.saveFailed');
  } finally {
    isSaving.value = false;
  }
}

function openService(service: ThirdPartySearchService): void {
  emit('openService', service);
}

function handleToggle(service: ThirdPartySearchService, value: boolean): void {
  if (value && !serviceForm(service).api_key) {
    showGlobalRequestError({
      title: t('settings.thirdParty.operationFailed'),
      path: '',
      detail: t('settings.thirdParty.apiKeyRequired'),
    });
    if (detailService.value !== service) {
      openService(service);
    }
    return;
  }
  pendingService.value = service;
  pendingListToggleValue.value = value;
  showListToggleConfirm.value = true;
}

onMounted(() => {
  void loadData();
});
</script>

<template>
  <section id="third-party-services" class="config-section third-party-section">
    <SettingsBreadcrumb :items="breadcrumbItems" @navigate="emit('navigateBreadcrumb', $event)" />

    <div v-if="statusText" class="section-status-bar">
      <span class="section-status">{{ statusText }}</span>
    </div>

    <p v-if="isLoading" class="section-status">{{ t('settings.thirdParty.loading') }}</p>

    <section v-else-if="props.detailService === null" class="service-list-panel">
      <header class="service-list-head">
        <div>
          <p>{{ t('settings.thirdParty.listSubtitle') }}</p>
        </div>
      </header>

      <div class="default-search-service-row">
        <label class="svc-field">
          <span>{{ t('settings.thirdParty.defaultSearchService') }}</span>
          <select v-model="defaultSearchService" class="default-search-service-select">
            <option value="deepseek">{{ serviceLabel('deepseek') }}</option>
            <option value="xiaomi_mimo">{{ serviceLabel('xiaomi_mimo') }}</option>
          </select>
        </label>
        <button
          type="button"
          class="primary-button btn-sm"
          :disabled="isSaving || !isDirty"
          @click="showSaveConfirm = true"
        >
          {{ isSaving ? t('settings.thirdParty.saving') : t('settings.thirdParty.saveButton') }}
        </button>
      </div>

      <div class="service-list">
        <article v-for="service in SERVICE_NAMES" :key="service" class="service-row">
          <div class="service-row-main">
            <strong>{{ serviceLabel(service) }}</strong>
            <span>{{ serviceSubtitle(service) }}</span>
            <div class="service-capabilities">
              <UiTag size="sm" shape="rounded">{{ t('settings.thirdParty.searchCapability') }}</UiTag>
            </div>
          </div>
          <div class="service-row-meta">
            <ToggleSwitch
              :checked="serviceForms[service].enabled"
              :label="serviceForms[service].enabled ? t('settings.thirdParty.enabled') : t('settings.thirdParty.disabled')"
              @toggle="handleToggle(service, $event)"
            />
            <button type="button" class="secondary-button btn-sm" @click="openService(service)">
              {{ t('settings.thirdParty.configure') }}
            </button>
          </div>
        </article>
      </div>
    </section>

    <section v-else-if="detailService" class="third-party-panel">
      <div class="third-party-form">
        <section class="capabilities-section">
          <h4>{{ t('settings.thirdParty.enableTitle') }}</h4>
          <div class="capability-row">
            <span style="font-size: 0.86rem; color: var(--text-strong); font-weight: 500;">{{ t('settings.thirdParty.enableService') }}</span>
            <ToggleSwitch
              :checked="serviceForms[detailService].enabled"
              @toggle="handleToggle(detailService, $event)"
            />
          </div>
        </section>

        <section class="capabilities-section">
          <h4>{{ t('settings.thirdParty.capabilitiesTitle') }}</h4>
          <div class="capability-row" style="justify-content: flex-start;">
            <UiTag size="sm" shape="rounded">{{ t('settings.thirdParty.searchCapability') }}</UiTag>
            <UiTag v-if="detailService === 'xiaomi_mimo'" size="sm">mimo-v2.5</UiTag>
          </div>
        </section>

        <section class="api-key-section">
          <h4>{{ t('settings.thirdParty.serviceConfigTitle') }}</h4>
          <label class="svc-field">
            <div class="capability-row" style="padding: 8px 14px;">
              <span style="font-size: 0.86rem; color: var(--text-strong); font-weight: 500; min-width: 60px;">{{ t('settings.thirdParty.apiKey') }}</span>
              <input
                v-model="serviceForms[detailService].api_key"
                class="api-key-input"
                :type="showApiKey ? 'text' : 'password'"
                autocomplete="off"
                :placeholder="apiKeyPlaceholder(detailService)"
              />
              <button type="button" class="secondary-button btn-sm" @click="showApiKey = !showApiKey">
                {{ showApiKey ? t('settings.thirdParty.hideKey') : t('settings.thirdParty.showKey') }}
              </button>
            </div>
          </label>
        </section>

        <div class="third-party-actions" style="justify-content: space-between;">
          <div class="third-party-actions-left">
            <button type="button" class="secondary-button" @click="openTestDialog(detailService)">
              {{ t('settings.thirdParty.testButton') }}
            </button>
          </div>
          <div class="third-party-actions-right" style="display: flex; gap: 10px;">
            <button v-if="isDirty" type="button" class="secondary-button" :disabled="isSaving" @click="resetChanges">
              {{ t('settings.thirdParty.resetChanges') }}
            </button>
            <button type="button" class="primary-button" :disabled="isSaving || !isDirty" @click="showSaveConfirm = true">
              {{ isSaving ? t('settings.thirdParty.saving') : t('settings.thirdParty.saveButton') }}
            </button>
          </div>
        </div>
      </div>
    </section>

    <Teleport to="body">
      <div v-if="showTestDialog" class="test-dialog-overlay" @click.self="closeTestDialog">
        <section class="test-dialog">
          <header class="test-dialog-head">
            <h3>{{ t('settings.thirdParty.testDialogTitle') }}</h3>
            <button type="button" class="test-dialog-close secondary-button" @click="closeTestDialog">
              <span class="icon-close">x</span>
            </button>
          </header>
          
          <div class="test-tabs">
            <button type="button" class="test-tab active">{{ t('settings.thirdParty.searchCapability') }}</button>
          </div>
          
          <div class="test-tab-panel" style="padding-top: 16px;">
            <label class="svc-field">
              <span class="svc-field-label" style="display: block; margin-bottom: 8px;">Query</span>
              <input 
                v-model="testQuery" 
                class="test-query-input" 
                :placeholder="t('settings.thirdParty.testQueryPlaceholder')"
              />
            </label>
            
            <div class="test-dialog-actions" style="justify-content: flex-end;">
              <button type="button" class="primary-button" :disabled="isTesting" @click="runTest">
                {{ isTesting ? t('settings.thirdParty.testing') : t('settings.thirdParty.testButton') }}
              </button>
            </div>
            
            <div v-if="testResult" class="test-result" :class="{ 'test-result--error': !testResult.success }">
              <div class="test-result-head">
                <span class="status-dot" :class="testResult.success ? 'status-dot--success' : 'status-dot--error'"></span>
                <span>{{ testResult.success ? t('settings.thirdParty.testSuccess') : t('settings.thirdParty.testFailed') }}</span>
                <span v-if="testResult.duration_ms">{{ testResult.duration_ms }} ms</span>
              </div>
              <pre v-if="testResult.content" class="result-content">{{ testResult.content }}</pre>
              <ul v-if="testResult.success && testResult.sources?.length" class="search-sources">
                <li v-for="(source, index) in testResult.sources" :key="`${source.url}-${index}`">
                  <a class="source-card" :href="source.url" target="_blank" rel="noreferrer">
                    <span class="source-card-index">{{ index + 1 }}</span>
                    <span class="source-card-body">
                      <span class="source-card-title">{{ source.title || source.url }}</span>
                      <span class="source-card-site">{{ source.site_name || source.url }}</span>
                    </span>
                  </a>
                </li>
              </ul>
              <p v-if="!testResult.success && testResult.error_message" style="margin: 0; color: var(--danger); font-size: 0.85rem;">{{ testResult.error_message }}</p>
            </div>
          </div>
        </section>
      </div>
    </Teleport>

    <ConfirmDialog
      :open="showSaveConfirm"
      :title="t('settings.thirdParty.saveConfirmTitle')"
      :message="t('settings.thirdParty.saveConfirmMessage')"
      @confirm="handleConfirmSave"
      @close="showSaveConfirm = false"
    />

    <ConfirmDialog
      :open="showListToggleConfirm"
      :title="t('settings.thirdParty.saveConfirmTitle')"
      :message="pendingListToggleValue ? '确定要开启该服务吗？' : '确定要关闭该服务吗？'"
      @confirm="handleConfirmListToggle"
      @close="showListToggleConfirm = false"
    />

  </section>
</template>

<style scoped>
.third-party-section {
  padding: 12px 0 0;
}

.section-status-bar {
  margin-bottom: 8px;
}

.section-status {
  color: var(--muted);
  font-size: 0.86rem;
}

.third-party-panel {
  display: grid;
  gap: 22px;
}

.service-list-panel {
  display: grid;
  gap: 14px;
}

.service-list-head p {
  margin: 0;
  color: var(--text-secondary);
  font-size: 0.76rem;
}

.service-list {
  display: grid;
  gap: 10px;
}

.default-search-service-row {
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 14px;
  border: 1px solid var(--panel-border);
  border-radius: 10px;
  background: var(--panel-bg);
}

.default-search-service-select {
  min-width: 180px;
  border: 1px solid var(--panel-border);
  border-radius: 8px;
  background: var(--surface-soft);
  color: var(--text-strong);
  padding: 7px 10px;
  font: inherit;
  font-size: 0.84rem;
}

.service-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  border: 1px solid var(--panel-border);
  border-radius: 10px;
  background: var(--panel-bg);
  padding: 12px 18px;
}

.service-row-main {
  min-width: 0;
  display: grid;
  gap: 7px;
}

.service-row-main strong {
  color: var(--text-strong);
  font-size: 1rem;
  line-height: 1.25;
}

.service-row-main span {
  color: var(--text-secondary);
  font-size: 0.74rem;
}

.service-capabilities {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.service-row-main .service-capabilities-label {
  color: var(--text-strong);
  font-size: 13px;
  font-weight: 400;
  line-height: 1.35;
}

.service-row-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 0 0 auto;
}

.third-party-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
}

.third-party-head-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 0 0 auto;
}

.third-party-head h3 {
  margin: 0;
  color: var(--text-strong);
  font-size: 1rem;
}

.third-party-head p {
  margin: 4px 0 0;
  color: var(--text-secondary);
  font-size: 0.76rem;
}

.third-party-form {
  display: grid;
  gap: 18px;
}

.svc-field {
  display: grid;
  gap: 6px;
}

.svc-field > span {
  color: var(--muted);
  font-size: 0.76rem;
}

.api-key-input {
  flex: 1;
  min-width: 0;
  border: none;
  background: transparent;
  color: var(--text-strong);
  font: inherit;
  font-size: 0.84rem;
  outline: none;
}

.test-query-input {
  width: 100%;
  min-width: 0;
  border: 1px solid color-mix(in srgb, var(--panel-border) 80%, var(--text-primary) 20%);
  border-radius: 12px;
  background: color-mix(in srgb, var(--panel-bg) 96%, var(--text-primary) 4%);
  color: var(--text-strong);
  padding: 10px 14px;
  font: inherit;
  font-size: 0.86rem;
  outline: none;
  box-sizing: border-box;
}

.test-query-input:focus {
  border-color: var(--focus-border);
  box-shadow: 0 0 0 2px var(--focus-glow);
}

.capability-row:has(.api-key-input:focus) {
  border-color: var(--focus-border);
  box-shadow: 0 0 0 2px var(--focus-glow);
}

.svc-textarea {
  resize: vertical;
  min-height: 86px;
  line-height: 1.5;
}

.capabilities-section {
  display: grid;
  gap: 10px;
}

.api-key-section {
  display: grid;
  gap: 10px;
}

.capabilities-section h4,
.api-key-section h4 {
  margin: 0;
  color: var(--text-strong);
  font-size: 0.9rem;
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

.capability-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  border: 1px solid var(--panel-border);
  border-radius: 10px;
  background: var(--panel-bg);
  padding: 12px 14px;
}

.capability-copy {
  min-width: 0;
  display: grid;
  gap: 4px;
}

.capability-copy strong {
  color: var(--text-strong);
  font-size: 0.86rem;
}

.capability-copy span {
  color: var(--text-secondary);
  font-size: 0.74rem;
}

.third-party-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
}

.btn-sm {
  min-width: 56px;
  height: 28px;
  padding: 0 10px;
  font-size: 0.78rem;
}

.test-dialog-overlay {
  position: fixed;
  inset: 0;
  z-index: 80;
  display: grid;
  place-items: center;
  padding: 20px;
  background: rgba(6, 10, 16, 0.56);
  backdrop-filter: blur(10px);
}

.test-dialog {
  width: min(620px, calc(100vw - 40px));
  max-height: calc(100vh - 40px);
  overflow: auto;
  display: grid;
  gap: 14px;
  padding: 18px;
  background: var(--panel-bg);
  border-radius: 12px;
}

.test-dialog-head,
.test-dialog-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.test-dialog-head h3 {
  margin: 0;
  color: var(--text-strong);
  font-size: 1rem;
}

.test-dialog-close {
  min-width: 32px;
  height: 32px;
  padding: 0;
  font-size: 1rem;
}

.test-tabs {
  display: flex;
  align-items: center;
  gap: 6px;
  border-bottom: 1px solid color-mix(in srgb, var(--divider) 76%, transparent);
}

.test-tab {
  border: 0;
  border-bottom: 2px solid transparent;
  background: transparent;
  color: var(--text-secondary);
  padding: 8px 10px;
  cursor: pointer;
  font: inherit;
  font-size: 0.82rem;
}

.test-tab.active {
  border-bottom-color: var(--focus-border);
  color: var(--text-strong);
  font-weight: 600;
}

.test-tab-panel {
  display: grid;
  gap: 12px;
}

.test-result {
  display: grid;
  gap: 10px;
  border: 1px solid color-mix(in srgb, var(--good) 30%, var(--panel-border) 70%);
  border-radius: 10px;
  background: color-mix(in srgb, var(--good) 8%, var(--panel-bg) 92%);
  padding: 12px;
}

.test-result--error {
  border-color: color-mix(in srgb, var(--danger) 30%, var(--panel-border) 70%);
  background: color-mix(in srgb, var(--danger) 8%, var(--panel-bg) 92%);
}

.test-result-head {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--text-strong);
  font-size: 0.84rem;
}

.test-result-head span:last-child {
  color: var(--text-secondary);
  font-size: 0.78rem;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex: 0 0 auto;
}

.status-dot--success {
  background: var(--good);
}

.status-dot--error {
  background: var(--danger);
}

.result-content {
  margin: 0;
  max-height: 300px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--text-primary);
  background: var(--panel-bg);
  border: 1px solid var(--panel-border);
  border-radius: 8px;
  padding: 10px;
  font-family: 'SF Mono', 'Fira Code', monospace;
  font-size: 0.78rem;
  line-height: 1.5;
}

.search-sources {
  display: grid;
  gap: 6px;
  margin: 12px 0 0;
  padding: 0;
  list-style: none;
  font-size: 0.8rem;
}

.source-card {
  display: flex;
  align-items: center;
  gap: 9px;
  min-width: 0;
  padding: 7px 9px;
  color: inherit;
  text-decoration: none;
  border: 1px solid var(--panel-border);
  border-radius: 7px;
  background: color-mix(in srgb, var(--panel-bg) 84%, var(--surface-soft) 16%);
  transition: border-color 0.15s ease, background 0.15s ease;
}

.source-card:hover {
  border-color: color-mix(in srgb, var(--accent) 48%, var(--panel-border) 52%);
  background: color-mix(in srgb, var(--accent) 7%, var(--panel-bg) 93%);
}

.source-card-index {
  flex: 0 0 auto;
  display: grid;
  place-items: center;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  color: var(--accent);
  background: color-mix(in srgb, var(--accent) 12%, transparent);
  font-size: 0.7rem;
  font-variant-numeric: tabular-nums;
}

.source-card-body {
  display: grid;
  min-width: 0;
  gap: 2px;
}

.source-card-title,
.source-card-site {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.source-card-title {
  color: var(--accent);
}

.source-card-site {
  color: var(--muted);
  font-size: 0.72rem;
}

@media (max-width: 720px) {
  .third-party-head,
  .third-party-actions,
  .service-row,
  .service-row-meta,
  .capability-row {
    align-items: stretch;
    flex-direction: column;
  }

  .third-party-head-actions {
    align-items: flex-start;
    flex-direction: column;
  }

  .api-key-row {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>

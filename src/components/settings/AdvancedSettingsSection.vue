<script setup lang="ts">
import { ref } from 'vue';
import { useI18n } from 'vue-i18n';
import { checkUpdate, updateSystemConfig } from '../../api';
import { autoCheckUpdate, hasUpdate, latestVersion, releaseUrl, appVersion } from '../../appUiState';
import ToggleSwitch from '../ui/ToggleSwitch.vue';
import SettingsBreadcrumb from './SettingsBreadcrumb.vue';
import type { SettingsBreadcrumbItem } from './types';
import type { UpdateCheckResult } from '../../api';

defineProps<{
  breadcrumbItems: SettingsBreadcrumbItem[];
}>();

const emit = defineEmits<{
  navigateBreadcrumb: [key: string];
}>();

const { t } = useI18n();
const checking = ref(false);
const lastCheckResult = ref<UpdateCheckResult | null>(null);
const toggling = ref(false);

async function handleToggleAutoCheck(): Promise<void> {
  if (toggling.value) return;
  toggling.value = true;
  try {
    const result = await updateSystemConfig({ auto_check_update: !autoCheckUpdate.value });
    autoCheckUpdate.value = result.auto_check_update;
  } catch (error) {
    console.error('Failed to update auto_check_update:', error);
  } finally {
    toggling.value = false;
  }
}

async function handleManualCheck(): Promise<void> {
  if (checking.value) return;
  checking.value = true;
  try {
    const result = await checkUpdate(true);
    lastCheckResult.value = result;
    hasUpdate.value = result.has_update;
    latestVersion.value = result.latest_version;
    releaseUrl.value = result.release_url;
  } catch (error) {
    console.error('Failed to check for updates:', error);
  } finally {
    checking.value = false;
  }
}
</script>

<template>
  <section id="advanced" class="config-section advanced-section">
    <SettingsBreadcrumb :items="breadcrumbItems" @navigate="emit('navigateBreadcrumb', $event)" />

    <section class="advanced-panel panel">

      <div class="advanced-body">
        <!-- Combined Check Update row -->
        <div class="advanced-row">
          <div class="advanced-row-info">
            <span class="advanced-row-label">{{ t('settings.advanced.autoCheckUpdate') }}</span>
            <span class="advanced-row-note">
              {{ t('settings.advanced.currentVersion') }}: v{{ appVersion }}
              <template v-if="hasUpdate">
                · {{ t('settings.advanced.updateAvailable', { version: latestVersion }) }}
                <a
                  v-if="releaseUrl"
                  :href="releaseUrl"
                  target="_blank"
                  rel="noopener"
                  class="update-link"
                >{{ t('settings.advanced.goToDownload') }}</a>
              </template>
              <template v-else-if="lastCheckResult">
                · {{ t('settings.advanced.upToDate') }}
              </template>
            </span>
          </div>
          <div class="advanced-row-actions">
            <button
              type="button"
              class="secondary-button btn-sm"
              :disabled="checking"
              @click="handleManualCheck"
            >
              {{ checking ? t('settings.advanced.checking') : t('settings.advanced.checkNow') }}
            </button>
            <ToggleSwitch
              variant="inline"
              :checked="autoCheckUpdate"
              :disabled="toggling"
              @toggle="handleToggleAutoCheck"
            />
          </div>
        </div>
      </div>
    </section>
  </section>
</template>

<style scoped>
.advanced-section {
  padding: 12px 0 0;
}

.advanced-panel {
  border: 1px solid var(--panel-border);
  border-radius: 14px;
  background: var(--surface-soft);
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}


.advanced-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.advanced-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.advanced-row-info {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.advanced-row-label {
  font-size: 0.82rem;
  font-weight: 600;
  color: var(--text-strong);
}

.advanced-row-note {
  font-size: 0.72rem;
  color: var(--text-secondary);
  line-height: 1.4;
}

.advanced-row-actions {
  display: flex;
  align-items: center;
  gap: 16px;
}

.btn-sm {
  height: 26px;
  min-width: 60px;
  padding: 0 10px;
  font-size: 0.72rem;
}

.update-link {
  color: var(--accent);
  text-decoration: none;
  font-weight: 600;
}

.update-link:hover {
  text-decoration: underline;
}
</style>

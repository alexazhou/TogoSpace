<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import { RouterView, useRoute, useRouter } from 'vue-router';
import {
  clearGlobalRequestError,
  connectionState,
  setGlobalRequestErrorAutoDismiss,
  globalRequestErrors,
  globalSuccessToasts,
  type GlobalRequestErrorToast,
  reconnectProgress,
  scheduleNotRunningReason,
  scheduleState,
  showGlobalSuccessToast,
  showQuickInit,
  showTokenDialog,
  authEnabled,
  appVersion,
  autoCheckUpdate,
  hasUpdate,
  latestVersion,
  releaseUrl,
  totalMessageCount,
  updateScheduleState,
} from './appUiState';
import { checkUpdate, getSystemStatus, resumeSchedule, setTeamEnabled } from './api';
import { getToken } from './authStore';
import QuickInitModal from './components/layout/QuickInitModal.vue';
import TokenDialog from './components/ui/TokenDialog.vue';
import TopBar from './components/layout/TopBar.vue';
import { DEFAULT_SETTINGS_SECTION } from './components/settings/sections';
import ConfirmDialog from './components/ui/ConfirmDialog.vue';
import { startRealtimeClient, stopRealtimeClient } from './realtime/wsClient';
import { installViewportRootClasses } from './responsive/breakpoints';
import { findTeamById, firstTeamId, loadTeams, preferredTeamId, setPreferredTeamId, teams, teamsLoaded } from './teamStore';
import { formatConnectionState } from './utils';

type ThemeMode = 'dark' | 'light';
type ConsoleMainView = 'chat' | 'tasks';

const route = useRoute();
const router = useRouter();
const { t } = useI18n();

const themeMode = ref<ThemeMode>((localStorage.getItem('theme-mode') as ThemeMode) || 'dark');

const teamIdFromRoute = computed<number | null>(() => {
  const raw = route.params.teamId;
  if (typeof raw !== 'string') {
    return null;
  }

  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
});

const currentTeam = computed(() => findTeamById(teamIdFromRoute.value));
const activeTeamId = computed(() => currentTeam.value?.id ?? preferredTeamId.value ?? firstTeamId.value);
const activeTeam = computed(() => findTeamById(activeTeamId.value));
const activeTeamEnabled = computed(() => {
  const enabled = activeTeam.value?.enabled as boolean | number | undefined;
  return enabled !== false && enabled !== 0;
});
const activeTeamEnabledPending = ref<Record<number, boolean>>({});
const isActiveTeamTogglePending = computed(() => (
  activeTeamId.value !== null ? Boolean(activeTeamEnabledPending.value[activeTeamId.value]) : false
));
const teamToggleConfirm = ref<{
  open: boolean;
  teamId: number | null;
  enabled: boolean;
}>({
  open: false,
  teamId: null,
  enabled: false,
});
const showTeamDisabledPill = computed(() => route.name === 'console' && !activeTeamEnabled.value);
const showTopbarConnectionStatus = computed(() => route.name === 'console');
const showTopbarBackToConsole = computed(() => route.name === 'settings');
const statusLabel = computed(() => formatConnectionState(connectionState.value));
const isLightMode = computed(() => themeMode.value === 'light');
const scheduleResumePending = ref(false);
const isConsoleRoute = computed(() => route.name === 'console');
const isSettingsRoute = computed(() => route.name === 'settings');
const consoleView = computed<ConsoleMainView>(() => (route.query.view === 'tasks' ? 'tasks' : 'chat'));
let removeViewportRootClasses: (() => void) | null = null;

// ── V13: Quick Init Modal ──

function getRequestErrorCountdownStyle(toast: GlobalRequestErrorToast): Record<string, string> | undefined {
  if (toast.dismissAt === null || toast.autoDismissMs === null) {
    return undefined;
  }

  return {
    '--countdown-duration': `${toast.autoDismissMs}ms`,
    '--countdown-delay': `${toast.countdownDelayMs}ms`,
  };
}

function formatRequestErrorDetail(toast: GlobalRequestErrorToast): string {
  const parts: string[] = [];
  if (toast.statusCode !== null) {
    parts.push(`${t('error.statusLabel')} ${toast.statusCode}`);
  }
  if (toast.detail) {
    parts.push(toast.detail);
  }
  return parts.join(' · ');
}

async function checkSystemStatus(): Promise<void> {
  try {
    const status = await getSystemStatus();
    showQuickInit.value = !status.initialized;
    authEnabled.value = status.auth_enabled ?? false;
    updateScheduleState(status.schedule_state ?? '', status.not_running_reason ?? '');
    setGlobalRequestErrorAutoDismiss(status.development_mode ? null : 5000);
    appVersion.value = status.version ?? '';
    autoCheckUpdate.value = status.auto_check_update ?? true;

    // 鉴权启用且无 token 时，触发 token 输入
    if (authEnabled.value && !getToken()) {
      showTokenDialog.value = true;
    }
  } catch {
    // Backend unreachable — don't show init modal
    showQuickInit.value = false;
    authEnabled.value = false;
    updateScheduleState('', '');
  }
}

function handleInitSkip(): void {
  showQuickInit.value = false;
}

function handleInitDone(): void {
  showQuickInit.value = false;
  window.dispatchEvent(new CustomEvent('settings-reload'));
}

function applyTheme(mode: ThemeMode): void {
  document.documentElement.dataset.theme = mode;
}

function toggleTheme(): void {
  themeMode.value = themeMode.value === 'dark' ? 'light' : 'dark';
}

function openSettings(): void {
  if (activeTeamId.value === null) {
    return;
  }
  router.push({
    name: 'settings',
    params: { teamId: activeTeamId.value, section: DEFAULT_SETTINGS_SECTION },
  }).catch(console.error);
}

function backToConsole(): void {
  if (activeTeamId.value === null) {
    router.push({ name: 'home' }).catch(console.error);
    return;
  }

  router.push({ name: 'console', params: { teamId: activeTeamId.value } }).catch(console.error);
}

function selectTeam(teamId: number): void {
  setPreferredTeamId(teamId);
  router.push({ name: 'console', params: { teamId } }).catch(console.error);
}

function switchConsoleView(view: ConsoleMainView): void {
  if (!isConsoleRoute.value) {
    return;
  }

  router.replace({
    query: {
      ...route.query,
      view: view === 'chat' ? undefined : view,
    },
  }).catch(console.error);
}

function requestActiveTeamEnabledToggle(enabled: boolean): void {
  if (!activeTeam.value) {
    return;
  }

  teamToggleConfirm.value = {
    open: true,
    teamId: activeTeam.value.id,
    enabled,
  };
}

function closeTeamToggleConfirm(): void {
  teamToggleConfirm.value = {
    open: false,
    teamId: null,
    enabled: false,
  };
}

async function updateTeamEnabledState(teamIdToUpdate: number, enabled: boolean): Promise<void> {
  if (activeTeamEnabledPending.value[teamIdToUpdate]) {
    return;
  }

  activeTeamEnabledPending.value = {
    ...activeTeamEnabledPending.value,
    [teamIdToUpdate]: true,
  };

  try {
    await setTeamEnabled(teamIdToUpdate, enabled);
    await loadTeams();
    showGlobalSuccessToast(enabled ? t('settings.page.teamEnabled') : t('settings.page.teamDisabled'));
  } catch (error) {
    console.error(error);
  } finally {
    const nextPending = { ...activeTeamEnabledPending.value };
    delete nextPending[teamIdToUpdate];
    activeTeamEnabledPending.value = nextPending;
  }
}

function confirmTeamToggle(): void {
  const { teamId: targetTeamId, enabled } = teamToggleConfirm.value;
  closeTeamToggleConfirm();
  if (targetTeamId === null) {
    return;
  }
  void updateTeamEnabledState(targetTeamId, enabled);
}

async function handleResumeSchedule(): Promise<void> {
  if (scheduleResumePending.value) {
    return;
  }

  scheduleResumePending.value = true;
  try {
    const result = await resumeSchedule();
    updateScheduleState(result.schedule_state ?? '', result.not_running_reason ?? '');
    if (String(result.schedule_state ?? '').trim().toLowerCase() === 'running') {
      showGlobalSuccessToast(t('topbar.resumeScheduleSuccess'));
    }
  } catch (error) {
    console.error(error);
  } finally {
    scheduleResumePending.value = false;
  }
}

function redirectToTeam(teamId: number | null): void {
  if (teamId === null) {
    return;
  }
  router.replace({ name: 'console', params: { teamId } }).catch(console.error);
}

watch(currentTeam, (team) => {
  if (team) {
    setPreferredTeamId(team.id);
  }
});

watch(
  [teamsLoaded, teamIdFromRoute, () => route.name],
  ([loaded, routeTeamId, routeName]) => {
    if (!loaded) {
      return;
    }

    if (routeName === 'home') {
      redirectToTeam(preferredTeamId.value ?? firstTeamId.value);
      return;
    }

    if (routeTeamId === null || !findTeamById(routeTeamId)) {
      redirectToTeam(preferredTeamId.value ?? firstTeamId.value);
    }
  },
  { immediate: true },
);

watch(
  themeMode,
  (mode) => {
    localStorage.setItem('theme-mode', mode);
    applyTheme(mode);
  },
  { immediate: true },
);

onMounted(async () => {
  removeViewportRootClasses = installViewportRootClasses();
  applyTheme(themeMode.value);
  startRealtimeClient();
  await Promise.all([loadTeams(), checkSystemStatus()]);

  // Auto-check for updates on startup if enabled
  if (autoCheckUpdate.value) {
    checkUpdate()
      .then((result) => {
        hasUpdate.value = result.has_update;
        latestVersion.value = result.latest_version;
        releaseUrl.value = result.release_url;
      })
      .catch(() => {
        // silently ignore
      });
  }
});

onBeforeUnmount(() => {
  removeViewportRootClasses?.();
  removeViewportRootClasses = null;
  stopRealtimeClient();
});
</script>

<template>
  <div class="shell" :class="{ 'shell-console': isConsoleRoute, 'shell-settings': isSettingsRoute }">
    <div class="ambient ambient-left"></div>
    <div class="ambient ambient-right"></div>

    <TopBar
      :connection-state="connectionState"
      :is-light-mode="isLightMode"
      :status-label="statusLabel"
      :reconnect-progress="reconnectProgress"
      :total-message-count="totalMessageCount"
      :teams="teams"
      :active-team-id="activeTeamId"
      :active-team-enabled="activeTeamEnabled"
      :active-team-enabled-pending="isActiveTeamTogglePending"
      :show-team-disabled-pill="showTeamDisabledPill"
      :show-connection-status="showTopbarConnectionStatus"
      :show-back-to-console="showTopbarBackToConsole"
      :schedule-state="scheduleState"
      :schedule-not-running-reason="scheduleNotRunningReason"
      :schedule-resume-pending="scheduleResumePending"
      :auth-enabled="authEnabled"
      :show-console-view-tabs="isConsoleRoute"
      :console-view="consoleView"
      :has-update="hasUpdate"
      :latest-version="latestVersion"
      :release-url="releaseUrl"
      @toggle-theme="toggleTheme"
      @select-team="selectTeam"
      @toggle-active-team-enabled="requestActiveTeamEnabledToggle"
      @back-to-console="backToConsole"
      @open-settings="openSettings"
      @resume-schedule="handleResumeSchedule"
      @switch-console-view="switchConsoleView"
    />

    <Teleport to="body">
      <div class="global-error-toast-layer">
        <div
          v-for="toast in globalRequestErrors"
          :key="toast.id"
          class="global-error-toast"
          role="alert"
        >
          <div class="global-error-toast__header">
            <strong class="global-error-toast__title">{{ toast.title }}</strong>
            <div class="global-error-toast__actions">
              <button
                type="button"
                class="global-error-toast__close"
                :aria-label="t('common.closeAlert')"
                @click="clearGlobalRequestError(toast.id)"
              >
                <svg
                  v-if="toast.dismissAt !== null"
                  viewBox="0 0 36 36"
                  class="global-error-toast__countdown-svg"
                  :style="getRequestErrorCountdownStyle(toast)"
                  aria-hidden="true"
                >
                  <circle class="global-error-toast__countdown-track" cx="18" cy="18" r="15"></circle>
                  <circle class="global-error-toast__countdown-progress" cx="18" cy="18" r="15"></circle>
                </svg>
                <span class="global-error-toast__close-icon">×</span>
              </button>
            </div>
          </div>

          <div class="global-error-toast__line">
            <span class="global-error-toast__line-label">{{ t('error.pathLabel') }}</span>
            <code class="global-error-toast__path">{{ toast.path }}</code>
          </div>

          <div class="global-error-toast__line">
            <span class="global-error-toast__line-label">{{ t('error.detailLabel') }}</span>
            <p class="global-error-toast__detail">{{ formatRequestErrorDetail(toast) || t('error.noDetail') }}</p>
          </div>
        </div>
      </div>
    </Teleport>

    <Teleport to="body">
      <div class="global-success-toast-layer">
        <div
          v-for="toast in globalSuccessToasts"
          :key="toast.id"
          class="global-success-toast"
          role="status"
          aria-live="polite"
        >
          <span>{{ toast.message }}</span>
        </div>
      </div>
    </Teleport>

    <main class="workspace" :class="{ 'workspace-console': isConsoleRoute, 'workspace-settings': isSettingsRoute }">
      <RouterView />
    </main>

    <QuickInitModal
      v-if="showQuickInit"
      @skip="handleInitSkip"
      @done="handleInitDone"
    />

    <TokenDialog
      v-if="showTokenDialog"
    />

    <ConfirmDialog
      :open="teamToggleConfirm.open"
      :title="teamToggleConfirm.enabled ? t('settings.page.toggleEnableTitle') : t('settings.page.toggleDisableTitle')"
      :message="teamToggleConfirm.enabled ? t('settings.page.toggleEnableMsg') : t('settings.page.toggleDisableMsg')"
      :confirm-label="teamToggleConfirm.enabled ? t('settings.page.toggleEnableBtn') : t('settings.page.toggleDisableBtn')"
      @close="closeTeamToggleConfirm"
      @confirm="confirmTeamToggle"
    />
  </div>
</template>

<style scoped>
.shell {
  position: relative;
  height: 100%;
  min-height: 100%;
  padding: 8px 10px;
  overflow: hidden;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  gap: 8px;
  box-sizing: border-box;
}

.shell-settings {
  overflow-x: auto;
}

:global(html.bp-console-mobile) .shell-settings {
  overflow-x: auto;
}

.ambient {
  display: none;
}

.ambient-left {
  top: -8rem;
  left: -8rem;
  background: var(--shell-glow-left);
}

.ambient-right {
  right: -8rem;
  bottom: -10rem;
  background: var(--shell-glow-right);
}

.workspace {
  min-height: 0;
  height: 100%;
  overflow: hidden;
  position: relative;
  z-index: 1;
  display: grid;
  grid-template-rows: minmax(0, 1fr);
}

.workspace-settings {
  overflow-x: auto;
}

:global(html.bp-console-mobile) .shell {
  height: auto;
  min-height: 100%;
  padding: 8px 8px calc(12px + env(safe-area-inset-bottom, 0px));
  overflow-x: hidden;
  overflow-y: auto;
}

:global(html.bp-console-mobile) .workspace {
  height: auto;
  overflow: visible;
}

:global(html.bp-console-mobile) .shell.shell-console {
  height: 100%;
  min-height: 0;
  padding: 12px 0 0;
  gap: 6px;
  overflow: hidden;
}

:global(html.bp-console-mobile) .workspace.workspace-console {
  height: 100%;
  min-height: 0;
  overflow: hidden;
}

:global(html.bp-console-short) .shell.shell-console {
  padding: 8px 0 0;
  gap: 6px;
}

.global-error-toast-layer {
  position: fixed;
  top: 8px;
  right: 8px;
  z-index: 140;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 12px;
  pointer-events: none;
}

.global-error-toast {
  display: grid;
  gap: 10px;
  width: min(392px, calc(100vw - 16px));
  padding: 14px 15px;
  border: 1px solid color-mix(in srgb, var(--state-danger) 38%, var(--border-default) 62%);
  border-radius: 22px;
  background:
    linear-gradient(180deg, color-mix(in srgb, var(--state-danger) 10%, var(--surface-overlay) 90%) 0%, color-mix(in srgb, var(--surface-panel-deep) 82%, var(--state-danger) 18%) 100%);
  color: color-mix(in srgb, var(--state-danger) 72%, var(--text-primary) 28%);
  box-shadow: 0 18px 44px rgba(0, 0, 0, 0.24);
  backdrop-filter: blur(16px);
  pointer-events: auto;
}

.global-error-toast__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
}

.global-error-toast__title {
  min-width: 0;
  margin: 0;
  color: color-mix(in srgb, var(--state-danger) 92%, var(--text-primary) 8%);
  font-size: 1.08rem;
  font-weight: 700;
  line-height: 1.15;
}

.global-error-toast__actions {
  display: flex;
  align-items: center;
  gap: 0;
  flex-shrink: 0;
}

.global-error-toast__close {
  position: relative;
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  border: none;
  border-radius: 999px;
  background: color-mix(in srgb, var(--surface-panel) 82%, transparent);
  color: inherit;
  cursor: pointer;
  padding: 0;
  line-height: 1;
  font-size: 1.08rem;
  transition: transform 0.15s ease, background 0.15s ease;
  isolation: isolate;
  box-shadow:
    inset 0 0 0 1px color-mix(in srgb, var(--border-default) 72%, transparent),
    0 0 0 1px color-mix(in srgb, var(--state-danger) 10%, transparent);
}

.global-error-toast__countdown-svg {
  position: absolute;
  inset: 0;
  display: block;
  width: 34px;
  height: 34px;
  transform: rotate(-90deg);
  overflow: visible;
  pointer-events: none;
  z-index: 0;
}

.global-error-toast__countdown-track,
.global-error-toast__countdown-progress {
  fill: none;
  stroke-width: 4;
}

.global-error-toast__countdown-track {
  stroke: color-mix(in srgb, var(--state-danger) 14%, var(--border-default) 86%);
}

.global-error-toast__countdown-progress {
  stroke: color-mix(in srgb, var(--state-danger) 82%, white 18%);
  stroke-linecap: round;
  stroke-dasharray: 94.2478;
  stroke-dashoffset: 94.2478;
  animation-name: error-toast-countdown;
  animation-duration: var(--countdown-duration, 5000ms);
  animation-timing-function: linear;
  animation-delay: var(--countdown-delay, 0ms);
  animation-fill-mode: forwards;
  will-change: stroke-dashoffset;
}

.global-error-toast__close:hover {
  transform: translateY(-1px);
  background: color-mix(in srgb, var(--state-danger) 10%, var(--surface-panel) 90%);
}

.global-error-toast__close-icon {
  position: relative;
  z-index: 1;
  display: block;
  transform: translateY(-0.5px);
}

.global-error-toast__line-label {
  display: block;
  margin-bottom: 2px;
  color: color-mix(in srgb, var(--text-secondary) 70%, var(--state-danger) 30%);
  font-size: 0.69rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.global-error-toast__line {
  display: grid;
  gap: 3px;
}

.global-error-toast__path {
  display: block;
  color: color-mix(in srgb, var(--state-info) 68%, var(--text-primary) 32%);
  font-family: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace;
  font-size: 0.78rem;
  line-height: 1.42;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  word-break: break-word;
}

.global-error-toast__detail {
  margin: 0;
  color: color-mix(in srgb, var(--state-danger) 88%, var(--text-primary) 12%);
  font-size: 0.92rem;
  line-height: 1.42;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  word-break: break-word;
}

.global-success-toast {
  width: min(360px, calc(100vw - 40px));
  min-height: 44px;
  padding: 10px 18px;
  border: 1px solid color-mix(in srgb, var(--state-success) 42%, var(--border-default) 58%);
  border-radius: 14px;
  background: color-mix(in srgb, var(--state-success) 18%, var(--surface-panel) 82%);
  color: color-mix(in srgb, var(--state-success) 78%, var(--text-primary) 22%);
  box-shadow: 0 16px 36px rgba(0, 0, 0, 0.16);
  font-size: 0.92rem;
  line-height: 1.3;
  text-align: center;
  pointer-events: none;
  animation: success-toast-drop 0.22s ease-out;
}

.global-success-toast-layer {
  position: fixed;
  top: 18px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 140;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  pointer-events: none;
}

.global-success-toast span {
  flex: 1;
}

@keyframes success-toast-drop {
  from {
    opacity: 0;
    transform: translateY(-12px);
  }

  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes error-toast-countdown {
  from {
    stroke-dashoffset: 94.2478;
  }

  to {
    stroke-dashoffset: 0;
  }
}
</style>

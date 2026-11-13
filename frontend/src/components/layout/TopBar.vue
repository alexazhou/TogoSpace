<script setup lang="ts">
import { useI18n } from 'vue-i18n';
import TopBarStatusGroup from './TopBarStatusGroup.vue';
import TopBarTeamSwitcher from './TopBarTeamSwitcher.vue';
import TopBarUtilityGroup from './TopBarUtilityGroup.vue';
import type { TeamSummary } from '../../types';
import type { ConnectionState } from '../../utils';

/**
 * 顶栏外壳（P4 拆分后）：只保留三块骨架职责——
 * 1. `.topbar` 容器与「返回控制台 / 打开设置」按钮；
 * 2. 控制台视图切换页签（chat / tasks）；
 * 3. 组合三个职责子组件：团队选择、连接状态、工具与更新。
 *
 * 拆分只搬移模板/样式/局部状态，props 与 emit 契约、DOM 层级与顺序、class 名一律不变，
 * 因此调用方（`App.vue`）与所有既有样式钩子都不受影响。
 */
const { t } = useI18n();
type ConsoleMainView = 'chat' | 'tasks';

const props = defineProps<{
  connectionState: ConnectionState;
  isLightMode: boolean;
  statusLabel: string;
  reconnectProgress: number;
  activeAgentCount: number;
  activeAgentNames: string[];
  teams: TeamSummary[];
  activeTeamId: number | null;
  activeTeamEnabled: boolean;
  activeTeamEnabledPending: boolean;
  showTeamDisabledPill?: boolean;
  showConnectionStatus?: boolean;
  scheduleState?: string;
  scheduleNotRunningReason?: string;
  scheduleResumePending?: boolean;
  authEnabled?: boolean;
  showBackToConsole?: boolean;
  showConsoleViewTabs?: boolean;
  consoleView?: ConsoleMainView;
  hasUpdate?: boolean;
  latestVersion?: string;
  releaseUrl?: string;
}>();

const emit = defineEmits<{
  toggleTheme: [];
  selectTeam: [teamId: number];
  toggleActiveTeamEnabled: [enabled: boolean];
  openSettings: [];
  backToConsole: [];
  resumeSchedule: [];
  switchConsoleView: [view: ConsoleMainView];
}>();
</script>

<template>
  <header class="topbar" :class="{ 'topbar-console': showConnectionStatus }">
    <div class="brand-group">
      <button
        class="nav-icon-button nav-icon-button--bare"
        type="button"
        :disabled="activeTeamId === null"
        :title="showBackToConsole ? t('settings.back') : t('topbar.settings')"
        :aria-label="showBackToConsole ? t('settings.back') : t('topbar.settings')"
        @click="showBackToConsole ? emit('backToConsole') : emit('openSettings')"
      >
        <i
          :class="showBackToConsole ? 'fa-solid fa-chevron-left' : 'fa-solid fa-gear'"
          aria-hidden="true"
        ></i>
      </button>
      <TopBarTeamSwitcher
        :teams="teams"
        :active-team-id="activeTeamId"
        :active-team-enabled="activeTeamEnabled"
        :active-team-enabled-pending="activeTeamEnabledPending"
        @select-team="emit('selectTeam', $event)"
        @toggle-active-team-enabled="emit('toggleActiveTeamEnabled', $event)"
      />
    </div>

    <div v-if="showConsoleViewTabs" class="topbar-center">
      <div
        class="console-view-switcher"
        role="tablist"
        :aria-label="t('console.viewTabs')"
      >
        <button
          type="button"
          class="console-view-switcher__tab"
          :class="{ active: consoleView === 'chat' }"
          :aria-selected="consoleView === 'chat'"
          @click="emit('switchConsoleView', 'chat')"
        >
          {{ t('console.viewChat') }}
        </button>
        <button
          type="button"
          class="console-view-switcher__tab"
          :class="{ active: consoleView === 'tasks' }"
          :aria-selected="consoleView === 'tasks'"
          @click="emit('switchConsoleView', 'tasks')"
        >
          {{ t('console.viewTasks') }}
        </button>
      </div>
    </div>

    <div class="status-group">
      <TopBarStatusGroup
        :connection-state="connectionState"
        :status-label="statusLabel"
        :reconnect-progress="reconnectProgress"
        :active-agent-count="activeAgentCount"
        :active-agent-names="activeAgentNames"
        :active-team-enabled="activeTeamEnabled"
        :show-team-disabled-pill="showTeamDisabledPill"
        :show-connection-status="showConnectionStatus"
        :schedule-state="scheduleState"
        :schedule-not-running-reason="scheduleNotRunningReason"
        :schedule-resume-pending="scheduleResumePending"
        @resume-schedule="emit('resumeSchedule')"
      />
      <TopBarUtilityGroup
        :is-light-mode="isLightMode"
        :auth-enabled="authEnabled"
        :has-update="hasUpdate"
        :latest-version="latestVersion"
        :release-url="releaseUrl"
        @toggle-theme="emit('toggleTheme')"
      />
    </div>
  </header>
</template>

<style scoped>
.topbar {
  position: relative;
  z-index: 8;
  isolation: isolate;
  display: flex;
  justify-content: space-between;
  gap: 10px;
  align-items: center;
  min-height: 0;
  background: color-mix(in srgb, var(--surface-panel) 84%, var(--surface-pill) 16%);
  border: 1px solid var(--panel-border-soft);
  border-radius: 10px;
  padding: 4px 10px 4px 5px;
  overflow: visible;
}

.brand-group {
  display: flex;
  align-items: center;
  gap: 5px;
  flex-wrap: wrap;
}

.topbar-center {
  position: absolute;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: none;
}

.console-view-switcher {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 1px;
  border: 1px solid var(--border-subtle);
  border-radius: 9px;
  background: var(--surface-pill);
  pointer-events: auto;
}

.console-view-switcher__tab {
  min-width: 68px;
  height: 22px;
  padding: 0 11px;
  border: 1px solid transparent;
  border-radius: 7px;
  background: transparent;
  color: var(--text-secondary);
  font-size: 0.76rem;
  font-weight: 700;
  cursor: pointer;
  transition:
    border-color 140ms ease,
    background 140ms ease,
    color 140ms ease;
}

.console-view-switcher__tab:hover {
  color: var(--text-primary);
  background: color-mix(in srgb, var(--interactive-selected) 34%, transparent);
}

.console-view-switcher__tab.active {
  border-color: color-mix(in srgb, var(--interactive-focus-border) 44%, transparent);
  background: color-mix(in srgb, var(--interactive-selected) 72%, var(--surface-panel) 28%);
  color: var(--text-primary);
}

.nav-action {
  appearance: none;
  -webkit-appearance: none;
  height: 28px;
  border: 1px solid var(--border-subtle);
  border-radius: 8px;
  background: var(--surface-pill);
  color: var(--text-primary);
  padding: 0 12px;
  cursor: pointer;
  outline: none;
  box-shadow: none;
  transition:
    border-color 140ms ease,
    background 140ms ease,
    color 140ms ease;
}

.nav-icon-button {
  appearance: none;
  -webkit-appearance: none;
  width: 24px;
  height: 24px;
  padding: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
  cursor: pointer;
  outline: none;
  box-shadow: none;
  transition:
    color 140ms ease,
    background 140ms ease,
    box-shadow 140ms ease;
}

.nav-action:disabled,
.nav-icon-button:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.nav-action:not(:disabled):hover {
  border-color: var(--interactive-focus-border);
  color: var(--text-primary);
}

.nav-icon-button--bare {
  border: none;
  background: transparent;
  color: var(--text-secondary);
}

.nav-icon-button--bare:not(:disabled):hover {
  background: color-mix(in srgb, var(--interactive-selected) 44%, transparent);
  color: var(--text-primary);
}

.status-group {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.nav-icon-button i {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  line-height: 1;
}

:global(html.bp-layout-narrow) .topbar {
  align-items: flex-start;
  flex-direction: column;
}

:global(html.bp-layout-narrow) .brand-group {
  width: 100%;
}

:global(html.bp-layout-narrow) .topbar-center {
  position: static;
  transform: none;
  width: 100%;
  justify-content: flex-start;
  pointer-events: auto;
}

:global(html.bp-layout-narrow) .status-group {
  width: 100%;
  justify-content: flex-start;
}

:global(html.bp-console-mobile) .topbar {
  gap: 6px;
  padding: 6px;
  border-radius: 14px;
}

:global(html.bp-console-mobile) .topbar.topbar-console {
  gap: 6px;
}

:global(html.bp-console-mobile) .topbar.topbar-console .brand-group {
  width: 100%;
  display: flex;
  flex-wrap: nowrap;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

:global(html.bp-console-mobile) .topbar.topbar-console .nav-icon-button--bare {
  width: 32px;
  height: 32px;
  border-radius: 10px;
  flex: 0 0 auto;
}

:global(html.bp-console-mobile) .topbar.topbar-console .topbar-center {
  position: static;
  transform: none;
  width: 100%;
  justify-content: center;
  pointer-events: auto;
}

:global(html.bp-console-mobile) .topbar.topbar-console .console-view-switcher {
  flex: 0 0 auto;
}

:global(html.bp-console-mobile) .topbar.topbar-console .status-group {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: nowrap;
  overflow-x: auto;
  overflow-y: hidden;
  padding-bottom: 2px;
  scrollbar-width: none;
}

:global(html.bp-console-mobile) .topbar.topbar-console .status-group::-webkit-scrollbar {
  display: none;
}

:global(html.bp-console-short) .topbar.topbar-console {
  gap: 4px;
  padding: 4px;
  border-radius: 12px;
}

:global(html.bp-console-short) .topbar.topbar-console .brand-group {
  gap: 4px;
}

:global(html.bp-console-short) .topbar.topbar-console .console-view-switcher {
  padding: 1px;
  border-radius: 8px;
}

:global(html.bp-console-short) .topbar.topbar-console .console-view-switcher__tab {
  min-width: 56px;
  height: 20px;
  padding: 0 9px;
  font-size: 0.72rem;
}

:global(html.bp-console-short) .topbar.topbar-console .status-group {
  gap: 4px;
  padding-bottom: 0;
}
</style>

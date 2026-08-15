<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue';
import { useI18n } from 'vue-i18n';
import i18n from '../../i18n';
import { setLanguage } from '../../api';
import { clearToken } from '../../authStore';
import { showTokenDialog } from '../../appUiState';
import { clearTeams } from '../../teamStore';
import { clearRuntimeStore } from '../../realtime/runtimeStore';
import LabeledSwitch from '../ui/LabeledSwitch.vue';
import ConfirmDialog from '../ui/ConfirmDialog.vue';
import type { TeamSummary } from '../../types';
import type { ConnectionState } from '../../utils';
import { displayName } from '../../utils';
import type { AppLocale } from '../../i18n';

const { t } = useI18n();
type ConsoleMainView = 'chat' | 'tasks';

const props = defineProps<{
  connectionState: ConnectionState;
  isLightMode: boolean;
  statusLabel: string;
  reconnectProgress: number;
  totalMessageCount: number;
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

const teamMenuOpen = ref(false);
const languageMenuOpen = ref(false);
const currentLocale = computed<AppLocale>(() => i18n.global.locale.value);
const logoutConfirmOpen = ref(false);

const activeTeamName = computed(() => {
  const team = props.teams.find((team) => team.id === props.activeTeamId);
  return team ? displayName(team) : t('topbar.selectTeam');
});
const enabledTeams = computed(() => props.teams
  .filter((team) => team.enabled)
  .slice()
  .sort((left, right) => left.id - right.id));
const disabledTeams = computed(() => props.teams
  .filter((team) => !team.enabled)
  .slice()
  .sort((left, right) => left.id - right.id));
const activeTeamToggleLabel = computed(() => {
  if (props.activeTeamEnabledPending) {
    return t('topbar.teamSwitching');
  }
  return props.activeTeamEnabled ? t('settings.teams.enabled') : t('settings.teams.disabled');
});
const activeTeamToggleAriaLabel = computed(() => (
  `${t('topbar.teamToggleLabel')}：${activeTeamToggleLabel.value}`
));
const scheduleLabel = computed(() => {
  switch (props.scheduleState) {
    case 'blocked': return t('topbar.scheduleStopped');
    case 'stopped': return t('topbar.scheduleStopped');
    default: return '';
  }
});
const scheduleTooltip = computed(() => {
  if (!props.scheduleState || props.scheduleState === 'running') {
    return '';
  }
  return props.scheduleNotRunningReason || (props.scheduleState === 'blocked' ? t('topbar.scheduleBlockedReason') : t('topbar.scheduleStoppedReason'));
});

function selectTeam(teamId: number): void {
  teamMenuOpen.value = false;
  emit('selectTeam', teamId);
}

function toggleTeamMenu(): void {
  if (!props.teams.length) {
    return;
  }
  teamMenuOpen.value = !teamMenuOpen.value;
}

function handleWindowPointerDown(event: PointerEvent): void {
  const target = event.target;
  if (!(target instanceof Element)) {
    return;
  }

  if (!target.closest('.team-switcher')) {
    teamMenuOpen.value = false;
  }
  if (!target.closest('.language-switch')) {
    languageMenuOpen.value = false;
  }
}

function handleWindowKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') {
    teamMenuOpen.value = false;
    languageMenuOpen.value = false;
  }
}

onBeforeUnmount(() => {
  window.removeEventListener('pointerdown', handleWindowPointerDown);
  window.removeEventListener('keydown', handleWindowKeydown);
});

if (typeof window !== 'undefined') {
  window.addEventListener('pointerdown', handleWindowPointerDown);
  window.addEventListener('keydown', handleWindowKeydown);
}

function handleTeamButtonKeydown(event: KeyboardEvent): void {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault();
    toggleTeamMenu();
    return;
  }

  if (event.key === 'ArrowDown') {
    event.preventDefault();
    teamMenuOpen.value = true;
  }
}

function handleTeamOptionKeydown(event: KeyboardEvent, teamId: number): void {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault();
    selectTeam(teamId);
  }
}

function isActiveTeam(teamId: number): boolean {
  return props.activeTeamId === teamId;
}

function optionTabIndex(teamId: number): number {
  return isActiveTeam(teamId) ? 0 : -1;
}

function toggleLanguageMenu(): void {
  languageMenuOpen.value = !languageMenuOpen.value;
}

async function handleSetLanguage(lang: AppLocale): Promise<void> {
  try {
    await setLanguage(lang);
    i18n.global.locale.value = lang;
    languageMenuOpen.value = false;
  } catch (error) {
    console.error('Failed to set language:', error);
  }
}

function listboxId(): string {
  return 'topbar-team-switcher-listbox';
}

function buttonLabelId(): string {
  return 'topbar-team-switcher-label';
}

function optionId(teamId: number): string {
  return `topbar-team-option-${teamId}`;
}

function activeOptionId(): string | undefined {
  return props.activeTeamId !== null ? optionId(props.activeTeamId) : undefined;
}

function optionLabel(team: TeamSummary): string {
  return `${displayName(team)} #${team.id}`;
}

function handleLogout(): void {
  logoutConfirmOpen.value = true;
}

function confirmLogout(): void {
  logoutConfirmOpen.value = false;
  clearRuntimeStore();
  clearTeams();
  clearToken();
  showTokenDialog.value = true;
}

function closeLogoutConfirm(): void {
  logoutConfirmOpen.value = false;
}

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
      <div class="team-switcher">
        <button
          :id="buttonLabelId()"
          type="button"
          class="team-switcher-button"
          :aria-expanded="teamMenuOpen"
          :aria-controls="listboxId()"
          aria-haspopup="listbox"
          @click="toggleTeamMenu"
          @keydown="handleTeamButtonKeydown"
        >
          <span class="team-switcher-button__label">{{ activeTeamName }}</span>
          <svg class="team-switcher-button__icon" viewBox="0 0 16 16" aria-hidden="true">
            <path d="m4 6 4 4 4-4" />
          </svg>
        </button>

        <div
          v-if="teamMenuOpen"
          :id="listboxId()"
          class="team-switcher-menu"
          role="listbox"
          :aria-labelledby="buttonLabelId()"
          :aria-activedescendant="activeOptionId()"
        >
          <section v-if="enabledTeams.length" class="team-switcher-group">
            <div class="team-switcher-group__head">
              <span class="team-switcher-group__title">{{ t('topbar.teamGroupEnabled') }}</span>
              <span class="team-switcher-group__count">{{ t('topbar.teamsCount', { count: enabledTeams.length }) }}</span>
            </div>
            <button
              v-for="team in enabledTeams"
              :id="optionId(team.id)"
              :key="team.id"
              type="button"
              class="team-switcher-option"
              :class="{ 'is-active': isActiveTeam(team.id) }"
              role="option"
              :aria-selected="isActiveTeam(team.id)"
              :aria-label="optionLabel(team)"
              :tabindex="optionTabIndex(team.id)"
              @click="selectTeam(team.id)"
              @keydown="handleTeamOptionKeydown($event, team.id)"
            >
              <span class="team-switcher-option__name">{{ displayName(team) }}</span>
              <span class="team-switcher-option__meta">#{{ team.id }}</span>
            </button>
          </section>

          <section v-if="disabledTeams.length" class="team-switcher-group">
            <div class="team-switcher-group__head">
              <span class="team-switcher-group__title">{{ t('topbar.teamGroupDisabled') }}</span>
              <span class="team-switcher-group__count">{{ t('topbar.teamsCount', { count: disabledTeams.length }) }}</span>
            </div>
            <button
              v-for="team in disabledTeams"
              :id="optionId(team.id)"
              :key="team.id"
              type="button"
              class="team-switcher-option team-switcher-option--disabled"
              :class="{ 'is-active': isActiveTeam(team.id) }"
              role="option"
              :aria-selected="isActiveTeam(team.id)"
              :aria-label="optionLabel(team)"
              :tabindex="optionTabIndex(team.id)"
              @click="selectTeam(team.id)"
              @keydown="handleTeamOptionKeydown($event, team.id)"
            >
              <span class="team-switcher-option__name">{{ displayName(team) }}</span>
              <span class="team-switcher-option__meta">#{{ team.id }}</span>
            </button>
          </section>
        </div>
      </div>
      <LabeledSwitch
        class="topbar-team-enabled-switch"
        :disabled="activeTeamId === null || activeTeamEnabledPending"
        :checked="activeTeamEnabled"
        :aria-label="activeTeamToggleAriaLabel"
        :title="activeTeamToggleAriaLabel"
        :label="activeTeamToggleLabel"
        @toggle="emit('toggleActiveTeamEnabled', $event)"
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
      <div v-if="showTeamDisabledPill && !activeTeamEnabled" class="team-disabled-pill">{{ t('topbar.teamDisabled') }}</div>
      <div
        v-if="scheduleLabel"
        class="schedule-state-pill-wrapper"
      >
        <div
          class="schedule-state-pill"
          :data-state="scheduleState"
        >
          <span class="schedule-dot"></span>
          {{ scheduleLabel }}
        </div>
        <div v-if="scheduleTooltip" class="schedule-tooltip" role="dialog" aria-live="polite">
          <p class="schedule-tooltip__label">{{ t('topbar.scheduleReasonLabel') }}</p>
          <p class="schedule-tooltip__reason">{{ scheduleTooltip }}</p>
          <button
            type="button"
            class="schedule-tooltip__action"
            :disabled="scheduleResumePending"
            @click="emit('resumeSchedule')"
          >
            {{ scheduleResumePending ? t('topbar.resumeSchedulePending') : t('topbar.resumeSchedule') }}
          </button>
        </div>
      </div>
      <div v-if="showConnectionStatus" class="status-pill" :data-state="connectionState">
        <span
          v-if="connectionState === 'waiting_reconnect'"
          class="reconnect-indicator"
          :style="{ '--reconnect-progress': reconnectProgress.toString() }"
          aria-hidden="true"
        >
          <svg viewBox="0 0 16 16" class="reconnect-ring">
            <circle class="reconnect-ring-track" cx="8" cy="8" r="5.5" />
            <circle class="reconnect-ring-progress" cx="8" cy="8" r="5.5" />
          </svg>
        </span>
        <span
          v-else
          class="status-dot"
          :class="{ 'status-dot-pulse': connectionState === 'reconnecting' }"
        ></span>
        {{ statusLabel }}
      </div>
      <div v-if="showConnectionStatus" class="metric-pill">{{ t('topbar.messagesCount', { count: totalMessageCount }) }}</div>
      <div class="topbar-utility-group">
        <button
          class="theme-switch"
          type="button"
          :aria-pressed="isLightMode"
          :title="isLightMode ? t('topbar.switchToDark') : t('topbar.switchToLight')"
          @click="emit('toggleTheme')"
        >
          <span
            class="theme-switch-icon theme-switch-icon-sun"
            :class="{ 'is-active': isLightMode }"
            aria-hidden="true"
          >
            <svg viewBox="0 0 24 24">
              <circle cx="12" cy="12" r="4"></circle>
              <path d="M12 2.75v2.5"></path>
              <path d="M12 18.75v2.5"></path>
              <path d="M4.93 4.93l1.77 1.77"></path>
              <path d="M17.3 17.3l1.77 1.77"></path>
              <path d="M2.75 12h2.5"></path>
              <path d="M18.75 12h2.5"></path>
              <path d="M4.93 19.07l1.77-1.77"></path>
              <path d="M17.3 6.7l1.77-1.77"></path>
            </svg>
          </span>
          <span class="theme-switch-track">
            <span class="theme-switch-thumb" :class="{ 'is-dark': !isLightMode }"></span>
          </span>
          <span
            class="theme-switch-icon theme-switch-icon-moon"
            :class="{ 'is-active': !isLightMode }"
            aria-hidden="true"
          >
            <svg viewBox="0 0 24 24">
              <path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a7.8 7.8 0 1 0 10.5 10.5Z"></path>
            </svg>
          </span>
        </button>
        <div class="language-switch">
          <button
            type="button"
            class="lang-button"
            :aria-expanded="languageMenuOpen"
            aria-haspopup="listbox"
            :aria-label="t('language.switcher')"
            :title="t('language.switcher')"
            @click="toggleLanguageMenu"
          >
            <svg class="lang-button__globe" viewBox="0 0 24 24" aria-hidden="true">
              <path d="M12 3a9 9 0 1 0 0 18a9 9 0 0 0 0-18Z" />
              <path d="M3.6 9h16.8" />
              <path d="M3.6 15h16.8" />
              <path d="M12 3c2.3 2.2 3.6 5.3 3.6 9s-1.3 6.8-3.6 9c-2.3-2.2-3.6-5.3-3.6-9s1.3-6.8 3.6-9Z" />
            </svg>
            <svg class="lang-button__chevron" viewBox="0 0 16 16" aria-hidden="true">
              <path d="m4 6 4 4 4-4" />
            </svg>
          </button>
          <div
            v-if="languageMenuOpen"
            class="lang-menu"
            role="listbox"
          >
            <button
              type="button"
              class="lang-option"
              :class="{ 'is-active': currentLocale === 'zh-CN' }"
              role="option"
              :aria-selected="currentLocale === 'zh-CN'"
              @click="handleSetLanguage('zh-CN')"
            >
              <span class="lang-option__check" aria-hidden="true">{{ currentLocale === 'zh-CN' ? '✓' : '' }}</span>
              {{ t('language.zhCN') }}
            </button>
            <button
              type="button"
              class="lang-option"
              :class="{ 'is-active': currentLocale === 'en' }"
              role="option"
              :aria-selected="currentLocale === 'en'"
              @click="handleSetLanguage('en')"
            >
              <span class="lang-option__check" aria-hidden="true">{{ currentLocale === 'en' ? '✓' : '' }}</span>
              {{ t('language.en') }}
            </button>
          </div>
        </div>
        <button
          v-if="authEnabled"
          class="nav-icon-button toolbar-icon-button logout-button"
          type="button"
          :title="t('topbar.logout')"
          :aria-label="t('topbar.logout')"
          @click="handleLogout"
        >
          <svg class="logout-button__icon" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M10 4.5H7.25A2.25 2.25 0 0 0 5 6.75v10.5a2.25 2.25 0 0 0 2.25 2.25H10" />
            <path d="M13 8.5 17 12l-4 3.5" />
            <path d="M9 12h8" />
          </svg>
        </button>
      </div>
      <a
        v-if="hasUpdate"
        :href="releaseUrl || 'https://github.com/alexazhou/TogoSpace/releases'"
        target="_blank"
        rel="noopener"
        class="update-pill"
      >
        <span class="update-pill__dot"></span>
        {{ t('topbar.updateAvailable', { version: latestVersion }) }}
      </a>
    </div>
  </header>

  <ConfirmDialog
    :open="logoutConfirmOpen"
    :title="t('topbar.logoutConfirmTitle')"
    :message="t('topbar.logoutConfirmMsg')"
    :confirm-label="t('topbar.logout')"
    @close="closeLogoutConfirm"
    @confirm="confirmLogout"
  />
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

.team-switcher {
  display: flex;
  align-items: center;
  position: relative;
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

.team-switcher-button {
  display: inline-flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  min-width: 180px;
  height: 28px;
  border: 1px solid var(--border-subtle);
  border-radius: 8px;
  background: var(--surface-pill);
  color: var(--text-primary);
  padding: 0 10px;
  outline: none;
  box-shadow: none;
  cursor: pointer;
  transition:
    border-color 140ms ease,
    background 140ms ease,
    color 140ms ease,
    box-shadow 140ms ease;
}

.team-switcher-button:hover {
  border-color: var(--interactive-focus-border);
  background: color-mix(in srgb, var(--interactive-selected) 40%, var(--surface-pill) 60%);
  color: var(--text-primary);
}

.team-switcher-button__label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.team-switcher-button__icon {
  width: 12px;
  height: 12px;
  flex: 0 0 auto;
  fill: none;
  stroke: var(--text-secondary);
  stroke-width: 1.8;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.team-switcher-menu {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  z-index: 24;
  min-width: 220px;
  max-height: 240px;
  overflow: auto;
  padding: 6px;
  display: grid;
  gap: 4px;
  border: 1px solid var(--border-default);
  border-radius: 10px;
  background: var(--surface-overlay);
  box-shadow: 0 10px 24px rgba(0, 0, 0, 0.14);
}

.team-switcher-group {
  display: grid;
  gap: 4px;
}

.team-switcher-group + .team-switcher-group {
  padding-top: 4px;
  border-top: 1px solid color-mix(in srgb, var(--room-card-border) 82%, transparent 18%);
}

.team-switcher-group__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 2px 4px;
}

.team-switcher-group__title {
  color: var(--text-secondary);
  font-size: 0.64rem;
  font-weight: 700;
  letter-spacing: 0.08em;
}

.team-switcher-group__count {
  color: var(--text-tertiary);
  font-size: 0.64rem;
}

.team-switcher-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  width: 100%;
  min-height: 30px;
  padding: 0 10px;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  color: var(--text-primary);
  cursor: pointer;
  text-align: left;
}

.team-switcher-option--disabled {
  color: color-mix(in srgb, var(--text-secondary) 82%, var(--text-primary) 18%);
}

.team-switcher-option:hover,
.team-switcher-option:focus-visible {
  border-color: color-mix(in srgb, var(--interactive-focus-border) 42%, transparent);
  background: color-mix(in srgb, var(--interactive-selected) 56%, var(--surface-panel) 44%);
  outline: none;
}

.team-switcher-option.is-active {
  border-color: color-mix(in srgb, var(--interactive-focus-border) 56%, var(--border-subtle) 44%);
  background: color-mix(in srgb, var(--interactive-selected) 72%, var(--surface-panel) 28%);
}

.team-switcher-option__name {
  min-width: 0;
  flex: 1 1 auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.team-switcher-option__meta {
  min-width: 42px;
  text-align: right;
  color: var(--text-tertiary);
  font-size: 0.68rem;
  flex: 0 0 auto;
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

.toolbar-icon-button {
  width: 28px;
  height: 28px;
  border: 1px solid var(--border-subtle);
  background: var(--surface-pill);
  color: var(--text-secondary);
}

.toolbar-icon-button:not(:disabled):hover {
  border-color: var(--interactive-focus-border);
  background: color-mix(in srgb, var(--interactive-selected) 28%, var(--surface-pill) 72%);
  color: var(--text-primary);
}

.team-switcher-button:focus-visible,
.nav-action:focus-visible,
.nav-icon-button:focus-visible,
.theme-switch:focus-visible {
  border-color: var(--interactive-focus-border);
  box-shadow: 0 0 0 2px var(--interactive-focus-ring);
}

.status-group {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.topbar-utility-group {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.team-disabled-pill,
.status-pill,
.metric-pill,
.schedule-state-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid var(--border-subtle);
  border-radius: 8px;
  padding: 3px 8px;
  background: var(--surface-pill);
  color: var(--text-secondary);
  font-size: 0.78rem;
  transition:
    border-color 140ms ease,
    background 140ms ease,
    color 140ms ease;
}

.team-disabled-pill:hover,
.status-pill:hover,
.metric-pill:hover,
.schedule-state-pill:hover {
  border-color: var(--interactive-focus-border);
  background: color-mix(in srgb, var(--interactive-selected) 18%, var(--surface-pill) 82%);
}

.team-disabled-pill {
  border-color: color-mix(in srgb, var(--state-warning) 28%, var(--border-subtle) 72%);
  background: color-mix(in srgb, var(--state-warning) 16%, var(--surface-pill) 84%);
}

.update-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid color-mix(in srgb, var(--state-info) 35%, var(--border-subtle) 65%);
  border-radius: 8px;
  padding: 3px 10px;
  background: color-mix(in srgb, var(--state-info) 14%, var(--surface-pill) 86%);
  color: color-mix(in srgb, var(--state-info) 85%, var(--text-primary) 15%);
  font-size: 0.72rem;
  font-weight: 600;
  text-decoration: none;
  white-space: nowrap;
  cursor: pointer;
  transition:
    border-color 140ms ease,
    background 140ms ease,
    color 140ms ease;
}

.update-pill:hover {
  border-color: color-mix(in srgb, var(--state-info) 55%, var(--interactive-focus-border) 45%);
  background: color-mix(in srgb, var(--state-info) 22%, var(--surface-pill) 78%);
  color: var(--text-primary);
}

.update-pill__dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--state-info);
}

.schedule-state-pill-wrapper {
  position: relative;
  display: inline-flex;
  align-items: center;
}

.schedule-state-pill {
  border-color: color-mix(in srgb, var(--state-warning) 28%, var(--border-subtle) 72%);
  background: color-mix(in srgb, var(--state-warning) 12%, var(--surface-pill) 88%);
  color: color-mix(in srgb, var(--state-warning) 82%, var(--text-primary) 18%);
}

.schedule-state-pill[data-state='stopped'] {
  border-color: color-mix(in srgb, var(--state-danger) 28%, var(--border-subtle) 72%);
  background: color-mix(in srgb, var(--state-danger) 12%, var(--surface-pill) 88%);
  color: color-mix(in srgb, var(--state-danger) 82%, var(--text-primary) 18%);
}

.schedule-tooltip {
  position: absolute;
  top: calc(100% + 6px);
  left: 50%;
  transform: translateX(-50%) scale(0.95);
  min-width: 220px;
  max-width: 280px;
  padding: 10px;
  border-radius: 10px;
  background: var(--surface-overlay);
  border: 1px solid var(--border-subtle);
  color: var(--text-primary);
  font-size: 0.75rem;
  white-space: normal;
  opacity: 0;
  visibility: hidden;
  transition: opacity 0.15s ease, transform 0.15s ease, visibility 0.15s ease;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
  z-index: 100;
  display: grid;
  gap: 8px;
}

.schedule-state-pill-wrapper:hover .schedule-tooltip {
  opacity: 1;
  visibility: visible;
  transform: translateX(-50%) scale(1);
}

.schedule-state-pill-wrapper:focus-within .schedule-tooltip {
  opacity: 1;
  visibility: visible;
  transform: translateX(-50%) scale(1);
}

.schedule-tooltip__label,
.schedule-tooltip__reason {
  margin: 0;
}

.schedule-tooltip__label {
  color: var(--text-tertiary);
  font-size: 0.64rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.schedule-tooltip__action {
  justify-self: start;
  min-height: 28px;
  border: 1px solid var(--interactive-focus-border);
  border-radius: 8px;
  background: color-mix(in srgb, var(--interactive-selected) 58%, var(--surface-pill) 42%);
  color: var(--text-primary);
  padding: 0 10px;
  font: inherit;
  cursor: pointer;
  transition:
    border-color 140ms ease,
    background 140ms ease,
    opacity 140ms ease;
}

.schedule-tooltip__action:hover:not(:disabled) {
  background: color-mix(in srgb, var(--interactive-selected) 72%, var(--surface-pill) 28%);
}

.schedule-tooltip__action:disabled {
  cursor: wait;
  opacity: 0.72;
}

.schedule-dot {
  width: 7px;
  height: 7px;
  border-radius: 999px;
  background: var(--state-warning);
}

.schedule-state-pill[data-state='stopped'] .schedule-dot {
  background: var(--state-danger);
}

.status-pill[data-state='connected'] {
  color: var(--state-success);
}

.status-pill[data-state='connected'] .status-dot {
  background: var(--state-success);
  box-shadow: none;
}

.status-pill[data-state='waiting_reconnect'] .status-dot,
.status-pill[data-state='reconnecting'] .status-dot,
.status-pill[data-state='connecting'] .status-dot {
  background: var(--state-warning);
  box-shadow: none;
}

.status-pill[data-state='waiting_reconnect'],
.status-pill[data-state='reconnecting'] {
  color: var(--state-warning);
}

.status-pill[data-state='disconnected'] .status-dot {
  background: var(--state-danger);
}

.status-dot {
  width: 7px;
  height: 7px;
  border-radius: 999px;
  background: var(--status-dot-idle);
}

.status-dot-pulse {
  width: 6px;
  height: 6px;
  background: var(--state-warning);
  animation: reconnect-dot-pulse 2s ease-in-out infinite;
}

.theme-switch {
  appearance: none;
  -webkit-appearance: none;
  display: inline-flex;
  align-items: center;
  gap: 2px;
  height: 28px;
  padding: 0 5px;
  border: 1px solid var(--border-subtle);
  border-radius: 999px;
  background: var(--surface-pill);
  color: var(--text-secondary);
  cursor: pointer;
  outline: none;
  box-shadow: none;
  transition:
    border-color 140ms ease,
    background 140ms ease,
    color 140ms ease;
}

.theme-switch:hover {
  border-color: var(--interactive-focus-border);
  color: var(--text-primary);
}

.theme-switch svg {
  width: 12px;
  height: 12px;
  fill: none;
  stroke: currentColor;
  stroke-width: 2;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.nav-icon-button i {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  line-height: 1;
}

.logout-button__icon {
  width: 16px;
  height: 16px;
  fill: none;
  stroke: currentColor;
  stroke-width: 2.1;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.theme-switch-icon {
  color: var(--text-secondary);
  transition: color 160ms ease;
}

.theme-switch-icon.is-active {
  color: var(--theme-switch-icon-active);
}

.theme-switch-track {
  position: relative;
  width: 28px;
  height: 18px;
  border-radius: 999px;
  background: var(--toolbar-switch-off);
}

.theme-switch-thumb {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 14px;
  height: 14px;
  border-radius: 999px;
  background: var(--toolbar-switch-handle);
  transition: transform 180ms ease;
}

.theme-switch-thumb.is-dark {
  transform: translateX(10px);
}

.reconnect-indicator {
  display: inline-flex;
}

.reconnect-ring {
  width: 14px;
  height: 14px;
  transform: rotate(-90deg);
}

.reconnect-ring-track,
.reconnect-ring-progress {
  fill: none;
  stroke-width: 2;
}

.reconnect-ring-track {
  stroke: var(--state-warning-track);
}

.reconnect-ring-progress {
  stroke: var(--state-warning);
  stroke-dasharray: 34.56;
  stroke-dashoffset: calc(34.56 * (1 - var(--reconnect-progress)));
}

@keyframes reconnect-dot-pulse {
  0%,
  100% {
    transform: scale(0.85);
    opacity: 0.55;
  }

  50% {
    transform: scale(1.35);
    opacity: 1;
  }
}

.language-switch {
  position: relative;
  display: flex;
  align-items: center;
}

.lang-button {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  height: 28px;
  padding: 0 5px 0 6px;
  border: 1px solid var(--border-subtle);
  border-radius: 8px;
  background: var(--surface-pill);
  color: var(--text-primary);
  cursor: pointer;
  outline: none;
  transition: border-color 140ms ease, background 140ms ease;
}

.lang-button:hover {
  border-color: var(--interactive-focus-border);
}

.lang-button:focus-visible {
  border-color: var(--interactive-focus-border);
  box-shadow: 0 0 0 2px var(--interactive-focus-ring);
}

.lang-button__globe,
.lang-button__chevron {
  fill: none;
  stroke: var(--text-secondary);
  stroke-width: 1.7;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.lang-button__globe {
  width: 16px;
  height: 16px;
}

.lang-button__chevron {
  width: 10px;
  height: 10px;
}

.lang-menu {
  position: absolute;
  top: calc(100% + 6px);
  right: 0;
  z-index: 24;
  min-width: 100px;
  padding: 4px;
  display: grid;
  gap: 2px;
  border: 1px solid var(--border-default);
  border-radius: 8px;
  background: var(--surface-overlay);
  box-shadow: 0 10px 24px rgba(0, 0, 0, 0.14);
}

.lang-option {
  display: grid;
  grid-template-columns: 14px 1fr;
  align-items: center;
  gap: 8px;
  width: 100%;
  min-height: 28px;
  padding: 0 10px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: var(--text-primary);
  font-size: 0.72rem;
  text-align: left;
  cursor: pointer;
  outline: none;
}

.lang-option__check {
  color: var(--text-secondary);
  font-size: 0.76rem;
  font-weight: 700;
  text-align: center;
}

.lang-option:hover,
.lang-option:focus-visible {
  border-color: color-mix(in srgb, var(--interactive-focus-border) 42%, transparent);
  background: color-mix(in srgb, var(--interactive-selected) 56%, var(--surface-panel) 44%);
}

.lang-option.is-active {
  border-color: color-mix(in srgb, var(--interactive-focus-border) 56%, var(--border-subtle) 44%);
  background: color-mix(in srgb, var(--interactive-selected) 72%, var(--surface-panel) 28%);
  font-weight: 600;
}

.logout-button {
  flex: 0 0 auto;
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

:global(html.bp-console-mobile) .topbar.topbar-console .team-switcher {
  flex: 1 1 auto;
  min-width: 0;
}

:global(html.bp-console-mobile) .topbar.topbar-console .team-switcher-button {
  width: 100%;
  min-width: 0;
  height: 32px;
  padding: 0 10px;
}

:global(html.bp-console-mobile) .topbar.topbar-console .topbar-team-enabled-switch {
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

:global(html.bp-console-mobile) .topbar.topbar-console .metric-pill {
  display: none;
}

:global(html.bp-console-mobile) .topbar.topbar-console .team-disabled-pill,
:global(html.bp-console-mobile) .topbar.topbar-console .status-pill,
:global(html.bp-console-mobile) .topbar.topbar-console .schedule-state-pill {
  min-height: 32px;
  padding: 0 10px;
  white-space: nowrap;
  flex: 0 0 auto;
}

:global(html.bp-console-mobile) .topbar.topbar-console .topbar-utility-group {
  margin-left: auto;
  gap: 4px;
  flex: 0 0 auto;
}

:global(html.bp-console-mobile) .topbar.topbar-console .theme-switch,
:global(html.bp-console-mobile) .topbar.topbar-console .lang-button,
:global(html.bp-console-mobile) .topbar.topbar-console .toolbar-icon-button {
  height: 32px;
}

:global(html.bp-console-mobile) .topbar.topbar-console .toolbar-icon-button {
  width: 32px;
}

:global(html.bp-console-short) .topbar.topbar-console {
  gap: 4px;
  padding: 4px;
  border-radius: 12px;
}

:global(html.bp-console-short) .topbar.topbar-console .brand-group {
  gap: 4px;
}

:global(html.bp-console-short) .topbar.topbar-console .nav-icon-button--bare,
:global(html.bp-console-short) .topbar.topbar-console .team-switcher-button,
:global(html.bp-console-short) .topbar.topbar-console .theme-switch,
:global(html.bp-console-short) .topbar.topbar-console .lang-button,
:global(html.bp-console-short) .topbar.topbar-console .toolbar-icon-button {
  height: 28px;
}

:global(html.bp-console-short) .topbar.topbar-console .nav-icon-button--bare,
:global(html.bp-console-short) .topbar.topbar-console .toolbar-icon-button {
  width: 28px;
}

:global(html.bp-console-short) .topbar.topbar-console .team-switcher-button {
  padding: 0 8px;
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

:global(html.bp-console-short) .topbar.topbar-console .team-disabled-pill,
:global(html.bp-console-short) .topbar.topbar-console .status-pill,
:global(html.bp-console-short) .topbar.topbar-console .schedule-state-pill {
  min-height: 28px;
  padding: 0 8px;
  font-size: 0.72rem;
}

:global(html.bp-console-short) .topbar.topbar-console .status-group {
  gap: 4px;
  padding-bottom: 0;
}
</style>

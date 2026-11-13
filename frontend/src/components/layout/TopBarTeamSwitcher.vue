<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue';
import { useI18n } from 'vue-i18n';
import LabeledSwitch from '../ui/LabeledSwitch.vue';
import type { TeamSummary } from '../../types';
import { displayName } from '../../utils';

/**
 * 顶栏「团队选择」区块：团队下拉（含启用/停用分组、ARIA listbox 键盘交互）+ 团队启用开关。
 *
 * 从 `TopBar.vue` 拆出（P4）：模板、状态与样式整体搬移，对外只通过 props/emit 交互，
 * 父组件不感知下拉开合等内部状态。
 */
const { t } = useI18n();

const props = defineProps<{
  teams: TeamSummary[];
  activeTeamId: number | null;
  activeTeamEnabled: boolean;
  activeTeamEnabledPending: boolean;
}>();

const emit = defineEmits<{
  selectTeam: [teamId: number];
  toggleActiveTeamEnabled: [enabled: boolean];
}>();

const teamMenuOpen = ref(false);

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
}

function handleWindowKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') {
    teamMenuOpen.value = false;
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
</script>

<template>
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
</template>

<style scoped>
.team-switcher {
  display: flex;
  align-items: center;
  position: relative;
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

:global(html.bp-console-short) .topbar.topbar-console .team-switcher-button {
  padding: 0 8px;
}
</style>

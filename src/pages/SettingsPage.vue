<script setup lang="ts">
import '../theme/legacy-aliases.css';
import { computed, onMounted, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import { useRoute, useRouter } from 'vue-router';
import { backupDatabase, getAgents, getDeptTree, getTeamPresetExport } from '../api';
import { showGlobalSuccessToast, showQuickInit, totalMessageCount, appVersion, autoCheckUpdate, hasUpdate, latestVersion } from '../appUiState';
import ModelsSettingsSection from '../components/settings/ModelsSettingsSection.vue';
import RolesSettingsSection from '../components/settings/RolesSettingsSection.vue';
import SkillsSettingsSection from '../components/settings/SkillsSettingsSection.vue';
import SettingsNavSidebar from '../components/settings/SettingsNavSidebar.vue';
import SystemMaintenanceSection from '../components/settings/SystemMaintenanceSection.vue';
import AdvancedSettingsSection from '../components/settings/AdvancedSettingsSection.vue';
import ThirdPartyServicesSection from '../components/settings/ThirdPartyServicesSection.vue';
import TeamCreateSection from '../components/settings/TeamCreateSection.vue';
import TeamPresetExportDialog from '../components/settings/TeamPresetExportDialog.vue';
import TeamsSettingsSection from '../components/settings/TeamsSettingsSection.vue';
import ClearDataChoiceDialog from '../components/settings/ClearDataChoiceDialog.vue';
import ConfirmDialog from '../components/ui/ConfirmDialog.vue';
import { useSettingsNavItems } from '../components/settings/settingsNavItems';
import { useSettingsRouting } from '../composables/useSettingsRouting';
import { useSettingsTeamDetailState } from '../composables/useSettingsTeamDetailState';
import { useSettingsTeamMutations } from '../composables/useSettingsTeamMutations';
import { useTeamSummaries } from '../composables/useTeamSummaries';
import { loadTeams, teams, teamsLoadFailed } from '../teamStore';
import type { AgentInfo, TeamDetail } from '../types';

const route = useRoute();
const router = useRouter();
const { t } = useI18n();

totalMessageCount.value = 0;

const teamId = computed(() => Number(route.params.teamId));
const agents = ref<AgentInfo[]>([]);
const deptTree = ref<any>(null);
const selectedTeamDetail = ref<TeamDetail | null>(null);
const settingsMainRef = ref<HTMLElement | null>(null);
const settingsScrollbarHovered = ref(false);
const isBackingUpDatabase = ref(false);
const isExportingTeamPreset = ref(false);
const isCreatingTeam = ref(false);
const backupConfirmOpen = ref(false);
const selectedExportTeamId = ref<number | null>(null);
const teamPresetExportDialogOpen = ref(false);

const navItems = useSettingsNavItems();
const { loadTeamSummaries, teamSummaries } = useTeamSummaries(teams);
const {
  breadcrumbItems,
  currentSectionId,
  detailTeamId,
  detailProviderIndex,
  detailThirdPartyService,
  goBack,
  handleBreadcrumbNavigate,
  openSection,
  openTeamDetail,
  clearTeamDetail,
  openProviderModels,
  clearProviderModels,
  openThirdPartyService,
  clearThirdPartyService,
  topbarBackLabel,
} = useSettingsRouting({
  route,
  router,
  teamId,
  navItems,
  selectedTeamDetail,
  openQuickInit: () => {
    showQuickInit.value = true;
  },
  t,
});
const {
  clearSelectedTeamDetail,
  handleTeamTreeSaved,
  hasTeamInfoChanges,
  isSavingTeamInfo,
  loadSelectedTeamDetail,
  resetTeamInfoDraft,
  saveTeamInfo,
  teamInfoDraft,
  teamInfoStatus,
} = useSettingsTeamDetailState({
  currentSectionId,
  detailTeamId,
  selectedTeamDetail,
  teams,
  loadTeams,
  loadTeamSummaries,
  t,
});

async function loadFullTeamDetail(targetTeamId: number | null): Promise<void> {
  await loadSelectedTeamDetail(targetTeamId);
  if (targetTeamId !== null) {
    try {
      deptTree.value = await getDeptTree(targetTeamId);
    } catch (error) {
      console.error(error);
      deptTree.value = null;
    }
  } else {
    deptTree.value = null;
  }
}

const {
  closeTeamClearDataConfirm,
  closeTeamDeleteConfirm,
  closeTeamToggleConfirm,
  confirmClearAgentData,
  confirmClearTeamData,
  confirmDeleteTeam,
  confirmTeamToggle,
  requestClearTeamData,
  requestDeleteSelectedTeam,
  requestTeamEnabledToggle,
  teamClearDataConfirm,
  teamDeleteConfirm,
  teamEnabledPending,
  teamToggleConfirm,
  updateTeamEnabledState,
} = useSettingsTeamMutations({
  teamId,
  teams,
  selectedTeamDetail,
  loadTeams,
  loadTeamSummaries,
  loadSelectedTeamDetail: loadFullTeamDetail,
  clearSelectedTeamDetail,
  router,
  t,
});

function openCreateTeam(): void {
  isCreatingTeam.value = true;
}

function handleTeamCreated(teamId: number): void {
  isCreatingTeam.value = false;
  void loadTeams();
  openTeamDetail(teamId);
}

function handleCreateTeamCancel(): void {
  isCreatingTeam.value = false;
}

const createTeamBreadcrumbItems = computed(() => [
  { key: 'settings', label: t('settings.title'), current: false },
  { key: 'teams', label: t('settings.nav.teams'), current: false },
  { key: 'create', label: t('settings.teams.newTeam'), current: true },
]);

function handleCreateTeamBreadcrumbNavigate(key: string): void {
  if (key === 'teams' || key === 'settings') {
    handleCreateTeamCancel();
    return;
  }
}

function handleDisableTeamForEdit(targetTeamId: number): void {
  void updateTeamEnabledState(targetTeamId, false);
}
function updateSettingsScrollbarHover(event: PointerEvent): void {
  const element = settingsMainRef.value;
  if (!element) {
    settingsScrollbarHovered.value = false;
    return;
  }

  const rect = element.getBoundingClientRect();
  const hoverInset = 18;
  const hoverVertical = element.scrollHeight > element.clientHeight
    && event.clientX >= rect.right - hoverInset
    && event.clientX <= rect.right;
  const hoverHorizontal = element.scrollWidth > element.clientWidth
    && event.clientY >= rect.bottom - hoverInset
    && event.clientY <= rect.bottom;

  settingsScrollbarHovered.value = hoverVertical || hoverHorizontal;
}

function clearSettingsScrollbarHover(): void {
  settingsScrollbarHovered.value = false;
}

function formatDateTime(value: string): string {
  if (!value) {
    return t('common.unknown');
  }

  const normalized = value.includes('T') ? value : value.replace(' ', 'T');
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) {
    return value.replace('T', ' ').split('.')[0];
  }

  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(date);
}

function requestBackupDatabase(): void {
  backupConfirmOpen.value = true;
}

function closeBackupConfirm(): void {
  backupConfirmOpen.value = false;
}

async function handleBackupDatabase(): Promise<void> {
  if (isBackingUpDatabase.value) {
    return;
  }

  backupConfirmOpen.value = false;
  isBackingUpDatabase.value = true;
  try {
    const result = await backupDatabase();
    showGlobalSuccessToast(t('settings.maintenance.backupSuccess', { file: result.backup_file_name }));
  } catch (error) {
    console.error(error);
  } finally {
    isBackingUpDatabase.value = false;
  }
}

function requestExportTeamPreset(): void {
  if (!teams.value.length) {
    return;
  }
  teamPresetExportDialogOpen.value = true;
}

function closeTeamPresetExportDialog(): void {
  teamPresetExportDialogOpen.value = false;
}

function buildTeamPresetFileName(teamName: string): string {
  const baseName = teamName.trim().replace(/[\\/:*?"<>|]/g, '_') || 'team_preset';
  return `${baseName}.json`;
}

function downloadJsonFile(fileName: string, payload: unknown): void {
  const blob = new Blob([`${JSON.stringify(payload, null, 2)}\n`], {
    type: 'application/json;charset=utf-8',
  });
  const objectUrl = window.URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = objectUrl;
  anchor.download = fileName;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.URL.revokeObjectURL(objectUrl);
}

async function handleExportTeamPreset(): Promise<void> {
  const targetTeamId = selectedExportTeamId.value;
  if (isExportingTeamPreset.value || targetTeamId === null) {
    return;
  }

  const targetTeam = teams.value.find((team) => team.id === targetTeamId);
  if (!targetTeam) {
    return;
  }

  isExportingTeamPreset.value = true;
  try {
    teamPresetExportDialogOpen.value = false;
    const teamPreset = await getTeamPresetExport(targetTeamId);
    const fileName = buildTeamPresetFileName(teamPreset.name || targetTeam.name);
    downloadJsonFile(fileName, teamPreset);
    showGlobalSuccessToast(t('settings.maintenance.exportSuccess', { file: fileName }));
  } catch (error) {
    console.error(error);
  } finally {
    isExportingTeamPreset.value = false;
  }
}

onMounted(() => {
  getAgents()
    .then((result) => {
      agents.value = result;
    })
    .catch((error) => {
      console.error(error);
    });
});

watch(
  [currentSectionId, detailTeamId],
  ([sectionId, targetTeamId]) => {
    if (sectionId === 'teams' && targetTeamId !== null) {
      getDeptTree(targetTeamId)
        .then((tree) => {
          deptTree.value = tree;
        })
        .catch((error) => {
          console.error(error);
          deptTree.value = null;
        });
    } else {
      deptTree.value = null;
    }
  },
  { immediate: true },
);

watch(
  () => teams.value.map((team) => team.id),
  (teamIds) => {
    if (!teamIds.length) {
      selectedExportTeamId.value = null;
      return;
    }
    if (selectedExportTeamId.value === null || !teamIds.includes(selectedExportTeamId.value)) {
      selectedExportTeamId.value = teamIds[0] ?? null;
    }
  },
  { immediate: true },
);
</script>

<template>
  <section class="settings-shell panel">
    <header class="settings-head">
      <div class="settings-head-main">
        <div class="settings-title-row">
          <h2>{{ t('settings.title') }}</h2>
          <div class="settings-eyebrow-group">
            <a
              v-if="appVersion && hasUpdate"
              :href="'https://github.com/alexazhou/TogoSpace/releases'"
              target="_blank"
              rel="noopener"
              class="settings-version-badge settings-version-badge--has-update"
              :data-tooltip="t('settings.advanced.updateAvailable', { version: latestVersion })"
            >v{{ appVersion }}*</a>
            <span v-else-if="appVersion" class="settings-version-badge">v{{ appVersion }}</span>
            <p class="settings-eyebrow">Admin Console</p>
          </div>
        </div>
      </div>
      <button type="button" class="secondary-button" @click="goBack">{{ topbarBackLabel }}</button>
    </header>

    <div class="settings-layout">
      <SettingsNavSidebar
        :items="navItems"
        :active-id="currentSectionId"
        @select="openSection"
      />

      <main
        ref="settingsMainRef"
        class="settings-main"
        :class="{ 'settings-main--scrollbar-hover': settingsScrollbarHovered }"
        @pointermove="updateSettingsScrollbarHover"
        @pointerleave="clearSettingsScrollbarHover"
      >
        <TeamCreateSection
          v-if="currentSectionId === 'teams' && isCreatingTeam"
          :breadcrumb-items="createTeamBreadcrumbItems"
          @navigate-breadcrumb="handleCreateTeamBreadcrumbNavigate"
          @created="handleTeamCreated"
          @cancel="handleCreateTeamCancel"
        />

        <TeamsSettingsSection
          v-else-if="currentSectionId === 'teams'"
          :breadcrumb-items="breadcrumbItems"
          :selected-team-detail="selectedTeamDetail"
          :team-info-draft="teamInfoDraft"
          :has-team-info-changes="hasTeamInfoChanges"
          :is-saving-team-info="isSavingTeamInfo"
          :team-info-status="teamInfoStatus"
          :team-summaries="teamSummaries"
          :team-enabled-pending="teamEnabledPending"
          :teams="teams"
          :team-list-load-failed="teamsLoadFailed"
          :format-date-time="formatDateTime"
          @navigate-breadcrumb="handleBreadcrumbNavigate"
          @create-team="openCreateTeam"
          @open-team-detail="openTeamDetail"
          @toggle-team-enabled="requestTeamEnabledToggle"
          @clear-team-detail="clearTeamDetail"
          @delete-team="requestDeleteSelectedTeam"
          @clear-team-data="requestClearTeamData"
          @save-team-info="saveTeamInfo"
          @reset-team-info-draft="resetTeamInfoDraft"
          @tree-saved="handleTeamTreeSaved"
          @disable-team="handleDisableTeamForEdit"
          @update:name="teamInfoDraft.name = $event"
          @update:working-directory="teamInfoDraft.workingDirectory = $event"
          @update:slogan="teamInfoDraft.slogan = $event"
          @update:rules="teamInfoDraft.rules = $event"
        />

        <RolesSettingsSection
          v-else-if="currentSectionId === 'roles'"
          :breadcrumb-items="breadcrumbItems"
          @navigate-breadcrumb="handleBreadcrumbNavigate"
        />

        <ModelsSettingsSection
          v-else-if="currentSectionId === 'models'"
          :breadcrumb-items="breadcrumbItems"
          :detail-provider-index="detailProviderIndex"
          @navigate-breadcrumb="handleBreadcrumbNavigate"
          @open-provider-models="openProviderModels"
          @clear-provider-models="clearProviderModels"
        />

        <ThirdPartyServicesSection
          v-else-if="currentSectionId === 'thirdPartyServices'"
          :breadcrumb-items="breadcrumbItems"
          :detail-service="detailThirdPartyService"
          @navigate-breadcrumb="handleBreadcrumbNavigate"
          @open-service="openThirdPartyService"
          @clear-service="clearThirdPartyService"
        />

        <SkillsSettingsSection
          v-else-if="currentSectionId === 'skills'"
          :breadcrumb-items="breadcrumbItems"
          @navigate-breadcrumb="handleBreadcrumbNavigate"
        />

        <SystemMaintenanceSection
          v-else-if="currentSectionId === 'maintenance'"
          :breadcrumb-items="breadcrumbItems"
          :teams="teams"
          :is-backing-up="isBackingUpDatabase"
          @navigate-breadcrumb="handleBreadcrumbNavigate"
          @backup-database="requestBackupDatabase"
          @export-team-preset="requestExportTeamPreset"
        />

        <AdvancedSettingsSection
          v-else-if="currentSectionId === 'advanced'"
          :breadcrumb-items="breadcrumbItems"
          @navigate-breadcrumb="handleBreadcrumbNavigate"
        />
      </main>
    </div>

    <ConfirmDialog
      :open="teamToggleConfirm.open"
      :title="teamToggleConfirm.enabled ? t('settings.page.toggleEnableTitle') : t('settings.page.toggleDisableTitle')"
      :message="teamToggleConfirm.enabled
        ? t('settings.page.toggleEnableMsg', { name: teamToggleConfirm.teamName })
        : t('settings.page.toggleDisableMsg', { name: teamToggleConfirm.teamName })"
      :confirm-label="teamToggleConfirm.enabled ? t('settings.page.toggleEnableBtn') : t('settings.page.toggleDisableBtn')"
      @close="closeTeamToggleConfirm"
      @confirm="confirmTeamToggle"
    />

    <ConfirmDialog
      :open="teamDeleteConfirm.open"
      :title="t('settings.page.deleteTitle')"
      :message="t('settings.page.deleteMsg')"
      :confirm-label="t('settings.page.deleteBtn')"
      danger
      @close="closeTeamDeleteConfirm"
      @confirm="confirmDeleteTeam"
    />

    <ConfirmDialog
      :open="backupConfirmOpen"
      :title="t('settings.maintenance.backupConfirmTitle')"
      :message="t('settings.maintenance.backupConfirmMsg')"
      :confirm-label="t('settings.maintenance.backupConfirmBtn')"
      @close="closeBackupConfirm"
      @confirm="handleBackupDatabase"
    />

    <TeamPresetExportDialog
      :open="teamPresetExportDialogOpen"
      :teams="teams"
      :selected-team-id="selectedExportTeamId"
      :is-exporting="isExportingTeamPreset"
      @close="closeTeamPresetExportDialog"
      @confirm="handleExportTeamPreset"
      @update-selected-team-id="selectedExportTeamId = $event"
    />

    <ClearDataChoiceDialog
      :open="teamClearDataConfirm.open"
      :team-name="teamClearDataConfirm.teamName"
      :members="selectedTeamDetail?.members || []"
      :dept-tree="deptTree"
      @close="closeTeamClearDataConfirm"
      @clear-team-data="confirmClearTeamData"
      @clear-agent-data="confirmClearAgentData"
    />
  </section>
</template>

<style scoped>
.settings-shell {
  height: 100%;
  min-height: 0;
  min-width: 680px;
  padding: 10px 12px;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  gap: 0;
}

.settings-head,
.section-head,
.sidebar-card-head,
.table-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.settings-head-main {
  display: grid;
  gap: 4px;
  min-width: 0;
}

.settings-head {
  position: relative;
  z-index: 2;
  background: var(--panel-bg);
  padding-bottom: 8px;
  border-bottom: 1px solid var(--divider);
}

.settings-breadcrumb {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}

.breadcrumb-link {
  position: relative;
  border: none;
  background: transparent;
  color: var(--hint-text);
  padding: 0;
  cursor: pointer;
  font-size: 0.72rem;
  line-height: 1.2;
}

.breadcrumb-link:not(:last-child)::after {
  content: '/';
  margin-left: 6px;
  color: var(--panel-border);
}

.breadcrumb-link.current {
  color: var(--text-strong);
  cursor: default;
}

.breadcrumb-link:hover:not(.current) {
  color: var(--accent);
}

.settings-title-row {
  display: flex;
  align-items: center;
  gap: 14px;
  min-width: 0;
}

.settings-eyebrow,
.section-eyebrow {
  margin: 0;
  color: var(--accent);
  text-transform: uppercase;
  letter-spacing: 0.14em;
  font-size: 0.68rem;
  flex: 0 0 auto;
}

.settings-eyebrow-group {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
}

.settings-version-badge {
  display: block;
  font-size: 0.62rem;
  font-weight: 600;
  color: var(--text-secondary);
  background: color-mix(in srgb, var(--text-secondary) 12%, transparent);
  border: 1px solid color-mix(in srgb, var(--text-secondary) 22%, var(--divider));
  border-radius: 6px;
  padding: 1px 6px;
  white-space: nowrap;
  user-select: none;
  letter-spacing: 0.02em;
  line-height: 1.3;
  text-decoration: none;
}

.settings-version-badge--has-update {
  color: var(--state-info);
  background: color-mix(in srgb, var(--state-info) 12%, transparent);
  border-color: color-mix(in srgb, var(--state-info) 25%, var(--divider));
  cursor: pointer;
}

.settings-version-badge--has-update:hover {
  background: color-mix(in srgb, var(--state-info) 20%, transparent);
  border-color: color-mix(in srgb, var(--state-info) 40%, var(--divider));
}

.settings-version-badge--has-update::after {
  content: attr(data-tooltip);
  position: absolute;
  top: calc(100% + 8px);
  left: 50%;
  transform: translateX(-50%);
  padding: 6px 10px;
  border-radius: 8px;
  background: var(--surface-overlay);
  border: 1px solid var(--border-subtle);
  color: var(--text-primary);
  font-size: 0.72rem;
  font-weight: 500;
  white-space: nowrap;
  pointer-events: none;
  opacity: 0;
  visibility: hidden;
  transition: opacity 0.15s ease, visibility 0.15s ease;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
  z-index: 100;
}

.settings-version-badge--has-update {
  position: relative;
}

.settings-version-badge--has-update:hover::after {
  opacity: 1;
  visibility: visible;
}

.settings-head h2,
.section-head h3 {
  margin: 0;
  color: var(--text-strong);
}

.settings-head h2 {
  flex: 0 1 auto;
  margin: 0;
  font-size: 1.72rem;
  line-height: 1.04;
}

.settings-layout {
  min-height: 0;
  display: grid;
  grid-template-columns: 260px minmax(0, 1fr);
  gap: 0;
}

.settings-sidebar,
.settings-main {
  min-height: 0;
}

.settings-sidebar {
  padding-top: 10px;
}

.sidebar-card,
.placeholder-card,
.status-card,
.metric-card,
.driver-card,
.team-card,
.empty-card,
.field-card,
.table-card {
  border: 1px solid var(--panel-border);
  border-radius: 14px;
  background: var(--surface-soft);
}

.sidebar-card {
  height: 100%;
  padding: 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.sidebar-card-head span,
.section-status,
.field-card span,
.placeholder-card span {
  color: var(--muted);
}

.settings-nav {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.nav-link {
  width: 100%;
  border: 1px solid var(--room-card-border);
  border-radius: 12px;
  background: var(--surface-soft);
  color: inherit;
  padding: 8px 10px;
  text-align: left;
  cursor: pointer;
  transition:
    border-color 140ms ease,
    background 140ms ease;
}

.nav-link strong {
  display: block;
  color: var(--text-strong);
  font-size: 0.82rem;
}

.nav-link span {
  display: block;
  margin-top: 2px;
  color: var(--muted);
  font-size: 0.7rem;
}

.nav-link:hover {
  border-color: var(--focus-border);
  background: var(--backend-selected-hover, color-mix(in srgb, var(--selected) 52%, var(--surface-soft) 48%));
}

.nav-link.active {
  border-color: var(--focus-border);
  background: var(--backend-selected-active, color-mix(in srgb, var(--selected) 60%, var(--surface-soft) 40%));
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--focus-border) 40%, transparent);
}

.settings-main {
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 10px 4px 0 18px;
  scrollbar-width: thin;
  scrollbar-color: color-mix(in srgb, var(--focus-border) 16%, var(--panel-border) 84%) transparent;
}

.settings-main::-webkit-scrollbar {
  width: 12px;
  height: 12px;
}

.settings-main::-webkit-scrollbar-track {
  background: transparent;
}

.settings-main::-webkit-scrollbar-thumb {
  border-radius: 999px;
  background: color-mix(in srgb, var(--focus-border) 16%, var(--panel-border) 84%);
  border: 2px solid transparent;
  background-clip: padding-box;
  min-height: 56px;
}

.settings-main.settings-main--scrollbar-hover::-webkit-scrollbar-thumb {
  background: color-mix(in srgb, var(--focus-border) 46%, var(--panel-border) 54%);
}

.settings-main.settings-main--scrollbar-hover {
  scrollbar-color: color-mix(in srgb, var(--focus-border) 46%, var(--panel-border) 54%) transparent;
}

.settings-main::-webkit-scrollbar-thumb:hover {
  background: color-mix(in srgb, var(--focus-border) 58%, var(--panel-border) 42%);
}

.config-section {
  padding: 12px 0 0;
}

.form-grid,
.placeholder-grid,
.status-grid,
.metric-grid,
.teams-grid {
  display: grid;
  gap: 8px;
  margin-top: 10px;
}

.form-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.placeholder-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.status-grid {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.metric-grid {
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

.teams-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.teams-list-head {
  grid-column: 1 / -1;
  margin-bottom: 2px;
}

.field-card,
.placeholder-card,
.status-card,
.metric-card,
.driver-card {
  padding: 10px;
}

.team-card,
.empty-card {
  padding: 9px 10px;
  border-color: color-mix(in srgb, var(--focus-border) 42%, var(--panel-border) 58%);
  box-shadow:
    inset 0 0 0 1px color-mix(in srgb, var(--focus-border) 18%, transparent),
    0 6px 16px rgba(0, 0, 0, 0.08);
}

.field-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.field-card-wide {
  grid-column: 1 / -1;
}

.field-card input,
.field-card textarea {
  width: 100%;
  border: 1px solid var(--panel-border);
  border-radius: 10px;
  background: var(--panel-bg);
  color: var(--text-strong);
  padding: 8px 10px;
  outline: none;
}

.field-card input:focus,
.field-card textarea:focus {
  border-color: var(--focus-border);
  box-shadow: 0 0 0 2px var(--focus-glow);
}

.placeholder-card strong {
  display: block;
  margin-top: 4px;
  color: var(--text-strong);
}

.status-card span,
.metric-card span {
  color: var(--muted);
}

.status-card strong,
.metric-card strong {
  display: block;
  margin-top: 4px;
  color: var(--text-strong);
  line-height: 1.35;
}

.metric-card strong {
  font-size: 1.32rem;
}

.placeholder-card p {
  margin: 6px 0 0;
  color: var(--muted);
  line-height: 1.4;
  font-size: 0.78rem;
}

.driver-card {
  margin-top: 10px;
}

.driver-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.driver-head h4 {
  margin: 0;
  color: var(--text-strong);
  font-size: 1rem;
}

.driver-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 10px;
}

.driver-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
  border: 1px solid var(--panel-border);
  border-radius: 12px;
  background: var(--panel-bg);
}

.driver-meta strong {
  display: block;
  color: var(--text-strong);
}

.driver-meta span {
  display: block;
  margin-top: 2px;
  color: var(--muted);
  font-size: 0.74rem;
}

.driver-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 64px;
  padding: 4px 10px;
  border-radius: 999px;
  background: rgba(248, 81, 73, 0.12);
  color: var(--danger);
  font-size: 0.74rem;
  font-weight: 600;
}

.driver-badge.online {
  background: rgba(86, 212, 176, 0.14);
  color: var(--good);
}

.team-card-head,
.team-card-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.team-card-title-group {
  display: flex;
  align-items: baseline;
  gap: 6px;
  min-width: 0;
}

.team-card-head strong,
.empty-card strong {
  color: var(--text-strong);
  font-size: 0.9rem;
  line-height: 1.15;
  margin: 0;
}

.team-card-id {
  color: var(--hint-text);
  font-size: 0.68rem;
  white-space: nowrap;
}

.team-card-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 54px;
  padding: 2px 7px;
  border-radius: 999px;
  background: rgba(248, 81, 73, 0.12);
  color: var(--danger);
  font-size: 0.64rem;
  font-weight: 600;
}

.team-card-badge.enabled {
  background: rgba(86, 212, 176, 0.14);
  color: var(--good);
}

.team-card-summary {
  display: flex;
  flex-direction: column;
  gap: 5px;
  margin-top: 7px;
}

.team-summary-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  min-width: 0;
}

.team-summary-chip {
  min-width: 0;
  max-width: 100%;
  padding: 4px 7px;
  border: 1px solid color-mix(in srgb, var(--focus-border) 22%, var(--panel-border) 78%);
  border-radius: 999px;
  background: var(--panel-bg);
  color: var(--muted);
  font-size: 0.68rem;
  line-height: 1.2;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.team-card-footer {
  margin-top: 7px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.team-last-active {
  min-width: 0;
  color: var(--hint-text);
  font-size: 0.64rem;
  line-height: 1.2;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.team-card-actions {
  margin-top: 0;
  justify-content: flex-end;
}

.team-detail-head {
  margin-top: 4px;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.team-detail-head h4 {
  margin: 0;
  color: var(--text-strong);
  font-size: 1rem;
}

.team-detail-head .section-eyebrow {
  margin-bottom: 2px;
}

.team-detail-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 8px;
}

.team-detail-status {
  color: var(--muted);
  font-size: 0.72rem;
}

.team-detail-stack {
  display: grid;
  grid-template-columns: 1fr;
  gap: 10px;
  margin-top: 10px;
  min-height: 0;
  align-items: start;
}

.empty-card p {
  margin: 4px 0 0;
  color: var(--muted);
  font-size: 0.72rem;
  line-height: 1.35;
}

.table-card {
  margin-top: 10px;
  overflow: hidden;
}

.table-row {
  padding: 8px 10px;
  border-top: 1px solid var(--panel-border);
  display: grid;
  grid-template-columns: 1.2fr 1.2fr 1fr 0.7fr;
  color: var(--text-strong);
  font-size: 0.84rem;
}

.table-row:first-child {
  border-top: none;
}

.table-row-head {
  color: var(--muted);
  background: color-mix(in srgb, var(--panel-bg) 55%, transparent);
}

</style>

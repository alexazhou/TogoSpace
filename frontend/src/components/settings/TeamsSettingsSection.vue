<script setup lang="ts">
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';
import TeamInfoCard from '../team/TeamInfoCard.vue';
import TeamTreeEditor from '../team/TeamTreeEditor.vue';
import SettingsBreadcrumb from './SettingsBreadcrumb.vue';
import ToggleSwitch from '../ui/ToggleSwitch.vue';
import { displayName } from '../../utils';
import type { SettingsBreadcrumbItem } from './types';
import type { TeamDetail, TeamSummary } from '../../types';

const props = defineProps<{
  breadcrumbItems: SettingsBreadcrumbItem[];
  selectedTeamDetail: TeamDetail | null;
  teamInfoDraft: {
    name: string;
    workingDirectory: string;
    slogan: string;
    rules: string;
  };
  hasTeamInfoChanges: boolean;
  isSavingTeamInfo: boolean;
  teamInfoStatus: string;
  teamEnabledPending: Record<number, boolean>;
  teamListLoadFailed: boolean;
  teamSummaries: Record<number, {
    activeMemberCount: number;
    offBoardMemberCount: number;
    roomCount: number;
    deptCount: number;
    hierarchyLevelCount: number;
    workingDirectory: string;
  }>;
  teams: TeamSummary[];
  formatDateTime: (value: string) => string;
}>();

const emit = defineEmits<{
  navigateBreadcrumb: [key: string];
  createTeam: [];
  openTeamDetail: [teamId: number];
  toggleTeamEnabled: [teamId: number, enabled: boolean];
  clearTeamDetail: [];
  deleteTeam: [];
  clearTeamData: [];
  saveTeamInfo: [];
  resetTeamInfoDraft: [];
  treeSaved: [];
  disableTeam: [teamId: number];
  'update:name': [value: string];
  'update:workingDirectory': [value: string];
  'update:slogan': [value: string];
  'update:rules': [value: string];
}>();

const { t } = useI18n();

const enabledTeams = computed(() => props.teams.filter((team) => team.enabled));
const disabledTeams = computed(() => props.teams.filter((team) => !team.enabled));
</script>

<template>
  <section id="teams" class="config-section">
    <SettingsBreadcrumb :items="breadcrumbItems" @navigate="emit('navigateBreadcrumb', $event)" />

    <template v-if="selectedTeamDetail">
      <div class="team-detail-head team-detail-head--compact">
        <div class="team-detail-actions">
          <ToggleSwitch
            :checked="selectedTeamDetail.enabled"
            :disabled="teamEnabledPending[selectedTeamDetail.id]"
            :label="teamEnabledPending[selectedTeamDetail.id]
              ? t('settings.teams.switching')
              : (selectedTeamDetail.enabled ? t('settings.teams.enabled') : t('settings.teams.disabled'))"
            @toggle="emit('toggleTeamEnabled', selectedTeamDetail.id, $event)"
          />
        </div>
      </div>

      <div class="team-detail-stack">
        <TeamInfoCard
          :name="teamInfoDraft.name"
          :working-directory="teamInfoDraft.workingDirectory"
          :slogan="teamInfoDraft.slogan"
          :rules="teamInfoDraft.rules"
          :editable-name="true"
          :show-working-directory="true"
          @update:name="emit('update:name', $event)"
          @update:working-directory="emit('update:workingDirectory', $event)"
          @update:slogan="emit('update:slogan', $event)"
          @update:rules="emit('update:rules', $event)"
        >
          <template #actions>
            <span v-if="teamInfoStatus" class="team-detail-status">{{ teamInfoStatus }}</span>
            <button
              v-if="hasTeamInfoChanges"
              type="button"
              class="secondary-button team-info-action-button team-info-action-button--compact"
              :disabled="isSavingTeamInfo"
              @click="emit('resetTeamInfoDraft')"
            >
              {{ t('settings.teams.reset') }}
            </button>
            <button
              type="button"
              class="secondary-button team-info-action-button"
              :disabled="!hasTeamInfoChanges || isSavingTeamInfo"
              @click="emit('saveTeamInfo')"
            >
              {{ isSavingTeamInfo ? t('settings.teams.saving') : t('settings.teams.saveBtn') }}
            </button>
          </template>
        </TeamInfoCard>

        <TeamTreeEditor
          :team-id="selectedTeamDetail.id"
          :team-name="selectedTeamDetail.name"
          :team-enabled="selectedTeamDetail.enabled"
          @saved="emit('treeSaved')"
          @disable-team="emit('disableTeam', $event)"
        />

        <div class="team-detail-danger-actions">
          <button type="button" class="secondary-button team-delete-button" @click="emit('deleteTeam')">
            {{ t('settings.teams.deleteTeam') }}
          </button>
          <button type="button" class="secondary-button team-delete-button" @click="emit('clearTeamData')">
            {{ t('settings.teams.clearData') }}
          </button>
        </div>
      </div>
    </template>

    <div v-else class="teams-grid">
      <div class="section-head section-head--compact teams-list-head">
        <button type="button" class="secondary-button" @click="emit('createTeam')">{{ t('settings.teams.newTeam') }}</button>
      </div>
      <section v-if="enabledTeams.length" class="team-group">
        <div class="team-group-head">
          <span class="team-group-title">{{ t('settings.teams.enabledGroup') }}</span>
          <span class="team-group-count">{{ t('settings.teams.count', { count: enabledTeams.length }) }}</span>
        </div>
        <div class="team-group-grid">
          <article v-for="team in enabledTeams" :key="team.id" class="team-card">
            <div class="team-card-head">
              <div class="team-card-title-group">
                <strong>{{ displayName(team) }}</strong>
                <span class="team-card-id">#{{ team.id }}</span>
              </div>
              <ToggleSwitch
                :checked="team.enabled"
                :disabled="teamEnabledPending[team.id]"
                :label="teamEnabledPending[team.id] ? t('settings.teams.switching') : (team.enabled ? t('settings.teams.enabled') : t('settings.teams.disabled'))"
                @toggle="emit('toggleTeamEnabled', team.id, $event)"
              />
            </div>
            <div class="team-card-summary">
              <div class="team-summary-row">
                <span class="team-summary-chip">{{ t('settings.teams.memberCount', { count: teamSummaries[team.id]?.activeMemberCount ?? 0 }) }}</span>
                <span class="team-summary-chip">{{ t('settings.teams.deptCount', { count: teamSummaries[team.id]?.deptCount ?? 0 }) }}</span>
                <span class="team-summary-chip">{{ t('settings.teams.roomCount', { count: teamSummaries[team.id]?.roomCount ?? 0 }) }}</span>
                <span class="team-summary-chip">{{ t('settings.teams.hierarchyCount', { count: teamSummaries[team.id]?.hierarchyLevelCount ?? 0 }) }}</span>
                <span
                  v-if="(teamSummaries[team.id]?.offBoardMemberCount ?? 0) > 0"
                  class="team-summary-chip"
                >
                  {{ t('settings.teams.offboardCount', { count: teamSummaries[team.id]?.offBoardMemberCount ?? 0 }) }}
                </span>
              </div>
            </div>
            <div class="team-card-footer">
              <span class="team-last-active">{{ t('settings.teams.lastActive', { time: formatDateTime(team.updated_at) }) }}</span>
              <div class="team-card-actions">
                <button type="button" class="ghost-button" @click="emit('openTeamDetail', team.id)">{{ t('settings.teams.viewDetail') }}</button>
              </div>
            </div>
          </article>
        </div>
      </section>

      <section v-if="disabledTeams.length" class="team-group">
        <div class="team-group-head">
          <span class="team-group-title">{{ t('settings.teams.disabledGroup') }}</span>
          <span class="team-group-count">{{ t('settings.teams.count', { count: disabledTeams.length }) }}</span>
        </div>
        <div class="team-group-grid">
          <article v-for="team in disabledTeams" :key="team.id" class="team-card team-card--disabled">
            <div class="team-card-head">
              <div class="team-card-title-group">
                <strong>{{ displayName(team) }}</strong>
                <span class="team-card-id">#{{ team.id }}</span>
              </div>
              <ToggleSwitch
                :checked="team.enabled"
                :disabled="teamEnabledPending[team.id]"
                :label="teamEnabledPending[team.id] ? t('settings.teams.switching') : (team.enabled ? t('settings.teams.enabled') : t('settings.teams.disabled'))"
                @toggle="emit('toggleTeamEnabled', team.id, $event)"
              />
            </div>
            <div class="team-card-summary">
              <div class="team-summary-row">
                <span class="team-summary-chip">{{ t('settings.teams.memberCount', { count: teamSummaries[team.id]?.activeMemberCount ?? 0 }) }}</span>
                <span class="team-summary-chip">{{ t('settings.teams.deptCount', { count: teamSummaries[team.id]?.deptCount ?? 0 }) }}</span>
                <span class="team-summary-chip">{{ t('settings.teams.roomCount', { count: teamSummaries[team.id]?.roomCount ?? 0 }) }}</span>
                <span class="team-summary-chip">{{ t('settings.teams.hierarchyCount', { count: teamSummaries[team.id]?.hierarchyLevelCount ?? 0 }) }}</span>
                <span
                  v-if="(teamSummaries[team.id]?.offBoardMemberCount ?? 0) > 0"
                  class="team-summary-chip"
                >
                  {{ t('settings.teams.offboardCount', { count: teamSummaries[team.id]?.offBoardMemberCount ?? 0 }) }}
                </span>
              </div>
            </div>
            <div class="team-card-footer">
              <span class="team-last-active">{{ t('settings.teams.lastActive', { time: formatDateTime(team.updated_at) }) }}</span>
              <div class="team-card-actions">
                <button type="button" class="ghost-button" @click="emit('openTeamDetail', team.id)">{{ t('settings.teams.viewDetail') }}</button>
              </div>
            </div>
          </article>
        </div>
      </section>

      <article v-if="teamListLoadFailed" class="empty-card">
        <strong>{{ t('settings.teams.loadFailed') }}</strong>
        <p>{{ t('settings.teams.loadFailedMsg') }}</p>
      </article>

      <article v-else-if="teams.length === 0" class="empty-card">
        <strong>{{ t('settings.teams.noTeams') }}</strong>
        <p>{{ t('settings.teams.noTeamsMsg') }}</p>
      </article>
    </div>
  </section>
</template>

<style scoped>
.team-card,
.empty-card {
  border: 1px solid var(--panel-border);
  border-radius: 14px;
  background: var(--surface-soft);
}

.config-section {
  padding: 12px 0 0;
}

.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.section-head,
.team-detail-head {
  margin-bottom: 8px;
}

.section-head--compact,
.team-detail-head--compact {
  justify-content: flex-end;
}

.teams-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  margin-top: 8px;
}

.teams-list-head {
  grid-column: 1 / -1;
  margin-bottom: 2px;
}

.team-group {
  grid-column: 1 / -1;
  display: grid;
  gap: 8px;
}

.team-group-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 2px 2px 0;
}

.team-group-title {
  color: var(--text-strong);
  font-size: 0.82rem;
  font-weight: 700;
}

.team-group-count {
  color: var(--settings-card-hint-text);
  font-size: 0.68rem;
}

.team-group-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.team-detail-head {
  margin-top: 4px;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.team-detail-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 8px;
  padding-top: 6px;
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

.team-detail-danger-actions {
  display: flex;
  justify-content: flex-start;
  flex-wrap: wrap;
  gap: 12px;
}

.team-info-action-button {
  min-width: 132px;
}

.team-info-action-button--compact {
  min-width: 88px;
}

.team-delete-button {
  min-width: 112px;
  border-color: color-mix(in srgb, #ef4444 30%, var(--team-create-control-border) 70%);
  background: color-mix(in srgb, var(--danger) 18%, var(--panel-bg) 82%);
  color: color-mix(in srgb, var(--text-strong) 82%, var(--danger) 18%);
}

.team-delete-button:hover:not(:disabled) {
  border-color: color-mix(in srgb, #ef4444 62%, var(--focus-border) 38%);
  background: color-mix(in srgb, var(--danger) 26%, var(--panel-bg) 74%);
  color: color-mix(in srgb, var(--text-strong) 72%, var(--danger) 28%);
}

.team-card,
.empty-card {
  padding: 9px 10px;
  border-color: color-mix(in srgb, var(--focus-border) 42%, var(--panel-border) 58%);
  box-shadow:
    inset 0 0 0 1px color-mix(in srgb, var(--focus-border) 18%, transparent),
    0 6px 16px rgba(0, 0, 0, 0.08);
}

.team-card--disabled {
  border-color: color-mix(in srgb, var(--panel-border) 86%, transparent 14%);
  background: color-mix(in srgb, var(--panel-bg) 90%, var(--surface-soft) 10%);
  box-shadow:
    inset 0 0 0 1px color-mix(in srgb, var(--panel-border) 56%, transparent 44%),
    0 4px 10px rgba(0, 0, 0, 0.04);
}

.team-card--disabled .team-card-head strong,
.team-card--disabled .team-group-title {
  color: color-mix(in srgb, var(--text-strong) 72%, var(--muted) 28%);
}

.team-card--disabled .team-card-id,
.team-card--disabled .team-last-active {
  color: color-mix(in srgb, var(--hint-text) 78%, var(--muted) 22%);
}

.team-card--disabled .team-summary-chip {
  border-color: color-mix(in srgb, var(--panel-border) 82%, transparent 18%);
  background: color-mix(in srgb, var(--panel-bg) 92%, var(--surface-soft) 8%);
  color: color-mix(in srgb, var(--muted) 86%, var(--text-strong) 14%);
}

.team-card--disabled .ghost-button {
  border-color: color-mix(in srgb, var(--panel-border) 88%, transparent 12%);
  color: color-mix(in srgb, var(--muted) 82%, var(--text-strong) 18%);
  background: color-mix(in srgb, var(--panel-bg) 94%, var(--surface-soft) 6%);
}

.team-card--disabled .ghost-button:hover:not(:disabled) {
  background: color-mix(in srgb, var(--selected) 18%, var(--panel-bg) 82%);
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
  color: var(--settings-card-hint-text);
  font-size: 0.68rem;
  white-space: nowrap;
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
  color: var(--settings-card-muted-text);
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
  color: var(--settings-card-hint-text);
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

.empty-card p {
  margin: 4px 0 0;
  color: var(--settings-card-muted-text);
  font-size: 0.72rem;
  line-height: 1.35;
}

@media (max-width: 780px) {
  .teams-grid {
    grid-template-columns: 1fr;
  }

  .team-group-grid {
    grid-template-columns: 1fr;
  }

  .team-card-footer {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>

import { computed, ref, watch, type ComputedRef, type Ref } from 'vue';
import { getTeamDetail, updateTeam } from '../api';
import { showGlobalSuccessToast } from '../appUiState';
import type { TeamDetail, TeamSummary } from '../types';

export type TeamInfoDraft = {
  name: string;
  workingDirectory: string;
  slogan: string;
  rules: string;
};

type TranslateFn = (key: string, params?: Record<string, string | number>) => string;

type UseSettingsTeamDetailStateOptions = {
  currentSectionId: ComputedRef<string>;
  detailTeamId: ComputedRef<number | null>;
  selectedTeamDetail?: Ref<TeamDetail | null>;
  teams: Ref<TeamSummary[]>;
  loadTeams: () => Promise<void>;
  loadTeamSummaries: () => Promise<void>;
  t: TranslateFn;
};

export function buildTeamInfoDraft(detail: TeamDetail): TeamInfoDraft {
  return {
    name: detail.name,
    workingDirectory: detail.working_directory || '',
    slogan: String(detail.config?.slogan || ''),
    rules: String(detail.config?.rules || ''),
  };
}

export function useSettingsTeamDetailState(options: UseSettingsTeamDetailStateOptions) {
  const selectedTeamDetail = options.selectedTeamDetail ?? ref<TeamDetail | null>(null);
  const teamInfoDraft = ref<TeamInfoDraft>({
    name: '',
    workingDirectory: '',
    slogan: '',
    rules: '',
  });
  const isSavingTeamInfo = ref(false);
  const teamInfoStatus = ref('');

  const hasTeamInfoChanges = computed(() => {
    if (!selectedTeamDetail.value) {
      return false;
    }

    return (
      teamInfoDraft.value.name !== selectedTeamDetail.value.name ||
      teamInfoDraft.value.workingDirectory !== (selectedTeamDetail.value.working_directory || '') ||
      teamInfoDraft.value.slogan !== String(selectedTeamDetail.value.config?.slogan || '') ||
      teamInfoDraft.value.rules !== String(selectedTeamDetail.value.config?.rules || '')
    );
  });

  async function loadSelectedTeamDetail(targetTeamId: number | null): Promise<void> {
    if (targetTeamId === null) {
      selectedTeamDetail.value = null;
      teamInfoStatus.value = '';
      return;
    }

    try {
      selectedTeamDetail.value = await getTeamDetail(targetTeamId);
      teamInfoDraft.value = buildTeamInfoDraft(selectedTeamDetail.value);
      teamInfoStatus.value = '';
    } catch (error) {
      console.error(error);
      selectedTeamDetail.value = null;
    }
  }

  async function saveTeamInfo(): Promise<void> {
    if (!selectedTeamDetail.value || isSavingTeamInfo.value || !hasTeamInfoChanges.value) {
      return;
    }

    isSavingTeamInfo.value = true;
    teamInfoStatus.value = '';

    try {
      await updateTeam(selectedTeamDetail.value.id, {
        name: teamInfoDraft.value.name.trim(),
        working_directory: teamInfoDraft.value.workingDirectory,
        config: {
          ...(selectedTeamDetail.value.config || {}),
          slogan: teamInfoDraft.value.slogan,
          rules: teamInfoDraft.value.rules,
        },
      });
      await Promise.all([
        loadSelectedTeamDetail(selectedTeamDetail.value.id),
        options.loadTeamSummaries(),
        options.loadTeams(),
      ]);
      teamInfoStatus.value = options.t('settings.page.teamSavedStatus');
      showGlobalSuccessToast(options.t('settings.page.teamSaved'));
    } catch (error) {
      console.error(error);
      teamInfoStatus.value = options.t('settings.page.teamSaveFailed');
    } finally {
      isSavingTeamInfo.value = false;
    }
  }

  function resetTeamInfoDraft(): void {
    if (!selectedTeamDetail.value) {
      return;
    }

    teamInfoDraft.value = buildTeamInfoDraft(selectedTeamDetail.value);
    teamInfoStatus.value = '';
  }

  function clearSelectedTeamDetail(): void {
    selectedTeamDetail.value = null;
    teamInfoStatus.value = '';
  }

  function handleTeamTreeSaved(): void {
    if (!selectedTeamDetail.value) {
      return;
    }

    Promise.all([
      loadSelectedTeamDetail(selectedTeamDetail.value.id),
      options.loadTeamSummaries(),
      options.loadTeams(),
    ]).catch(console.error);
  }

  watch(
    options.teams,
    (latestTeams) => {
      if (!selectedTeamDetail.value) {
        return;
      }

      const latestSummary = latestTeams.find((team) => team.id === selectedTeamDetail.value?.id);
      if (!latestSummary || selectedTeamDetail.value.enabled === latestSummary.enabled) {
        return;
      }

      selectedTeamDetail.value = {
        ...selectedTeamDetail.value,
        enabled: latestSummary.enabled,
      };
    },
    { deep: true },
  );

  watch(
    [options.currentSectionId, options.detailTeamId],
    ([sectionId, targetTeamId]) => {
      if (sectionId === 'teams') {
        loadSelectedTeamDetail(targetTeamId).catch(console.error);
        return;
      }
      clearSelectedTeamDetail();
    },
    { immediate: true },
  );

  return {
    clearSelectedTeamDetail,
    handleTeamTreeSaved,
    hasTeamInfoChanges,
    isSavingTeamInfo,
    loadSelectedTeamDetail,
    resetTeamInfoDraft,
    saveTeamInfo,
    selectedTeamDetail,
    teamInfoDraft,
    teamInfoStatus,
  };
}

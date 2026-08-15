import { ref, type ComputedRef, type Ref } from 'vue';
import { clearAgentData, clearTeamData, deleteTeam, setTeamEnabled } from '../api';
import { showGlobalSuccessToast } from '../appUiState';
import type { TeamDetail, TeamSummary } from '../types';

type TranslateFn = (key: string, params?: Record<string, string | number>) => string;

type SettingsRouterLike = {
  push: (location: {
    name: string;
    params?: Record<string, any>;
    query?: Record<string, any>;
  }) => Promise<unknown>;
};

type UseSettingsTeamMutationsOptions = {
  teamId: ComputedRef<number>;
  teams: Ref<TeamSummary[]>;
  selectedTeamDetail: Ref<TeamDetail | null>;
  loadTeams: () => Promise<void>;
  loadTeamSummaries: () => Promise<void>;
  loadSelectedTeamDetail: (teamId: number | null) => Promise<void>;
  clearSelectedTeamDetail: () => void;
  router: SettingsRouterLike;
  t: TranslateFn;
};

type TeamToggleConfirmState = {
  open: boolean;
  teamId: number | null;
  teamName: string;
  enabled: boolean;
};

type TeamDeleteConfirmState = {
  open: boolean;
  teamId: number | null;
  teamName: string;
};

function createClosedToggleConfirm(): TeamToggleConfirmState {
  return {
    open: false,
    teamId: null,
    teamName: '',
    enabled: false,
  };
}

function createClosedDeleteConfirm(): TeamDeleteConfirmState {
  return {
    open: false,
    teamId: null,
    teamName: '',
  };
}

export function useSettingsTeamMutations(options: UseSettingsTeamMutationsOptions) {
  const teamEnabledPending = ref<Record<number, boolean>>({});
  const teamToggleConfirm = ref<TeamToggleConfirmState>(createClosedToggleConfirm());
  const teamDeleteConfirm = ref<TeamDeleteConfirmState>(createClosedDeleteConfirm());
  const teamClearDataConfirm = ref<TeamDeleteConfirmState>(createClosedDeleteConfirm());

  async function updateTeamEnabledState(teamIdToUpdate: number, enabled: boolean): Promise<void> {
    if (teamEnabledPending.value[teamIdToUpdate]) {
      return;
    }

    teamEnabledPending.value = {
      ...teamEnabledPending.value,
      [teamIdToUpdate]: true,
    };

    try {
      await setTeamEnabled(teamIdToUpdate, enabled);
      await Promise.all([
        options.loadTeams(),
        options.loadTeamSummaries(),
        options.selectedTeamDetail.value?.id === teamIdToUpdate
          ? options.loadSelectedTeamDetail(teamIdToUpdate)
          : Promise.resolve(),
      ]);
      showGlobalSuccessToast(enabled ? options.t('settings.page.teamEnabled') : options.t('settings.page.teamDisabled'));
    } catch (error) {
      console.error(error);
    } finally {
      const nextPending = { ...teamEnabledPending.value };
      delete nextPending[teamIdToUpdate];
      teamEnabledPending.value = nextPending;
    }
  }

  function requestTeamEnabledToggle(teamIdToUpdate: number, enabled: boolean): void {
    const team = options.teams.value.find((item) => item.id === teamIdToUpdate);
    if (!team) {
      return;
    }

    teamToggleConfirm.value = {
      open: true,
      teamId: teamIdToUpdate,
      teamName: team.name,
      enabled,
    };
  }

  function closeTeamToggleConfirm(): void {
    teamToggleConfirm.value = createClosedToggleConfirm();
  }

  function confirmTeamToggle(): void {
    const { teamId: targetTeamId, enabled } = teamToggleConfirm.value;
    closeTeamToggleConfirm();
    if (targetTeamId === null) {
      return;
    }
    void updateTeamEnabledState(targetTeamId, enabled);
  }

  function requestDeleteSelectedTeam(): void {
    if (!options.selectedTeamDetail.value) {
      return;
    }

    teamDeleteConfirm.value = {
      open: true,
      teamId: options.selectedTeamDetail.value.id,
      teamName: options.selectedTeamDetail.value.name,
    };
  }

  function closeTeamDeleteConfirm(): void {
    teamDeleteConfirm.value = createClosedDeleteConfirm();
  }

  async function confirmDeleteTeam(): Promise<void> {
    const { teamId: targetTeamId } = teamDeleteConfirm.value;
    closeTeamDeleteConfirm();
    if (targetTeamId === null) {
      return;
    }

    try {
      await deleteTeam(targetTeamId);
      await Promise.all([
        options.loadTeams(),
        options.loadTeamSummaries(),
      ]);
      options.clearSelectedTeamDetail();
      const nextTeamId = options.teams.value[0]?.id ?? options.teamId.value;
      options.router.push({
        name: 'settings',
        params: { teamId: nextTeamId, section: 'teams' },
      }).catch(console.error);
      showGlobalSuccessToast(options.t('settings.page.teamDeleted'));
    } catch (error) {
      console.error(error);
    }
  }

  function requestClearTeamData(): void {
    if (!options.selectedTeamDetail.value) {
      return;
    }

    teamClearDataConfirm.value = {
      open: true,
      teamId: options.selectedTeamDetail.value.id,
      teamName: options.selectedTeamDetail.value.name,
    };
  }

  function closeTeamClearDataConfirm(): void {
    teamClearDataConfirm.value = createClosedDeleteConfirm();
  }

  async function confirmClearTeamData(): Promise<void> {
    const { teamId: targetTeamId, teamName } = teamClearDataConfirm.value;
    closeTeamClearDataConfirm();
    if (targetTeamId === null) {
      return;
    }

    try {
      const result = await clearTeamData(targetTeamId);
      showGlobalSuccessToast(
        options.t('settings.page.clearDataSuccess', {
          name: teamName,
          rooms: result.deleted.rooms,
          tasks: result.deleted.tasks,
          histories: result.deleted.histories,
          messages: result.deleted.messages,
          activities: result.deleted.activities,
        }),
      );
    } catch (error) {
      console.error(error);
    }
  }

  async function confirmClearAgentData(agentId: number, agentName: string): Promise<void> {
    closeTeamClearDataConfirm();

    try {
      const result = await clearAgentData(agentId);
      showGlobalSuccessToast(
        options.t('member.clearDataSuccess', {
          name: agentName,
          histories: result.deleted.histories,
        }),
      );
    } catch (error) {
      console.error(error);
    }
  }

  return {
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
  };
}

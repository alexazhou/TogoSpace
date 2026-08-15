import { beforeEach, describe, expect, it, vi } from 'vitest';
import { computed, effectScope, ref, type EffectScope } from 'vue';
import type { TeamDetail, TeamSummary } from '../../types';

const {
  clearTeamDataMock,
  deleteTeamMock,
  setTeamEnabledMock,
  showGlobalSuccessToastMock,
} = vi.hoisted(() => ({
  clearTeamDataMock: vi.fn(),
  deleteTeamMock: vi.fn(),
  setTeamEnabledMock: vi.fn(),
  showGlobalSuccessToastMock: vi.fn(),
}));

vi.mock('../../api', () => ({
  clearTeamData: clearTeamDataMock,
  deleteTeam: deleteTeamMock,
  setTeamEnabled: setTeamEnabledMock,
}));

vi.mock('../../appUiState', () => ({
  showGlobalSuccessToast: showGlobalSuccessToastMock,
}));

import { useSettingsTeamMutations } from '../useSettingsTeamMutations';

function createTeamSummary(overrides: Partial<TeamSummary>): TeamSummary {
  return {
    id: 1,
    name: 'Alpha',
    i18n: {},
    working_directory: '/workspace',
    config: {},
    max_function_calls: null,
    enabled: true,
    created_at: '',
    updated_at: '',
    ...overrides,
  };
}

function createTeamDetail(overrides: Partial<TeamDetail>): TeamDetail {
  return {
    ...createTeamSummary({}),
    members: [],
    rooms: [],
    ...overrides,
  };
}

describe('useSettingsTeamMutations', () => {
  let scope: EffectScope | null = null;

  async function flushPromises(): Promise<void> {
    await Promise.resolve();
    await Promise.resolve();
  }

  beforeEach(() => {
    scope?.stop();
    scope = null;
    clearTeamDataMock.mockReset();
    deleteTeamMock.mockReset();
    setTeamEnabledMock.mockReset();
    showGlobalSuccessToastMock.mockReset();
  });

  function mountState() {
    const teams = ref([
      createTeamSummary({ id: 9, name: 'Alpha' }),
      createTeamSummary({ id: 12, name: 'Beta' }),
    ]);
    const selectedTeamDetail = ref<TeamDetail | null>(createTeamDetail({ id: 9, name: 'Alpha' }));
    const loadTeams = vi.fn().mockResolvedValue(undefined);
    const loadTeamSummaries = vi.fn().mockResolvedValue(undefined);
    const loadSelectedTeamDetail = vi.fn().mockResolvedValue(undefined);
    const clearSelectedTeamDetail = vi.fn();
    const router = {
      push: vi.fn().mockResolvedValue(undefined),
    };

    scope = effectScope();
    const state = scope.run(() => useSettingsTeamMutations({
      teamId: computed(() => 3),
      teams,
      selectedTeamDetail,
      loadTeams,
      loadTeamSummaries,
      loadSelectedTeamDetail,
      clearSelectedTeamDetail,
      router,
      t: (key: string) => key,
    }));

    if (!state) {
      throw new Error('Failed to mount team mutations');
    }

    return {
      clearSelectedTeamDetail,
      loadSelectedTeamDetail,
      loadTeamSummaries,
      loadTeams,
      router,
      selectedTeamDetail,
      state,
      teams,
    };
  }

  it('confirms team enable toggles and refreshes dependent state', async () => {
    setTeamEnabledMock.mockResolvedValue(undefined);
    const { loadSelectedTeamDetail, loadTeamSummaries, loadTeams, state } = mountState();

    state.requestTeamEnabledToggle(9, false);
    expect(state.teamToggleConfirm.value).toMatchObject({
      open: true,
      teamId: 9,
      teamName: 'Alpha',
      enabled: false,
    });

    state.confirmTeamToggle();
    await flushPromises();

    expect(setTeamEnabledMock).toHaveBeenCalledWith(9, false);
    expect(loadTeams).toHaveBeenCalled();
    expect(loadTeamSummaries).toHaveBeenCalled();
    expect(loadSelectedTeamDetail).toHaveBeenCalledWith(9);
    expect(showGlobalSuccessToastMock).toHaveBeenCalledWith('settings.page.teamDisabled');
  });

  it('deletes the selected team and routes to the next available team', async () => {
    deleteTeamMock.mockResolvedValue(undefined);
    const { clearSelectedTeamDetail, loadTeamSummaries, loadTeams, router, state, teams } = mountState();
    loadTeams.mockImplementation(async () => {
      teams.value = [createTeamSummary({ id: 12, name: 'Beta' })];
    });

    state.requestDeleteSelectedTeam();
    expect(state.teamDeleteConfirm.value.open).toBe(true);

    await state.confirmDeleteTeam();

    expect(deleteTeamMock).toHaveBeenCalledWith(9);
    expect(loadTeams).toHaveBeenCalled();
    expect(loadTeamSummaries).toHaveBeenCalled();
    expect(clearSelectedTeamDetail).toHaveBeenCalled();
    expect(router.push).toHaveBeenCalledWith({
      name: 'settings',
      params: { teamId: 12, section: 'teams' },
    });
    expect(showGlobalSuccessToastMock).toHaveBeenCalledWith('settings.page.teamDeleted');
  });

  it('clears selected team data and formats the success toast payload', async () => {
    clearTeamDataMock.mockResolvedValue({
      deleted: {
        tasks: 3,
        histories: 4,
        messages: 5,
      },
    });
    const { state } = mountState();

    state.requestClearTeamData();
    expect(state.teamClearDataConfirm.value.open).toBe(true);

    await state.confirmClearTeamData();

    expect(clearTeamDataMock).toHaveBeenCalledWith(9);
    expect(showGlobalSuccessToastMock).toHaveBeenCalledWith('settings.page.clearDataSuccess');
  });
});

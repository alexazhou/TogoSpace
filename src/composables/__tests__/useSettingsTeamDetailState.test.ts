import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { computed, effectScope, nextTick, ref, type EffectScope } from 'vue';
import type { TeamDetail, TeamSummary } from '../../types';

const {
  getTeamDetailMock,
  updateTeamMock,
  showGlobalSuccessToastMock,
} = vi.hoisted(() => ({
  getTeamDetailMock: vi.fn(),
  updateTeamMock: vi.fn(),
  showGlobalSuccessToastMock: vi.fn(),
}));

vi.mock('../../api', () => ({
  getTeamDetail: getTeamDetailMock,
  updateTeam: updateTeamMock,
}));

vi.mock('../../appUiState', () => ({
  showGlobalSuccessToast: showGlobalSuccessToastMock,
}));

import { useSettingsTeamDetailState } from '../useSettingsTeamDetailState';

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

describe('useSettingsTeamDetailState', () => {
  let scope: EffectScope | null = null;

  beforeEach(() => {
    getTeamDetailMock.mockReset();
    updateTeamMock.mockReset();
    showGlobalSuccessToastMock.mockReset();
  });

  afterEach(() => {
    scope?.stop();
    scope = null;
  });

  function mountState(options?: {
    section?: string;
    detailTeamId?: number | null;
    teams?: TeamSummary[];
  }) {
    const currentSectionId = ref(options?.section ?? 'teams');
    const detailTeamId = ref<number | null>(options?.detailTeamId ?? 9);
    const teams = ref(options?.teams ?? [createTeamSummary({ id: 9, enabled: true })]);
    const loadTeams = vi.fn().mockResolvedValue(undefined);
    const loadTeamSummaries = vi.fn().mockResolvedValue(undefined);

    scope = effectScope();
    const state = scope.run(() => useSettingsTeamDetailState({
      currentSectionId: computed(() => currentSectionId.value),
      detailTeamId: computed(() => detailTeamId.value),
      teams,
      loadTeams,
      loadTeamSummaries,
      t: (key: string) => key,
    }));

    if (!state) {
      throw new Error('Failed to mount team detail state');
    }

    return {
      currentSectionId,
      detailTeamId,
      teams,
      loadTeams,
      loadTeamSummaries,
      state,
    };
  }

  it('loads selected team detail when entering the teams section', async () => {
    getTeamDetailMock.mockResolvedValue(createTeamDetail({
      id: 9,
      name: 'Alpha',
      working_directory: '/detail',
      config: { slogan: 'S', rules: 'R' },
    }));

    const { state } = mountState();
    await nextTick();
    await Promise.resolve();

    expect(getTeamDetailMock).toHaveBeenCalledWith(9);
    expect(state.selectedTeamDetail.value?.name).toBe('Alpha');
    expect(state.teamInfoDraft.value).toEqual({
      name: 'Alpha',
      workingDirectory: '/detail',
      slogan: 'S',
      rules: 'R',
    });
  });

  it('saves edited team info and refreshes dependent state', async () => {
    getTeamDetailMock.mockResolvedValue(createTeamDetail({
      id: 9,
      name: 'Alpha',
      working_directory: '/detail',
      config: { slogan: 'S', rules: 'R' },
    }));
    updateTeamMock.mockResolvedValue(undefined);

    const { state, loadTeamSummaries, loadTeams } = mountState();
    await nextTick();
    await Promise.resolve();

    state.teamInfoDraft.value.name = '  Beta  ';
    state.teamInfoDraft.value.rules = 'R2';

    await state.saveTeamInfo();

    expect(updateTeamMock).toHaveBeenCalledWith(9, {
      name: 'Beta',
      working_directory: '/detail',
      config: { slogan: 'S', rules: 'R2' },
    });
    expect(loadTeamSummaries).toHaveBeenCalled();
    expect(loadTeams).toHaveBeenCalled();
    expect(state.teamInfoStatus.value).toBe('settings.page.teamSavedStatus');
    expect(showGlobalSuccessToastMock).toHaveBeenCalledWith('settings.page.teamSaved');
  });

  it('clears selection outside the teams section and syncs enabled state from summaries', async () => {
    getTeamDetailMock.mockResolvedValue(createTeamDetail({
      id: 9,
      name: 'Alpha',
      enabled: true,
    }));

    const { currentSectionId, teams, state } = mountState();
    await nextTick();
    await Promise.resolve();

    teams.value = [createTeamSummary({ id: 9, enabled: false })];
    await nextTick();
    expect(state.selectedTeamDetail.value?.enabled).toBe(false);

    currentSectionId.value = 'roles';
    await nextTick();
    await Promise.resolve();

    expect(state.selectedTeamDetail.value).toBeNull();
    expect(state.teamInfoStatus.value).toBe('');
  });
});

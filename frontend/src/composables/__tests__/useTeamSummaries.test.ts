import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { effectScope, nextTick, ref, type EffectScope } from 'vue';
import type { TeamSummary } from '../../types';

const {
  getAgentsByTeamIdMock,
  getDeptTreeMock,
  getTeamDetailMock,
} = vi.hoisted(() => ({
  getAgentsByTeamIdMock: vi.fn(),
  getDeptTreeMock: vi.fn(),
  getTeamDetailMock: vi.fn(),
}));

vi.mock('../../api', () => ({
  getAgentsByTeamId: getAgentsByTeamIdMock,
  getDeptTree: getDeptTreeMock,
  getTeamDetail: getTeamDetailMock,
}));

import { useTeamSummaries } from '../useTeamSummaries';

function createTeam(overrides: Partial<TeamSummary>): TeamSummary {
  return {
    id: 1,
    name: 'Alpha',
    i18n: {},
    working_directory: '/tmp/team',
    config: {},
    max_function_calls: null,
    enabled: true,
    created_at: '',
    updated_at: '',
    ...overrides,
  };
}

describe('useTeamSummaries', () => {
  let scope: EffectScope | null = null;

  beforeEach(() => {
    getAgentsByTeamIdMock.mockReset();
    getDeptTreeMock.mockReset();
    getTeamDetailMock.mockReset();
  });

  afterEach(() => {
    scope?.stop();
    scope = null;
  });

  it('loads summary metrics for teams and counts off-board members separately', async () => {
    const teams = ref([createTeam({ id: 8, working_directory: '/fallback' })]);
    getTeamDetailMock.mockResolvedValue({
      rooms: [{ id: 1 }, { id: 2 }],
      working_directory: '/detail',
    });
    getDeptTreeMock.mockResolvedValue({
      name: '总部',
      responsibility: '',
      manager_id: 1,
      agent_ids: [1],
      children: [],
    });
    getAgentsByTeamIdMock.mockResolvedValue([
      { employ_status: 'ON_BOARD' },
      { employ_status: 'OFF_BOARD' },
      {},
    ]);

    scope = effectScope();
    const state = scope.run(() => useTeamSummaries(teams));
    if (!state) {
      throw new Error('Failed to mount team summaries');
    }

    await state.loadTeamSummaries();
    await nextTick();

    expect(state.teamSummaries.value[8]).toMatchObject({
      activeMemberCount: 2,
      offBoardMemberCount: 1,
      roomCount: 2,
      workingDirectory: '/detail',
    });
  });

  it('falls back to zeroed summary data when one team summary request fails', async () => {
    const teams = ref([createTeam({ id: 5, working_directory: '/fallback' })]);
    getTeamDetailMock.mockRejectedValue(new Error('boom'));
    getDeptTreeMock.mockResolvedValue(null);
    getAgentsByTeamIdMock.mockResolvedValue([]);

    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

    scope = effectScope();
    const state = scope.run(() => useTeamSummaries(teams));
    if (!state) {
      throw new Error('Failed to mount team summaries');
    }

    await state.loadTeamSummaries();
    await nextTick();

    expect(state.teamSummaries.value[5]).toEqual({
      activeMemberCount: 0,
      offBoardMemberCount: 0,
      roomCount: 0,
      deptCount: 0,
      hierarchyLevelCount: 0,
      workingDirectory: '/fallback',
    });

    consoleErrorSpy.mockRestore();
  });
});

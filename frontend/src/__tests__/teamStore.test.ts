import { beforeEach, describe, expect, it, vi } from 'vitest';

const { getTeamsMock } = vi.hoisted(() => ({
  getTeamsMock: vi.fn(),
}));

vi.mock('../api', () => ({
  getTeams: getTeamsMock,
}));

async function loadTeamStore() {
  vi.resetModules();
  return import('../teamStore');
}

describe('teamStore', () => {
  beforeEach(() => {
    localStorage.clear();
    getTeamsMock.mockReset();
  });

  it('reads preferred team id from localStorage on module init', async () => {
    localStorage.setItem('preferred-team-id', '7');

    const teamStore = await loadTeamStore();

    expect(teamStore.preferredTeamId.value).toBe(7);
  });

  it('persists and clears preferred team id', async () => {
    const teamStore = await loadTeamStore();

    teamStore.setPreferredTeamId(12);
    expect(teamStore.preferredTeamId.value).toBe(12);
    expect(localStorage.getItem('preferred-team-id')).toBe('12');

    teamStore.setPreferredTeamId(null);
    expect(teamStore.preferredTeamId.value).toBeNull();
    expect(localStorage.getItem('preferred-team-id')).toBeNull();
  });

  it('loads teams successfully and updates derived state', async () => {
    const teamStore = await loadTeamStore();
    getTeamsMock.mockResolvedValue([
      {
        id: 2,
        name: 'Alpha',
        i18n: {},
        working_directory: '/tmp/a',
        config: {},
        max_function_calls: null,
        enabled: true,
        created_at: '',
        updated_at: '',
      },
    ]);

    await teamStore.loadTeams();

    expect(teamStore.teams.value).toHaveLength(1);
    expect(teamStore.teamsLoaded.value).toBe(true);
    expect(teamStore.teamsLoadFailed.value).toBe(false);
    expect(teamStore.firstTeamId.value).toBe(2);
    expect(teamStore.findTeamById(2)?.name).toBe('Alpha');
  });

  it('marks load failure when team loading rejects', async () => {
    const teamStore = await loadTeamStore();
    getTeamsMock.mockRejectedValue(new Error('boom'));

    await expect(teamStore.loadTeams()).rejects.toThrow('boom');
    expect(teamStore.teamsLoadFailed.value).toBe(true);
    expect(teamStore.teamsLoaded.value).toBe(false);
  });
});

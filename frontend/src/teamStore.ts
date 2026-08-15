import { computed, ref } from 'vue';
import { getTeams } from './api';
import type { TeamSummary } from './types';

const preferredTeamStorageKey = 'preferred-team-id';

export const teams = ref<TeamSummary[]>([]);
export const teamsLoaded = ref(false);
export const teamsLoadFailed = ref(false);
export const preferredTeamId = ref<number | null>(readPreferredTeamId());

function readPreferredTeamId(): number | null {
  const raw = window.localStorage.getItem(preferredTeamStorageKey);
  if (!raw) {
    return null;
  }

  const parsed = Number(raw);
  return Number.isFinite(parsed) ? parsed : null;
}

export function setPreferredTeamId(teamId: number | null): void {
  preferredTeamId.value = teamId;
  if (teamId === null) {
    window.localStorage.removeItem(preferredTeamStorageKey);
    return;
  }
  window.localStorage.setItem(preferredTeamStorageKey, String(teamId));
}

export async function loadTeams(): Promise<void> {
  try {
    teams.value = await getTeams();
    teamsLoaded.value = true;
    teamsLoadFailed.value = false;
  } catch (error) {
    teamsLoadFailed.value = true;
    throw error;
  }
}

export function clearTeams(): void {
  teams.value = [];
  teamsLoaded.value = false;
  teamsLoadFailed.value = false;
  setPreferredTeamId(null);
}

export function findTeamById(teamId: number | null): TeamSummary | null {
  if (teamId === null) {
    return null;
  }
  return teams.value.find((team) => team.id === teamId) ?? null;
}

export const firstTeamId = computed(() => teams.value[0]?.id ?? null);

import { ref, watch, type Ref } from 'vue';
import { getAgentsByTeamId, getDeptTree, getTeamDetail } from '../api';
import { countDeptHierarchyLevels, countDeptNodes } from '../components/settings/teamSummary';
import type { TeamSummary } from '../types';

export type TeamSummaryMetrics = {
  activeMemberCount: number;
  offBoardMemberCount: number;
  roomCount: number;
  deptCount: number;
  hierarchyLevelCount: number;
  workingDirectory: string;
};

export function useTeamSummaries(teams: Ref<TeamSummary[]>) {
  const teamSummaries = ref<Record<number, TeamSummaryMetrics>>({});

  async function loadTeamSummaries(): Promise<void> {
    const entries = await Promise.all(
      teams.value.map(async (team) => {
        try {
          const [detail, deptTree, teamAgents] = await Promise.all([
            getTeamDetail(team.id),
            getDeptTree(team.id),
            getAgentsByTeamId(team.id),
          ]);
          const activeMemberCount = teamAgents.filter((agent) => String(agent.employ_status ?? '').toUpperCase() !== 'OFF_BOARD').length;
          const offBoardMemberCount = teamAgents.filter((agent) => String(agent.employ_status ?? '').toUpperCase() === 'OFF_BOARD').length;
          return [team.id, {
            activeMemberCount,
            offBoardMemberCount,
            roomCount: detail.rooms.length,
            deptCount: countDeptNodes(deptTree),
            hierarchyLevelCount: countDeptHierarchyLevels(deptTree),
            workingDirectory: detail.working_directory || team.working_directory || '',
          }] as const;
        } catch (error) {
          console.error(error);
          return [team.id, {
            activeMemberCount: 0,
            offBoardMemberCount: 0,
            roomCount: 0,
            deptCount: 0,
            hierarchyLevelCount: 0,
            workingDirectory: team.working_directory || '',
          }] as const;
        }
      }),
    );
    teamSummaries.value = Object.fromEntries(entries);
  }

  watch(
    () => teams.value.map((team) => team.id),
    (teamIds) => {
      if (!teamIds.length) {
        teamSummaries.value = {};
        return;
      }

      loadTeamSummaries().catch(console.error);
    },
    { immediate: true },
  );

  return {
    loadTeamSummaries,
    teamSummaries,
  };
}

import { computed, type MaybeRefOrGetter, toValue } from 'vue';
import { t } from '../i18n';
import { i18nText, findDepartmentPath, isDepartmentLeader } from '../utils';
import type {
  AgentActivity,
  AgentInfo,
  AgentStatus,
  DeptTreeNode,
  MessageInfo,
  RoleTemplateSummary,
  RoomState,
} from '../types';
import {
  getAgentActivities,
  getAgentStatus,
  getDeptTreeState,
  getRoomMessageHistoryState,
  getRoomMessages,
  getRoleTemplatesState,
  getTeamAgents,
  getTeamRooms,
} from './runtimeStore';

type AgentInfoWithDepartmentPath = AgentInfo & {
  departmentPath: string;
  isDepartmentLeader: boolean;
};

export function useTeamAgents(teamId: MaybeRefOrGetter<number | null>) {
  return computed<AgentInfo[]>(() => getTeamAgents(toValue(teamId)));
}

export function useTeamAgentsWithDepartmentPath(teamId: MaybeRefOrGetter<number | null>) {
  return computed<AgentInfoWithDepartmentPath[]>(() => {
    const resolvedTeamId = toValue(teamId);
    const agents = getTeamAgents(resolvedTeamId);
    const deptTree = getDeptTreeState(resolvedTeamId);

    return agents.map((agent) => {
      const agentId = typeof agent.id === 'number' ? agent.id : null;
      const departmentPath = agentId !== null ? findDepartmentPath(deptTree, agentId) : null;
      return {
        ...agent,
        departmentPath: departmentPath?.join(' / ') ?? t('teamTree.unassignedDepartment'),
        isDepartmentLeader: agentId !== null ? isDepartmentLeader(deptTree, agentId) : false,
      };
    });
  });
}

export function useTeamRooms(teamId: MaybeRefOrGetter<number | null>) {
  return computed<RoomState[]>(() => getTeamRooms(toValue(teamId)));
}

export function useRoomMessages(roomId: MaybeRefOrGetter<number | null>) {
  return computed<MessageInfo[]>(() => getRoomMessages(toValue(roomId)));
}

export function useRoomMessageHistory(roomId: MaybeRefOrGetter<number | null>) {
  return computed(() => getRoomMessageHistoryState(toValue(roomId)));
}

export function useAgentActivities(agentId: MaybeRefOrGetter<number | null>) {
  return computed<AgentActivity[]>(() => getAgentActivities(toValue(agentId)));
}

export function useAgentStatus(agentId: MaybeRefOrGetter<number | null>) {
  return computed<AgentStatus | null>(() => getAgentStatus(toValue(agentId)));
}

export function useDeptTree(teamId: MaybeRefOrGetter<number | null>) {
  return computed<DeptTreeNode | null>(() => getDeptTreeState(toValue(teamId)));
}

export function useRoleTemplates() {
  return computed<RoleTemplateSummary[]>(() => getRoleTemplatesState());
}

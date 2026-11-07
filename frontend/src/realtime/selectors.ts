import { computed, type MaybeRefOrGetter, toValue } from 'vue';
import { t } from '../i18n';
import { i18nText, findDepartmentPath, isDepartmentLeader } from '../utils';
import type {
  AgentActivity,
  AgentContextUsage,
  AgentInfo,
  AgentStatus,
  DeptTreeNode,
  MessageInfo,
  RoleTemplateSummary,
  RoomState,
} from '../types';
import {
  getAgentActivities,
  getAgentContextUsage,
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

/**
 * 上下文用量响应式读取；`null` 表示「不知道」（组件据此整条不渲染）。
 *
 * 与 `useAgentStatus` 同构：任何组件都能按 agentId 消费，无需自己订阅 WS。
 */
export function useAgentContextUsage(agentId: MaybeRefOrGetter<number | null>) {
  return computed<AgentContextUsage | null>(() => getAgentContextUsage(toValue(agentId)));
}

export function useDeptTree(teamId: MaybeRefOrGetter<number | null>) {
  return computed<DeptTreeNode | null>(() => getDeptTreeState(toValue(teamId)));
}

export function useRoleTemplates() {
  return computed<RoleTemplateSummary[]>(() => getRoleTemplatesState());
}

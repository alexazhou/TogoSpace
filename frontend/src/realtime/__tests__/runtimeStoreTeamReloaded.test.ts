import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { AgentInfo, DeptTreeNode, RoomInfo } from '../../types';

const apiMocks = vi.hoisted(() => ({
  getAgentsByTeamId: vi.fn(),
  getRooms: vi.fn(),
  getRoomLastMessages: vi.fn(),
  getDeptTree: vi.fn(),
}));

vi.mock('../../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api')>();
  return {
    ...actual,
    getAgentsByTeamId: apiMocks.getAgentsByTeamId,
    getRooms: apiMocks.getRooms,
    getRoomLastMessages: apiMocks.getRoomLastMessages,
    getDeptTree: apiMocks.getDeptTree,
  };
});

import {
  applyRealtimeEvent,
  clearRuntimeStore,
  getDeptTreeState,
  getTeamAgents,
  seedDeptTree,
  seedTeamAgents,
} from '../runtimeStore';

const TEAM_ID = 7;

function createAgent(overrides: Partial<AgentInfo>): AgentInfo {
  return {
    id: 1,
    name: 'Agent',
    i18n: {},
    model: '',
    status: 'idle',
    ...overrides,
  };
}

function createDeptTree(name: string): DeptTreeNode {
  return {
    id: 1,
    name,
    responsibility: '',
    manager_id: 5,
    agent_ids: [5],
    children: [],
  };
}

describe('runtimeStore team_reloaded handling', () => {
  beforeEach(() => {
    clearRuntimeStore();
    apiMocks.getAgentsByTeamId.mockReset();
    apiMocks.getRooms.mockReset();
    apiMocks.getRoomLastMessages.mockReset();
    apiMocks.getDeptTree.mockReset();

    apiMocks.getAgentsByTeamId.mockResolvedValue([createAgent({ id: 5, name: 'Alice' })]);
    apiMocks.getRooms.mockResolvedValue([] as RoomInfo[]);
    apiMocks.getRoomLastMessages.mockResolvedValue([]);
  });

  afterEach(() => {
    clearRuntimeStore();
  });

  // 成员的部门路径由部门树现算（selectors 读 getDeptTreeState），
  // 只重载成员列表不会刷新它，因此热更新事件必须一并重载部门树。
  it('reloads the department tree so member department paths refresh', async () => {
    seedDeptTree(TEAM_ID, createDeptTree('旧部门'));
    seedTeamAgents(TEAM_ID, [createAgent({ id: 5, name: 'Alice' })]);

    apiMocks.getDeptTree.mockResolvedValue(createDeptTree('新部门'));

    applyRealtimeEvent({ type: 'team_reloaded', teamId: TEAM_ID });

    await vi.waitFor(() => {
      expect(getDeptTreeState(TEAM_ID)?.name).toBe('新部门');
    });
    expect(apiMocks.getDeptTree).toHaveBeenCalledWith(TEAM_ID);
  });

  it('still reloads rooms and agents on the same event', async () => {
    applyRealtimeEvent({ type: 'team_reloaded', teamId: TEAM_ID });

    await vi.waitFor(() => {
      expect(getTeamAgents(TEAM_ID).map((agent) => agent.id)).toEqual([5]);
    });
    expect(apiMocks.getAgentsByTeamId).toHaveBeenCalledWith(TEAM_ID, { includeSpecial: true });
    expect(apiMocks.getRooms).toHaveBeenCalled();
  });
});

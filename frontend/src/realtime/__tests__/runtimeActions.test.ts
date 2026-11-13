import { describe, expect, it, vi, beforeEach } from 'vitest';
import type { AgentInfo, RoomInfo } from '../../types';

/**
 * P4 行为一致性对照（action 层）：
 * 1. `load*` 仍然「先 seed 进 store、再返回同一份数据」——这是拆分前 `runtimeStore` 里的写法，
 *    调用方依赖返回值（如 `CreateRoomDialog` 直接用 `loadTeamRooms()` 的返回值）；
 * 2. `runtimeStore.ts` 源码里不再有业务 fetch 的**值**导入（只允许 `import type`），
 *    这条是硬约束的静态护栏，防止以后有人把 fetch 又搬回 store。
 */
const apiMocks = vi.hoisted(() => ({
  getAgentsByTeamId: vi.fn(),
  getRooms: vi.fn(),
  getRoomLastMessages: vi.fn(),
  getRoomMessages: vi.fn(),
  getAgentActivities: vi.fn(),
  getDeptTree: vi.fn(),
  getRoleTemplates: vi.fn(),
}));

vi.mock('../../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api')>();
  return { ...actual, ...apiMocks };
});

import {
  loadAgentActivities,
  loadDeptTree,
  loadRoleTemplates,
  loadRoomMessagesState,
  loadTeamAgents,
  loadTeamRooms,
} from '../runtimeActions';
import {
  clearRuntimeStore,
  getAgentActivities,
  getAgentStatus,
  getDeptTreeState,
  getRoomMessageHistoryState,
  getRoomMessages,
  getRoleTemplatesState,
  getTeamAgents,
  getTeamRooms,
} from '../runtimeStore';

const TEAM_ID = 11;

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

describe('runtimeActions：load* 的写库与返回值契约', () => {
  beforeEach(() => {
    clearRuntimeStore();
    for (const mock of Object.values(apiMocks)) {
      mock.mockReset();
    }
  });

  it('loadTeamAgents：先写 store（含 status / exec_env 派生），再返回同一份数组', async () => {
    const agents = [createAgent({ id: 7, name: 'Ann', status: 'active', exec_env: 'container', current_container_id: 'abc' })];
    apiMocks.getAgentsByTeamId.mockResolvedValue(agents);

    const returned = await loadTeamAgents(TEAM_ID, { includeSpecial: true });

    expect(apiMocks.getAgentsByTeamId).toHaveBeenCalledWith(TEAM_ID, { includeSpecial: true });
    expect(returned).toBe(agents);
    // store 里存的是 ref 包出来的响应式代理，引用与入参不同，按内容比对（拆分前行为一致）。
    expect(getTeamAgents(TEAM_ID).map((agent) => agent.id)).toEqual([7]);
    expect(getAgentStatus(7)).toBe('active');
  });

  it('loadRoomMessagesState：返回归一化后的消息，并把 hasMoreHistory 写进 store', async () => {
    apiMocks.getRoomMessages.mockResolvedValue({
      messages: [
        { id: 101, sender_id: -1, content: 'hi', send_time: '2026-01-01 00:00:00', seq: 1, insert_immediately: false },
      ],
      hasMore: true,
    });

    const messages = await loadRoomMessagesState(TEAM_ID, 5);

    expect(messages).toHaveLength(1);
    expect(messages[0].db_id).toBe(101);
    expect(getRoomMessages(5)).toHaveLength(1);
    expect(getRoomMessageHistoryState(5).hasMoreHistory).toBe(true);
  });

  it('loadTeamRooms：返回落库后的房间列表（preview 合并后）', async () => {
    apiMocks.getRooms.mockResolvedValue([
      { room_id: 3, room_name: 'Room', room_type: 'group', tags: [], agents: [], preview: '', unread: 2, current_turn_agent_id: null, state: 'idle', need_scheduling: false, agent_read_index: {} } as unknown as RoomInfo,
    ]);
    apiMocks.getRoomLastMessages.mockResolvedValue([]);

    const rooms = await loadTeamRooms(TEAM_ID);

    expect(rooms).toHaveLength(1);
    expect(rooms[0].unread).toBe(0);
    expect(getTeamRooms(TEAM_ID)).toEqual(rooms);
  });

  it('loadAgentActivities / loadDeptTree / loadRoleTemplates：写库并返回', async () => {
    apiMocks.getAgentActivities.mockResolvedValue([]);
    apiMocks.getDeptTree.mockResolvedValue(null);
    apiMocks.getRoleTemplates.mockResolvedValue([{ id: 1, name: 'R', i18n: {} }]);

    await loadAgentActivities(9);
    await loadDeptTree(TEAM_ID);
    await loadRoleTemplates();

    expect(getAgentActivities(9)).toEqual([]);
    expect(getDeptTreeState(TEAM_ID)).toBeNull();
    expect(getRoleTemplatesState()).toHaveLength(1);
  });
});

// 用 Vite 的 `?raw` 读源码，避免给前端引入 @types/node（tsconfig 只含 vite/client + vitest/globals）。
const rawSources = import.meta.glob(['../runtimeStore.ts', '../runtimeActions.ts'], {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>;

describe('runtimeStore 源码护栏：不直接 import 业务 fetch', () => {
  it('store 里只有 import type，没有业务 fetch 的值导入', () => {
    const source = rawSources['../runtimeStore.ts'];
    expect(typeof source).toBe('string');
    const importLines = source.split('\n').filter((line) => line.trim().startsWith('import'));
    const apiValueImports = importLines.filter((line) => line.includes("from '../api'") && !line.trim().startsWith('import type'));
    expect(apiValueImports).toEqual([]);
    // 反向确认：action 层才是 fetch 的落点
    expect(rawSources['../runtimeActions.ts']).toContain("from '../api'");
  });
});

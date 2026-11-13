import {
  getAgentActivities as fetchAgentActivities,
  getAgentsByTeamId as fetchAgentsByTeamId,
  getDeptTree as fetchDeptTree,
  getRoleTemplates as fetchRoleTemplates,
  getRoomLastMessages as fetchRoomLastMessages,
  getRoomMessages as fetchRoomMessages,
  getRooms as fetchRooms,
} from '../api';
import type { AgentActivity, AgentInfo, DeptTreeNode, MessageInfo, RoleTemplateSummary, RoomState } from '../types';
import { formatPreview } from '../utils';
import { resolveRoomPreview } from './roomPreview';
import {
  ROOM_MESSAGES_PAGE_SIZE,
  registerTeamReloadHandler,
  resolveOldestLoadedMessageId,
  getRoomEntry,
  getTeamRooms,
  normalizeMessage,
  resolveMessageSenderDisplayName,
  seedAgentActivities,
  seedDeptTree,
  seedRoleTemplates,
  seedRoomMessages,
  seedTeamAgents,
  seedTeamRooms,
  setRoomEntry,
  syncRoomPreview,
} from './runtimeStore';

/**
 * runtimeStore 的 action 层：**唯一**负责「发起业务 API 拉取 → 通过 seed/mutation 写入 store」的地方。
 *
 * 为什么单独一层（P4）：
 * - store 只保留状态与纯 mutation（seed* / get* / set* / applyRealtimeEvent），不再 import 任何业务 fetch，
 *   这样「谁在什么时候拉数据」与「状态怎么变」彻底分离，注入测试替身时不必再 mock 整个 store。
 * - 拉取语义与迁移前逐字一致：入参、返回值的 Promise 内容、写库的先后顺序（先 seed 再返回）都没有变化，
 *   调用方只需把 import 从 `runtimeStore` 换成 `runtimeActions`，行为不变。
 */
export async function loadTeamAgents(teamId: number, options?: { includeSpecial?: boolean }): Promise<AgentInfo[]> {
  const agents = await fetchAgentsByTeamId(teamId, options);
  seedTeamAgents(teamId, agents);
  return agents;
}

export async function loadTeamRooms(teamId: number): Promise<RoomState[]> {
  const baseRooms = await fetchRooms(teamId);
  const roomIds = baseRooms.map((room) => room.room_id).filter((roomId) => roomId > 0);
  const previewMap = new Map<number, string>();

  if (roomIds.length > 0) {
    try {
      const lastMessageItems = await fetchRoomLastMessages(roomIds);
      for (const item of lastMessageItems) {
        const senderDisplayName = resolveMessageSenderDisplayName(teamId, item.sender_id);
        previewMap.set(item.room_id, formatPreview(senderDisplayName, item.content));
      }
    } catch {
      // 房间列表本身可用时，不让 preview 补充失败阻塞整个加载流程。
    }
  }

  const rooms: RoomState[] = baseRooms.map((room) => ({
    ...room,
    preview: resolveRoomPreview({
      messages: getRoomEntry(room.room_id).messages,
      previousRoom: getTeamRooms(teamId).find((item) => item.room_id === room.room_id) ?? null,
      preview: previewMap.get(room.room_id),
      resolveSenderDisplayName: (senderId) => resolveMessageSenderDisplayName(teamId, senderId),
    }),
    unread: 0,
    current_turn_agent_id: room.current_turn_agent_id ?? null,
  }));

  seedTeamRooms(teamId, rooms);
  return rooms;
}

export async function loadRoomMessagesState(teamId: number, roomId: number): Promise<MessageInfo[]> {
  const page = await fetchRoomMessages(roomId, { limit: ROOM_MESSAGES_PAGE_SIZE });
  const messages = page.messages.map((m) => normalizeMessage(teamId, m));
  seedRoomMessages(roomId, messages, { hasMoreHistory: page.hasMore });
  syncRoomPreview(teamId, roomId, messages);
  return messages;
}

export async function loadOlderRoomMessagesState(teamId: number, roomId: number): Promise<MessageInfo[]> {
  const currentEntry = getRoomEntry(roomId);
  if (!currentEntry.hasMoreHistory || currentEntry.loadingHistory) {
    return [];
  }
  const oldestMessageId = resolveOldestLoadedMessageId(currentEntry.messages);
  if (oldestMessageId === null) {
    return [];
  }

  setRoomEntry(roomId, { loadingHistory: true });

  try {
    const page = await fetchRoomMessages(roomId, {
      limit: ROOM_MESSAGES_PAGE_SIZE,
      beforeId: oldestMessageId,
    });
    const messages = page.messages.map((message) => normalizeMessage(teamId, message));
    seedRoomMessages(roomId, messages, {
      preserveExisting: true,
      hasMoreHistory: page.hasMore,
    });
    syncRoomPreview(teamId, roomId, getRoomEntry(roomId).messages);
    return messages;
  } finally {
    setRoomEntry(roomId, { loadingHistory: false });
  }
}

export async function loadAgentActivities(agentId: number): Promise<AgentActivity[]> {
  const activities = await fetchAgentActivities(agentId);
  seedAgentActivities(agentId, activities);
  return activities;
}

export async function loadDeptTree(teamId: number): Promise<DeptTreeNode | null> {
  const deptTree = await fetchDeptTree(teamId);
  seedDeptTree(teamId, deptTree);
  return deptTree;
}

export async function loadRoleTemplates(): Promise<RoleTemplateSummary[]> {
  const roleTemplates = await fetchRoleTemplates();
  seedRoleTemplates(roleTemplates);
  return roleTemplates;
}

/**
 * 把 `team_reloaded` 热更新所需的三个重拉动作注册给 store。
 *
 * 为什么需要显式安装：原实现里这三个 load 就写在 `applyRealtimeEvent` 内，store 因此直接
 * import 业务 fetch；P4 把拉取移到本层后，store 只保留一个注册点，由这里在应用启动时
 * （App.vue 调用 `startRealtimeClient()` 之前）装配，保证事件到达时处理器已就位。
 */
export function installRuntimeActions(): void {
  registerTeamReloadHandler((teamId) => {
    loadTeamRooms(teamId);
    loadTeamAgents(teamId, { includeSpecial: true });
    // 成员的部门归属由部门树（manager_id / agent_ids）现算，成员列表重载不会
    // 顺带更新它；组织树变更也走热更新，这里必须一并重载，否则成员卡片的
    // 部门路径会一直是旧值。
    loadDeptTree(teamId);
  });
}

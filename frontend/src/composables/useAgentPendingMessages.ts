import { computed, ref, toValue, watch, type ComputedRef, type MaybeRefOrGetter, type Ref } from 'vue';
import { getAgentAvatarUrl } from '../avatar';
import { connectionState } from '../appUiState';
import { getTeamAgents, loadRoomMessagesState, loadTeamRooms } from '../realtime/runtimeStore';
import { useRoomMessages, useTeamRooms } from '../realtime/selectors';
import {
  OPERATOR_ID,
  SYSTEM_ID,
  isUnreadPrivateMessage,
  resolvePendingStatus,
} from '../realtime/readCursor';
import type { ReadCursorContext } from '../realtime/readCursor';
import { buildPendingKey } from '../components/chat/pending/types';
import type { PendingMessageItem, PendingSender } from '../components/chat/pending/types';
import { displayName } from '../utils';
import type { MessageInfo } from '../types';

/**
 * Agent 活动面板的「待读消息」数据源。
 *
 * 只做控制房（Operator 与这个 Agent 的 PRIVATE 房）：
 * - 取房间：从 `teamRooms` 里按「PRIVATE + 成员含 OPERATOR(-1) 与该 Agent」自己找，
 *   **不调 `GET control_room.json`**——那个接口会「获取或创建」，只是打开卡片不该建房。
 * - 取消息：首屏拉一页最新的，WS 重连后重拉一次；日常增量由 `runtimeStore` 的
 *   `message` / `message_changed` 事件写进房间消息，这里只做订阅。
 * - 判定：与聊天页同一个函数（`readCursor.ts`），两边不会各写一套规则。
 *
 * 组件只拿到算好的 `PendingMessageItem[]`，不碰读游标与房间类型。
 */
export interface UseAgentPendingMessagesOptions {
  agentId: MaybeRefOrGetter<number | null>;
  teamId: MaybeRefOrGetter<number | null>;
  open: MaybeRefOrGetter<boolean>;
}

export interface AgentPendingMessagesController {
  /** 控制房 ID；不存在（从未督导过）时为 null。 */
  controlRoomId: Ref<number | null>;
  /** 原始待读消息，供计数 / 贴底判断用。 */
  pendingMessages: ComputedRef<MessageInfo[]>;
  /** 交给 `PendingMessageDock` 的展示数据。 */
  pendingItems: ComputedRef<PendingMessageItem[]>;
  /** 手工重拉（Dialog 在 `sendSupervise` 建出控制房后调用）。 */
  reload: () => Promise<void>;
  /**
   * 告诉它「控制房是这个 ID」。
   * 第一次督导时房间是后端刚建的，`teamRooms` 里可能还没有；拿接口返回的 `room_id`
   * 直接认下来，比等 `room_added` 推送更稳。
   */
  notifyControlRoom: (roomId: number) => void;
}

export function useAgentPendingMessages(
  options: UseAgentPendingMessagesOptions,
): AgentPendingMessagesController {
  const teamRooms = useTeamRooms(() => toValue(options.teamId));
  const teamAgents = computed(() => getTeamAgents(toValue(options.teamId)));

  /**
   * 控制房 ID 必须是响应式的：它会被 `useRoomMessages` 和两个 computed 读，
   * 用普通变量的话房间从「没有」变成「刚建好」时算不出来。
   */
  const controlRoomId = ref<number | null>(null);

  /** 从房间列表里认控制房；找不到返回 null，不触发创建。 */
  function findControlRoomId(): number | null {
    const agentId = toValue(options.agentId);
    if (agentId === null) {
      return null;
    }
    const room = teamRooms.value.find((candidate) => (
      candidate.room_type === 'private'
      && candidate.agents.includes(OPERATOR_ID)
      && candidate.agents.includes(agentId)
    ));
    return room?.room_id ?? null;
  }

  const roomMessages = useRoomMessages(() => controlRoomId.value);

  function resolveAgentReadIndex(roomId: number): Record<string, number> {
    const room = teamRooms.value.find((candidate) => candidate.room_id === roomId);
    return room?.agent_read_index ?? {};
  }

  function buildContext(): ReadCursorContext | null {
    const agentId = toValue(options.agentId);
    const roomId = controlRoomId.value;
    if (agentId === null || roomId === null) {
      return null;
    }
    // 控制房固定是「Operator + 这一个 Agent」，接收方 AI 只能是他自己。
    return {
      isPrivate: true,
      roomAgents: [OPERATOR_ID, agentId],
      agentReadIndex: resolveAgentReadIndex(roomId),
    };
  }

  const pendingMessages = computed<MessageInfo[]>(() => {
    const context = buildContext();
    if (!context) {
      return [];
    }
    return roomMessages.value.filter((message) => isUnreadPrivateMessage(message, context));
  });

  function resolveSender(senderId: number): PendingSender {
    if (senderId === SYSTEM_ID) {
      return { displayName: 'SYSTEM', avatarUrl: '', clickable: false };
    }
    if (senderId === OPERATOR_ID) {
      return {
        displayName: 'OPERATOR',
        avatarUrl: getAgentAvatarUrl('OPERATOR'),
        clickable: false,
      };
    }
    const agent = teamAgents.value.find((candidate) => candidate.id === senderId);
    return {
      displayName: agent ? displayName(agent) : String(senderId),
      avatarUrl: getAgentAvatarUrl(agent?.name ?? String(senderId)),
      clickable: Boolean(agent),
    };
  }

  const pendingItems = computed<PendingMessageItem[]>(() => {
    const context = buildContext();
    if (!context) {
      return [];
    }
    return pendingMessages.value.map((message, index) => ({
      key: buildPendingKey(message, index),
      message,
      status: resolvePendingStatus(message, context) ?? 'queued',
      sender: resolveSender(message.sender_id),
    }));
  });

  /** 拉一页最新消息；失败只影响待读区，不打断活动列表，因此静默交给控制台。 */
  async function reload(): Promise<void> {
    const teamId = toValue(options.teamId);
    const roomId = controlRoomId.value;
    if (!toValue(options.open) || teamId === null || roomId === null) {
      return;
    }
    try {
      await loadRoomMessagesState(teamId, roomId);
    } catch (error) {
      console.error(error);
    }
  }

  /**
   * 房间列表变化时重新认一次控制房。
   * 后端建房会推 `room_added`，此时 `teamRooms` 才匹配得上；补一次消息拉取，
   * 避免「房间有了但待读区还是空的」。
   */
  function syncControlRoom(): void {
    const next = findControlRoomId();
    if (next === controlRoomId.value) {
      return;
    }
    controlRoomId.value = next;
    reload().catch(console.error);
  }

  watch(
    () => [toValue(options.open), toValue(options.agentId), toValue(options.teamId)] as const,
    () => {
      controlRoomId.value = toValue(options.open) ? findControlRoomId() : null;
      reload().catch(console.error);
    },
    { immediate: true },
  );

  watch(
    () => teamRooms.value.map((room) => room.room_id).join(','),
    () => {
      if (!toValue(options.open)) {
        return;
      }
      syncControlRoom();
    },
  );

  /** WS 重连后房间状态与消息可能都过期了，重拉一次把待读区恢复出来。 */
  watch(
    () => connectionState.value,
    (state, previousState) => {
      if (state !== 'connected' || previousState === 'connected' || previousState === 'connecting') {
        return;
      }
      reload().catch(console.error);
    },
  );

  function notifyControlRoom(roomId: number): void {
    const teamId = toValue(options.teamId);
    if (controlRoomId.value !== roomId) {
      controlRoomId.value = roomId;
    }
    // 刚建的控制房可能还不在 store 里，补一次房间列表，读游标才能跟上。
    if (teamId !== null && !teamRooms.value.some((room) => room.room_id === roomId)) {
      loadTeamRooms(teamId).catch(console.error);
    }
    reload().catch(console.error);
  }

  return { controlRoomId, pendingMessages, pendingItems, reload, notifyControlRoom };
}

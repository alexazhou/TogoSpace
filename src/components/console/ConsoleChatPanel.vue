<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useTemplateRef, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import { escalateMessageToImmediate, postRoomMessage } from '../../api';
import { useConsoleMessageScroll } from '../../composables/useConsoleMessageScroll';
import { displayName } from '../../utils';
import ChatPanel from '../chat/ChatPanel.vue';
import RoomSettingsDialog from './RoomSettingsDialog.vue';
import type {
  AgentInfo,
  DeptTreeNode,
  MessageInfo,
  RoleTemplateSummary,
  RoomMemberProfile,
  RoomState,
} from '../../types';

const props = defineProps<{
  currentRoom: RoomState | null;
  agents: AgentInfo[];
  deptTree: DeptTreeNode | null;
  roleTemplates: RoleTemplateSummary[];
  messages: MessageInfo[];
  hasMoreHistory: boolean;
  loadingOlderMessages: boolean;
  errorMessage: string;
  reloadingMessages: boolean;
  teamEnabled: boolean;
}>();

const emit = defineEmits<{
  updateError: [value: string];
  clickWorkingAgent: [agentId: number];
  clickAgent: [agentId: number];
  loadOlderMessages: [];
  roomUpdated: [];
}>();

const messageViewport = useTemplateRef('messageViewport');
const draft = defineModel<string>('draft', { default: '' });
const escalatingMessageIds = ref<number[]>([]);

const {
  shouldFollowMessages,
  bindMessageScrollListener,
  scrollMessagesToBottom,
  cleanupMessageScroll,
} = useConsoleMessageScroll(messageViewport);
const { t } = useI18n();
const OPERATOR_MEMBER_ID = -1;
const roomSettingsOpen = ref(false);

const canOperatorCompose = computed(() => (
  Boolean(props.currentRoom && props.currentRoom.agents.includes(OPERATOR_MEMBER_ID))
));

const composerNotice = computed(() => {
  if (!props.teamEnabled) {
    return t('chat.teamDisabled');
  }
  if (!props.currentRoom || canOperatorCompose.value) {
    return '';
  }
  return t('chat.observeMode');
});

function isDeptRoom(room: RoomState | null): boolean {
  return Boolean(room && Array.isArray(room.tags) && room.tags.includes('DEPT'));
}

function parseDeptIdFromBizId(bizId: string | null | undefined): number | null {
  const matched = String(bizId ?? '').match(/^DEPT:(\d+)$/);
  if (!matched) {
    return null;
  }

  const deptId = Number(matched[1]);
  return Number.isFinite(deptId) ? deptId : null;
}

function findDeptNodeById(tree: DeptTreeNode | null, deptId: number): DeptTreeNode | null {
  if (!tree) {
    return null;
  }
  if (tree.id === deptId) {
    return tree;
  }
  for (const child of tree.children) {
    const matched = findDeptNodeById(child, deptId);
    if (matched) {
      return matched;
    }
  }
  return null;
}

function findDeptNodeByName(tree: DeptTreeNode | null, deptName: string): DeptTreeNode | null {
  if (!tree) {
    return null;
  }
  if (tree.name === deptName) {
    return tree;
  }
  for (const child of tree.children) {
    const matched = findDeptNodeByName(child, deptName);
    if (matched) {
      return matched;
    }
  }
  return null;
}

const currentDeptLeaderId = computed<number | null>(() => {
  const room = props.currentRoom;
  if (!room || !isDeptRoom(room)) {
    return null;
  }

  const deptId = parseDeptIdFromBizId(room.biz_id);
  const deptNode = deptId !== null
    ? findDeptNodeById(props.deptTree, deptId)
    : findDeptNodeByName(props.deptTree, room.room_name);
  if (!deptNode || typeof deptNode.manager_id !== 'number') {
    return null;
  }
  return deptNode.manager_id;
});

const memberProfiles = computed<RoomMemberProfile[]>(() => {
  if (!props.currentRoom) {
    return [];
  }

  const agentMap = new Map(props.agents.map((agent) => [agent.id, agent]));
  const templateMap = new Map(props.roleTemplates.map((template) => [template.id, displayName(template)]));
  const memberAgents: AgentInfo[] = [];

  for (const agentId of props.currentRoom.agents) {
    const agent = agentMap.get(agentId);
    if (agent) {
      memberAgents.push(agent);
    }
  }

  return memberAgents.map((agent) => {
    const templateName = agent.role_template_id ? (templateMap.get(agent.role_template_id) ?? null) : null;
    return {
      id: agent.id as number,
      name: agent.name,
      i18n: agent.i18n,
      employee_number: typeof agent.employee_number === 'number' ? agent.employee_number : null,
      role_template_name: templateName,
      is_leader: agent.id === currentDeptLeaderId.value,
    };
  });
});

async function handleSubmit(): Promise<void> {
  const content = draft.value.trim();
  if (!content || !props.currentRoom || !canOperatorCompose.value) {
    return;
  }

  emit('updateError', '');

  try {
    await postRoomMessage(props.currentRoom.room_id, content);
    draft.value = '';
  } catch (error) {
    emit('updateError', '消息发送失败。');
    console.error(error);
  }
}

async function handleEscalateMessage(messageId: number): Promise<void> {
  if (!props.currentRoom || escalatingMessageIds.value.includes(messageId)) {
    return;
  }

  emit('updateError', '');
  escalatingMessageIds.value = [...escalatingMessageIds.value, messageId];

  try {
    await escalateMessageToImmediate(props.currentRoom.room_id, messageId);
  } catch (error) {
    emit('updateError', t('chat.escalateFailed'));
    console.error(error);
  } finally {
    escalatingMessageIds.value = escalatingMessageIds.value.filter((id) => id !== messageId);
  }
}

function openRoomSettings(): void {
  if (!props.currentRoom) {
    return;
  }
  roomSettingsOpen.value = true;
}

function closeRoomSettings(): void {
  roomSettingsOpen.value = false;
}

function handleRoomUpdated(): void {
  emit('roomUpdated');
}

watch(
  () => props.currentRoom?.room_id ?? null,
  async () => {
    await nextTick();
    bindMessageScrollListener();
    await scrollMessagesToBottom();
  },
);

watch(
  () => props.messages.length,
  async () => {
    await nextTick();
    bindMessageScrollListener();
    if (!shouldFollowMessages.value) {
      return;
    }
    await scrollMessagesToBottom();
  },
);

onMounted(() => {
  bindMessageScrollListener();
});

onBeforeUnmount(() => {
  cleanupMessageScroll();
});
</script>

<template>
  <div ref="messageViewport" class="console-chat-panel">
    <ChatPanel
      :current-room="currentRoom"
      :member-profiles="memberProfiles"
      :messages="messages"
      :has-more-history="hasMoreHistory"
      :loading-older-messages="loadingOlderMessages"
      :error-message="errorMessage"
      :reloading-messages="reloadingMessages"
      :draft="draft"
      :composer-notice="composerNotice"
      :escalating-message-ids="escalatingMessageIds"
      @update-draft="draft = $event"
      @submit="handleSubmit"
      @click-agent="emit('clickAgent', $event)"
      @click-working-agent="emit('clickWorkingAgent', $event)"
      @load-older-messages="emit('loadOlderMessages')"
      @escalate-message="handleEscalateMessage"
      @open-room-settings="openRoomSettings"
    />
    <RoomSettingsDialog
      :open="roomSettingsOpen"
      :room="currentRoom"
      @close="closeRoomSettings"
      @updated="handleRoomUpdated"
    />
  </div>
</template>

<style scoped>
.console-chat-panel {
  flex: 1 1 auto;
  min-height: 0;
  min-width: 0;
  height: 100%;
  width: 100%;
  display: flex;
}

.console-chat-panel > * {
  flex: 1 1 auto;
  min-height: 0;
  min-width: 0;
  height: 100%;
  width: 100%;
}
</style>

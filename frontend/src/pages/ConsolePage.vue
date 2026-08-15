<script setup lang="ts">
import '../theme/legacy-aliases.css';
import { computed, onBeforeUnmount, onMounted, ref, useTemplateRef, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import { useRoute, useRouter } from 'vue-router';
import { connectionState } from '../appUiState';
import AgentActivityDialog from '../components/agent/AgentActivityDialog.vue';
import ConsoleAgentListPanel from '../components/console/ConsoleAgentListPanel.vue';
import ConsoleChatPanel from '../components/console/ConsoleChatPanel.vue';
import ConsoleRoomListPanel from '../components/console/ConsoleRoomListPanel.vue';
import ConsoleTaskTreePanel from '../components/console/ConsoleTaskTreePanel.vue';
import CreateRoomDialog from '../components/console/CreateRoomDialog.vue';
import { useAgentActivityDialogState } from '../composables/useAgentActivityDialogState';
import { useConsoleRuntimeState } from '../composables/useConsoleRuntimeState';
import { useConsoleSidebarLayout } from '../composables/useConsoleSidebarLayout';
import { loadDeptTree, loadRoleTemplates } from '../realtime/runtimeStore';
import { getAgentControlRoom } from '../api';
import { useDeptTree, useRoleTemplates } from '../realtime/selectors';
import { VIEWPORT_QUERIES } from '../responsive/breakpoints';
import { findTeamById } from '../teamStore';
import { displayName, i18nText } from '../utils';

type MobileSheetTab = 'rooms' | 'agents';
type ConsoleMainView = 'chat' | 'tasks';

const MOBILE_LAYOUT_MEDIA_QUERY = VIEWPORT_QUERIES.consoleMobile;

const route = useRoute();
const router = useRouter();
const { t } = useI18n();

const loading = ref(true);
const reloadingMessages = ref(false);
const errorMessage = ref('');
const createRoomDialogOpen = ref(false);
const isMobileLayout = ref(false);
const mobileSheetOpen = ref(false);
const mobileSheetTab = ref<MobileSheetTab>('rooms');
const runtimeRefreshToken = ref(0);
const leftStack = useTemplateRef('leftStack');

let mobileLayoutMediaQuery: MediaQueryList | null = null;

const teamId = computed(() => Number(route.params.teamId));
const routeRoomId = computed<number | null>(() => {
  const raw = route.params.roomId;
  if (typeof raw !== 'string') {
    return null;
  }
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
});
const currentTeam = computed(() => findTeamById(teamId.value));
const currentTeamLabel = computed(() => (
  currentTeam.value ? displayName(currentTeam.value) : t('topbar.selectTeam')
));
const currentConsoleView = computed<ConsoleMainView>(() => (route.query.view === 'tasks' ? 'tasks' : 'chat'));

async function navigateToRoom(roomId: number, replace = false): Promise<void> {
  const method = replace ? router.replace : router.push;
  await method({
    name: 'console',
    params: { teamId: teamId.value, roomId },
  });
}

const {
  agents,
  currentRoom,
  hasMoreHistory,
  loadingOlderMessages,
  messages,
  rooms,
  selectedRoomId,
  clearSelectedRoom,
  refreshRuntimeState,
  loadRoomMessages: loadRuntimeRoomMessages,
  loadOlderMessages,
  clearRuntimeContext,
} = useConsoleRuntimeState({
  teamId,
  routeRoomId,
  navigateToRoom,
});
const deptTree = useDeptTree(teamId);
const roleTemplates = useRoleTemplates();
const {
  leftStackStyle,
  sidebarDividerDragging,
  startSidebarResize,
} = useConsoleSidebarLayout(leftStack);
const {
  open: agentDetailOpen,
  selectedAgentId,
  selectedAgentName,
  selectedAgentStatus,
  selectedAgentTemplateName,
  openAgent,
  closeAgentDetail,
} = useAgentActivityDialogState(agents, roleTemplates);

async function handleNavigateToChat(agentId: number): Promise<void> {
  try {
    const { room_id } = await getAgentControlRoom(agentId);
    closeAgentDetail();
    await navigateToRoom(room_id);
  } catch (error) {
    errorMessage.value = String(error);
    console.error(error);
  }
}

const mobileRoomLabel = computed(() => (
  currentConsoleView.value === 'tasks'
    ? t('console.viewTasks')
    : currentRoom.value
    ? i18nText(currentRoom.value.i18n, 'display_name', currentRoom.value.room_name)
    : t('chat.noRoom')
));
const mobileRoomStatusLabel = computed(() => (
  currentConsoleView.value === 'tasks'
    ? t('console.taskTreeEyebrow')
    : currentRoom.value?.state === 'scheduling' ? t('chat.active') : t('chat.idle')
));
const mobileSheetTitle = computed(() => (
  mobileSheetTab.value === 'rooms' ? t('room.chatRooms') : t('agent.teamMembersLabel')
));

function syncMobileLayout(matches: boolean): void {
  isMobileLayout.value = matches;
  if (!matches) {
    mobileSheetOpen.value = false;
  }
}

function handleMobileLayoutChange(event: MediaQueryListEvent): void {
  syncMobileLayout(event.matches);
}

function openMobileSheet(tab: MobileSheetTab): void {
  if (!isMobileLayout.value) {
    return;
  }

  mobileSheetTab.value = tab;
  mobileSheetOpen.value = true;
}

function closeMobileSheet(): void {
  mobileSheetOpen.value = false;
}

async function loadRoomMessages(
  roomId: number,
  options?: { force?: boolean; replaceRoute?: boolean; syncRoute?: boolean },
): Promise<void> {
  if (!options?.force && selectedRoomId.value === roomId) {
    return;
  }

  reloadingMessages.value = true;
  errorMessage.value = '';

  try {
    await loadRuntimeRoomMessages(roomId, options);
  } catch (error) {
    errorMessage.value = t('console.loadFailed');
    console.error(error);
  } finally {
    reloadingMessages.value = false;
  }
}

async function refreshAll(): Promise<void> {
  if (!currentTeam.value) {
    return;
  }

  loading.value = true;
  errorMessage.value = '';

  try {
    const [{ rooms: nextRooms }] = await Promise.all([
      refreshRuntimeState(),
      loadRoleTemplates(),
      loadDeptTree(teamId.value),
    ]);

    const fallbackRoomId = nextRooms[0]?.room_id ?? null;
    const targetRoomId =
      routeRoomId.value && nextRooms.some((room) => room.room_id === routeRoomId.value)
        ? routeRoomId.value
        : fallbackRoomId;

    if (targetRoomId !== null) {
      await loadRoomMessages(targetRoomId, {
        force: true,
        replaceRoute: routeRoomId.value !== targetRoomId,
        syncRoute: true,
      });
    } else {
      clearSelectedRoom();
    }

    runtimeRefreshToken.value += 1;
  } catch (error) {
    errorMessage.value = t('console.connectFailed');
    console.error(error);
  } finally {
    loading.value = false;
  }
}

function openCreateRoomDialog(): void {
  if (loading.value || !currentTeam.value) {
    return;
  }

  closeMobileSheet();
  createRoomDialogOpen.value = true;
}

function closeCreateRoomDialog(): void {
  createRoomDialogOpen.value = false;
}

async function handleSelectRoom(roomId: number): Promise<void> {
  await loadRoomMessages(roomId, { force: true });
  closeMobileSheet();
}

function handleSelectAgent(agentId: number): void {
  closeMobileSheet();
  openAgent(agentId);
}

watch(
  () => currentTeam.value?.name,
  (teamName, previousTeamName) => {
    if (!teamName || teamName === previousTeamName) {
      return;
    }
    refreshAll().catch(console.error);
  },
);

watch(
  () => routeRoomId.value,
  (roomId) => {
    if (roomId === null || roomId === selectedRoomId.value) {
      return;
    }
    if (!rooms.value.some((room) => room.room_id === roomId)) {
      return;
    }
    loadRoomMessages(roomId, { force: true, syncRoute: false }).catch(console.error);
  },
);

watch(
  () => connectionState.value,
  (state, previousState) => {
    if (
      !currentTeam.value
      || state !== 'connected'
      || previousState === 'connected'
      || previousState === 'connecting'
    ) {
      return;
    }
    refreshAll().catch(console.error);
  },
);

onMounted(async () => {
  if (typeof window !== 'undefined' && 'matchMedia' in window) {
    mobileLayoutMediaQuery = window.matchMedia(MOBILE_LAYOUT_MEDIA_QUERY);
    syncMobileLayout(mobileLayoutMediaQuery.matches);
    mobileLayoutMediaQuery.addEventListener('change', handleMobileLayoutChange);
  }

  if (currentTeam.value) {
    await refreshAll();
  }
});

onBeforeUnmount(() => {
  if (mobileLayoutMediaQuery) {
    mobileLayoutMediaQuery.removeEventListener('change', handleMobileLayoutChange);
    mobileLayoutMediaQuery = null;
  }

  clearRuntimeContext();
});
</script>

<template>
  <div class="workspace-grid" :class="{ 'workspace-grid-mobile': isMobileLayout }">
    <div v-if="!isMobileLayout" ref="leftStack" class="left-stack" :style="leftStackStyle">
      <ConsoleRoomListPanel
        :team-id="teamId"
        :loading="loading"
        :current-room-id="selectedRoomId"
        @select-room="handleSelectRoom"
        @create-room="openCreateRoomDialog"
      />

      <button
        type="button"
        class="left-stack-splitter"
        :class="{ dragging: sidebarDividerDragging }"
        aria-label="调整聊天室和成员卡片高度"
        @pointerdown="startSidebarResize"
      >
        <span class="splitter-grip"></span>
      </button>

      <ConsoleAgentListPanel :team-id="teamId" @select-agent="openAgent" />
    </div>

    <div class="chat-pane" :class="{ 'chat-pane-mobile': isMobileLayout }">
      <div class="chat-pane-shell">
        <section v-if="isMobileLayout" class="mobile-console-bar panel">
          <button type="button" class="mobile-room-chip" @click="openMobileSheet('rooms')">
            <span class="mobile-room-chip__eyebrow">{{ currentTeamLabel }}</span>
            <strong>{{ mobileRoomLabel }}</strong>
            <span class="mobile-room-chip__meta">
              <span>
                {{
                  currentConsoleView === 'tasks'
                    ? t('console.taskTreeEyebrow')
                    : currentRoom
                      ? t('room.membersCount', { count: currentRoom.agents.length })
                      : t('console.mobileSelectRoom')
                }}
              </span>
              <span v-if="currentConsoleView === 'tasks' || currentRoom">{{ mobileRoomStatusLabel }}</span>
            </span>
          </button>

          <div class="mobile-console-actions">
            <button
              type="button"
              class="mobile-console-action"
              :class="{ active: mobileSheetOpen && mobileSheetTab === 'rooms' }"
              @click="openMobileSheet('rooms')"
            >
              <span>{{ t('room.chatRooms') }}</span>
              <strong>{{ rooms.length }}</strong>
            </button>
            <button
              type="button"
              class="mobile-console-action"
              :class="{ active: mobileSheetOpen && mobileSheetTab === 'agents' }"
              @click="openMobileSheet('agents')"
            >
              <span>{{ t('agent.teamMembersLabel') }}</span>
              <strong>{{ agents.length }}</strong>
            </button>
          </div>
        </section>

        <ConsoleChatPanel
          v-if="currentConsoleView === 'chat'"
          :current-room="currentRoom"
          :agents="agents"
          :dept-tree="deptTree"
          :role-templates="roleTemplates"
          :messages="messages"
          :has-more-history="hasMoreHistory"
          :loading-older-messages="loadingOlderMessages"
          :error-message="errorMessage"
          :reloading-messages="reloadingMessages"
          :team-enabled="currentTeam?.enabled ?? true"
          @update-error="errorMessage = $event"
          @click-agent="openAgent"
          @click-working-agent="openAgent"
          @load-older-messages="loadOlderMessages().catch(console.error)"
          @room-updated="refreshAll().catch(console.error)"
        />

        <ConsoleTaskTreePanel
          v-else
          :team-id="teamId"
          :active="currentConsoleView === 'tasks'"
          :agents="agents"
          :refresh-token="runtimeRefreshToken"
        />
      </div>
    </div>

    <Teleport to="body">
      <div
        v-if="isMobileLayout && mobileSheetOpen"
        class="mobile-console-sheet-backdrop"
        @click.self="closeMobileSheet"
      >
        <section class="mobile-console-sheet panel">
          <span class="mobile-console-sheet__grabber" aria-hidden="true"></span>
          <div class="mobile-console-sheet__head">
            <div class="mobile-console-sheet__title-wrap">
              <p class="mobile-console-sheet__eyebrow">{{ currentTeamLabel }}</p>
              <h2>{{ mobileSheetTitle }}</h2>
            </div>
            <button type="button" class="mobile-console-sheet__close" @click="closeMobileSheet">
              {{ t('common.close') }}
            </button>
          </div>

          <div class="mobile-console-sheet__tabs">
            <button
              type="button"
              class="mobile-console-sheet__tab"
              :class="{ active: mobileSheetTab === 'rooms' }"
              @click="mobileSheetTab = 'rooms'"
            >
              <span>{{ t('room.chatRooms') }}</span>
              <strong>{{ rooms.length }}</strong>
            </button>
            <button
              type="button"
              class="mobile-console-sheet__tab"
              :class="{ active: mobileSheetTab === 'agents' }"
              @click="mobileSheetTab = 'agents'"
            >
              <span>{{ t('agent.teamMembersLabel') }}</span>
              <strong>{{ agents.length }}</strong>
            </button>
          </div>

          <div class="mobile-console-sheet__body">
            <ConsoleRoomListPanel
              v-if="mobileSheetTab === 'rooms'"
              :team-id="teamId"
              :loading="loading"
              :current-room-id="selectedRoomId"
              @select-room="handleSelectRoom"
              @create-room="openCreateRoomDialog"
            />

            <ConsoleAgentListPanel
              v-else
              :team-id="teamId"
              @select-agent="handleSelectAgent"
            />
          </div>
        </section>
      </div>
    </Teleport>

    <CreateRoomDialog :open="createRoomDialogOpen" @close="closeCreateRoomDialog" />

    <AgentActivityDialog
      :open="agentDetailOpen"
      :agent-id="selectedAgentId"
      :agent-name="selectedAgentName"
      :agent-status="selectedAgentStatus"
      :role-template-name="selectedAgentTemplateName"
      @close="closeAgentDetail"
      @navigate-to-chat="handleNavigateToChat"
    />
  </div>
</template>

<style scoped>
.workspace-grid {
  display: grid;
  grid-template-columns: 320px minmax(0, 1fr);
  gap: 8px;
  min-height: 0;
  height: 100%;
  overflow: hidden;
}

.left-stack {
  display: grid;
  min-height: 0;
  min-width: 0;
}

.left-stack-splitter {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: none;
  background: transparent;
  cursor: row-resize;
  touch-action: none;
}

.splitter-grip {
  width: 100%;
  height: 2px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--border-default) 55%, transparent);
  opacity: 0;
  transition:
    opacity 0.18s ease,
    transform 0.18s ease;
}

.left-stack-splitter:hover .splitter-grip {
  opacity: 0;
}

.left-stack-splitter.dragging .splitter-grip {
  opacity: 0.22;
  transform: scaleY(1.2);
}

.chat-pane {
  min-height: 0;
  min-width: 0;
  height: 100%;
  display: flex;
  overflow: hidden;
}

.chat-pane-shell {
  flex: 1 1 auto;
  min-height: 0;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.chat-pane-shell > :last-child {
  flex: 1 1 auto;
  min-height: 0;
}

.mobile-console-bar {
  display: none;
}

.mobile-console-sheet-backdrop {
  position: fixed;
  inset: 0;
  z-index: 58;
  display: none;
}

:global(html.bp-layout-narrow) .workspace-grid {
  grid-template-columns: 1fr;
  grid-template-rows: minmax(280px, 38vh) minmax(0, 1fr);
}

:global(html.bp-layout-narrow) .left-stack {
  min-height: 0;
}

:global(html.bp-console-mobile) .workspace-grid,
:global(html.bp-console-mobile) .workspace-grid-mobile {
  grid-template-columns: 1fr;
  grid-template-rows: minmax(0, 1fr);
  gap: 8px;
  height: 100%;
  min-height: 100%;
  overflow: hidden;
}

:global(html.bp-console-mobile) .chat-pane-mobile {
  height: 100%;
  min-height: 0;
  overflow: hidden;
}

:global(html.bp-console-mobile) .chat-pane-shell {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  height: 100%;
  min-height: 0;
  gap: 6px;
}

:global(html.bp-console-mobile) .chat-pane-shell > :last-child {
  min-height: 0;
}

:global(html.bp-console-mobile) .mobile-console-bar {
  display: grid;
  gap: 8px;
  padding: 8px;
  border-radius: 16px;
  border-color: color-mix(in srgb, var(--interactive-focus-border) 16%, var(--border-default) 84%);
  background:
    linear-gradient(180deg, color-mix(in srgb, var(--surface-panel) 92%, var(--surface-pill) 8%), color-mix(in srgb, var(--surface-panel) 96%, black 4%));
}

:global(html.bp-console-mobile) .mobile-room-chip,
:global(html.bp-console-mobile) .mobile-console-action,
:global(html.bp-console-mobile) .mobile-console-sheet__tab,
:global(html.bp-console-mobile) .mobile-console-sheet__close {
  appearance: none;
}

:global(html.bp-console-mobile) .mobile-room-chip {
  width: 100%;
  display: grid;
  gap: 4px;
  padding: 10px 12px;
  border: 1px solid color-mix(in srgb, var(--interactive-focus-border) 18%, var(--border-default) 82%);
  border-radius: 14px;
  background:
    linear-gradient(145deg, color-mix(in srgb, var(--surface-overlay) 94%, var(--surface-pill) 6%), color-mix(in srgb, var(--surface-panel) 96%, black 4%));
  color: inherit;
  text-align: left;
  box-shadow: 0 8px 20px rgba(3, 14, 24, 0.1);
}

:global(html.bp-console-mobile) .mobile-room-chip__eyebrow,
:global(html.bp-console-mobile) .mobile-console-sheet__eyebrow {
  margin: 0;
  color: var(--text-secondary);
  font-size: 0.68rem;
  letter-spacing: 0.08em;
}

:global(html.bp-console-mobile) .mobile-room-chip strong {
  color: var(--text-primary);
  font-size: 0.98rem;
  line-height: 1.2;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

:global(html.bp-console-mobile) .mobile-room-chip__meta {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
  color: var(--text-secondary);
  font-size: 0.72rem;
}

:global(html.bp-console-mobile) .mobile-console-actions,
:global(html.bp-console-mobile) .mobile-console-sheet__tabs {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

:global(html.bp-console-mobile) .mobile-console-action,
:global(html.bp-console-mobile) .mobile-console-sheet__tab {
  min-height: 44px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 0 12px;
  border: 1px solid color-mix(in srgb, var(--border-default) 82%, transparent);
  border-radius: 12px;
  background: color-mix(in srgb, var(--surface-panel-muted) 74%, var(--surface-panel) 26%);
  color: var(--text-secondary);
  cursor: pointer;
  transition:
    border-color 0.18s ease,
    background 0.18s ease,
    color 0.18s ease,
    transform 0.18s ease;
}

:global(html.bp-console-mobile) .mobile-console-action strong,
:global(html.bp-console-mobile) .mobile-console-sheet__tab strong {
  color: var(--text-primary);
  font-size: 0.96rem;
  line-height: 1;
}

:global(html.bp-console-mobile) .mobile-console-action.active,
:global(html.bp-console-mobile) .mobile-console-action:hover,
:global(html.bp-console-mobile) .mobile-console-sheet__tab.active {
  border-color: color-mix(in srgb, var(--interactive-focus-border) 44%, var(--border-default) 56%);
  background: color-mix(in srgb, var(--interactive-selected) 74%, var(--surface-panel) 26%);
  color: var(--text-primary);
  transform: translateY(-1px);
}

:global(html.bp-console-mobile) .mobile-console-sheet-backdrop {
  display: flex;
  align-items: flex-end;
  padding: 12px;
  background: rgba(4, 12, 20, 0.42);
  backdrop-filter: blur(10px);
}

:global(html.bp-console-mobile) .mobile-console-sheet {
  width: min(100%, 760px);
  max-height: min(76vh, 720px);
  margin: 0 auto;
  display: grid;
  grid-template-rows: auto auto minmax(0, 1fr);
  gap: 12px;
  padding: 12px 12px calc(14px + env(safe-area-inset-bottom, 0px));
  border-radius: 24px 24px 18px 18px;
  border-color: color-mix(in srgb, var(--interactive-focus-border) 18%, var(--border-default) 82%);
  background:
    linear-gradient(180deg, color-mix(in srgb, var(--surface-overlay) 94%, var(--surface-pill) 6%), color-mix(in srgb, var(--surface-panel) 92%, black 8%));
  box-shadow: 0 22px 54px rgba(4, 14, 24, 0.3);
  overflow: hidden;
  animation: mobile-sheet-rise 0.22s ease-out;
}

:global(html.bp-console-mobile) .mobile-console-sheet__grabber {
  width: 46px;
  height: 5px;
  margin: 0 auto;
  border-radius: 999px;
  background: color-mix(in srgb, var(--border-default) 50%, transparent);
}

:global(html.bp-console-mobile) .mobile-console-sheet__head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: flex-start;
}

:global(html.bp-console-mobile) .mobile-console-sheet__title-wrap {
  min-width: 0;
}

:global(html.bp-console-mobile) .mobile-console-sheet__title-wrap h2 {
  margin: 4px 0 0;
  color: var(--text-primary);
  font-size: 1.08rem;
  line-height: 1.2;
}

:global(html.bp-console-mobile) .mobile-console-sheet__close {
  border: 1px solid var(--border-default);
  border-radius: 999px;
  background: color-mix(in srgb, var(--surface-pill) 88%, var(--surface-panel) 12%);
  color: var(--text-secondary);
  min-height: 34px;
  padding: 0 13px;
  cursor: pointer;
}

:global(html.bp-console-mobile) .mobile-console-sheet__close:hover {
  border-color: var(--interactive-focus-border);
  color: var(--text-primary);
}

:global(html.bp-console-mobile) .mobile-console-sheet__body {
  min-height: 0;
  overflow: hidden;
}

:global(html.bp-console-mobile) .mobile-console-sheet__body > * {
  height: 100%;
}

:global(html.bp-compact) .mobile-room-chip__meta {
  flex-wrap: wrap;
  justify-content: flex-start;
}

:global(html.bp-compact) .mobile-console-sheet-backdrop {
  padding: 0;
  align-items: flex-end;
}

:global(html.bp-compact) .mobile-console-sheet {
  width: 100%;
  max-height: min(80vh, 780px);
  border-radius: 22px 22px 0 0;
  padding-left: 10px;
  padding-right: 10px;
}

:global(html.bp-console-short) .workspace-grid,
:global(html.bp-console-short) .workspace-grid-mobile {
  gap: 4px;
}

:global(html.bp-console-short) .chat-pane-shell {
  gap: 6px;
}

:global(html.bp-console-short) .mobile-console-bar {
  gap: 0;
  padding: 0;
  border: none;
  background: transparent;
  box-shadow: none;
}

:global(html.bp-console-short) .mobile-room-chip {
  display: none;
}

:global(html.bp-console-short) .mobile-console-actions {
  gap: 4px;
}

:global(html.bp-console-short) .mobile-console-action {
  min-height: 34px;
  padding: 0 8px;
  border-radius: 9px;
  font-size: 0.76rem;
}

:global(html.bp-console-short) .mobile-console-action strong {
  font-size: 0.88rem;
}

@keyframes mobile-sheet-rise {
  from {
    opacity: 0;
    transform: translateY(18px);
  }

  to {
    opacity: 1;
    transform: translateY(0);
  }
}
</style>

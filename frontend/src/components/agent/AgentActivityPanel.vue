<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import { escalateMessageToImmediate, getAgentActivitiesPage } from '../../api';
import { connectionState } from '../../appUiState';
import { useAgentActivities } from '../../realtime/selectors';
import { useStickToBottom } from '../../composables/useStickToBottom';
import { useAgentPendingMessages } from '../../composables/useAgentPendingMessages';
import type { AgentActivity } from '../../types';
import AgentActivityItem from './AgentActivityItem.vue';
import PendingMessageDock from '../chat/pending/PendingMessageDock.vue';

const props = defineProps<{
  open: boolean;
  agentId: number | null;
  /** 该 Agent 所属 team，用于定位控制房与读游标。 */
  teamId?: number | null;
}>();

const emit = defineEmits<{
  followChange: [boolean];
}>();

const { t } = useI18n();

const activityListRef = ref<HTMLElement | null>(null);
const activityContentRef = ref<HTMLElement | null>(null);
const loadedActivities = ref<AgentActivity[]>([]);
const activitiesLoading = ref(false);
const activitiesLoadingOlder = ref(false);
const activitiesErrorMessage = ref('');
const activitiesHasMore = ref(false);
const ACTIVITY_PAGE_SIZE = 50;
let activityRequestToken = 0;

/**
 * 自动贴底跟随。
 * 以前用 `programmaticScroll` 布尔 + 50ms 定时器去猜「这次 scroll 是谁造成的」，会被程序
 * 滚动和浏览器 scroll anchoring 补偿污染；一旦误判就把跟随永久关掉、且没有自愈路径。
 * 现在意图只看用户输入事件（wheel / touch / 键盘 / 位置被向上拖动），事实只看
 * distanceToBottom，两者分离（见 composable 顶部注释）。
 */
const stickToBottom = useStickToBottom({
  onFollowChange: (value) => emit('followChange', value),
});
const activityFollowing = stickToBottom.following;
const hasNewActivitiesBelow = stickToBottom.hasNewContent;

/** 仅用于「往上滚到顶就加载更早一页」，与跟随状态机无关。 */
let lastActivityScrollTop = 0;

const realtimeActivities = useAgentActivities(() => props.agentId);

/**
 * 待读消息（控制房里发给这个 Agent、它还没读到的）。
 *
 * 数据源独立于活动列表：待读条目不是 Activity，id 空间也不同，混进 `visibleActivities`
 * 会污染排序和分页。这里只负责把算好的展示数据交给 `PendingMessageDock`。
 */
const pendingState = useAgentPendingMessages({
  agentId: () => props.agentId,
  teamId: () => props.teamId ?? null,
  open: () => props.open,
});
const pendingItems = pendingState.pendingItems;
const escalatingPendingIds = ref<number[]>([]);

/**
 * 待读条目增减也计入贴底跟随。
 *
 * 待读区挂在滚动容器外面、输入框上方，它一变高就把活动列表的视口压矮；贴底控制器
 * 观察的是视口自身尺寸，本来就能感知到。这里再主动通知一次，是为了在视口 RO 回调
 * 之前（同一次渲染里）就把位置补到底，避免出现一帧的可见跳动。
 */
watch(
  () => pendingItems.value.length,
  () => {
    stickToBottom.notifyContentChanged();
  },
  { flush: 'post' },
);

async function handlePendingEscalate(dbId: number): Promise<void> {
  const roomId = pendingState.controlRoomId.value;
  if (roomId === null || escalatingPendingIds.value.includes(dbId)) {
    return;
  }
  escalatingPendingIds.value = [...escalatingPendingIds.value, dbId];
  try {
    await escalateMessageToImmediate(roomId, dbId);
  } catch (error) {
    console.error(error);
  } finally {
    escalatingPendingIds.value = escalatingPendingIds.value.filter((id) => id !== dbId);
  }
}

/**
 * 供 `AgentActivityDialog` 在 `sendSupervise` 成功后调用：第一次督导时控制房是后端刚建的，
 * 这时把返回的 `room_id` 直接告诉面板，待读区才能立刻接上。
 */
function notifyControlRoom(roomId: number): void {
  pendingState.notifyControlRoom(roomId);
}

defineExpose({ notifyControlRoom });

function sortActivitiesAscending(items: AgentActivity[]): AgentActivity[] {
  return [...items].sort((left, right) => left.id - right.id);
}

function mergeActivityCollections(...groups: AgentActivity[][]): AgentActivity[] {
  const merged = new Map<number, AgentActivity>();
  for (const group of groups) {
    for (const activity of group) {
      merged.set(activity.id, activity);
    }
  }
  return sortActivitiesAscending([...merged.values()]);
}

const visibleActivities = computed(() =>
  mergeActivityCollections(loadedActivities.value, realtimeActivities.value)
    .filter((activity) => activity.activity_type !== 'agent_state'),
);

async function scrollActivitiesToBottom(): Promise<void> {
  await nextTick();
  stickToBottom.scrollToLatest();
}

async function loadActivities(): Promise<void> {
  if (!props.open || props.agentId === null) {
    loadedActivities.value = [];
    activitiesHasMore.value = false;
    activitiesErrorMessage.value = '';
    activitiesLoadingOlder.value = false;
    activitiesLoading.value = false;
    lastActivityScrollTop = 0;
    return;
  }

  const requestToken = ++activityRequestToken;
  activitiesLoading.value = true;
  activitiesErrorMessage.value = '';
  activitiesLoadingOlder.value = false;
  loadedActivities.value = [];
  activitiesHasMore.value = false;
  lastActivityScrollTop = 0;

  try {
    const page = await getAgentActivitiesPage(props.agentId, { limit: ACTIVITY_PAGE_SIZE });
    if (requestToken !== activityRequestToken) {
      return;
    }
    loadedActivities.value = sortActivitiesAscending(page.activities);
    activitiesHasMore.value = page.hasMore;
    await scrollActivitiesToBottom();
  } catch (error) {
    if (requestToken !== activityRequestToken) {
      return;
    }
    activitiesErrorMessage.value = t('agent.loadFailed');
    console.error(error);
  } finally {
    if (requestToken === activityRequestToken) {
      activitiesLoading.value = false;
    }
  }
}

async function loadOlderActivities(): Promise<void> {
  if (
    !props.open
    || props.agentId === null
    || activitiesLoading.value
    || activitiesLoadingOlder.value
    || !activitiesHasMore.value
    || loadedActivities.value.length === 0
  ) {
    return;
  }

  const oldestLoadedId = loadedActivities.value[0]?.id;
  const listEl = activityListRef.value;
  if (!oldestLoadedId || !listEl) {
    return;
  }

  const requestToken = activityRequestToken;
  const previousScrollHeight = listEl.scrollHeight;
  const previousScrollTop = listEl.scrollTop;
  activitiesLoadingOlder.value = true;

  try {
    const page = await getAgentActivitiesPage(props.agentId, {
      limit: ACTIVITY_PAGE_SIZE,
      beforeId: oldestLoadedId,
    });
    if (requestToken !== activityRequestToken) {
      return;
    }

    if (page.activities.length > 0) {
      loadedActivities.value = mergeActivityCollections(page.activities, loadedActivities.value);
      await nextTick();
      if (activityListRef.value === listEl) {
        // 一次性位置补偿：把新增内容的等量高度补回 scrollTop，视线不跳。
        // 这是「向上翻页」的局部修正，不参与跟随状态机（此时本来就是 IDLE）。
        listEl.scrollTop = listEl.scrollHeight - previousScrollHeight + previousScrollTop;
        lastActivityScrollTop = listEl.scrollTop;
      }
    }

    activitiesHasMore.value = page.hasMore;
  } catch (error) {
    if (requestToken !== activityRequestToken) {
      return;
    }
    if (loadedActivities.value.length === 0 && realtimeActivities.value.length === 0) {
      activitiesErrorMessage.value = t('agent.loadFailed');
    }
    console.error(error);
  } finally {
    if (requestToken === activityRequestToken) {
      activitiesLoadingOlder.value = false;
    }
  }
}

function handleActivityListScroll(): void {
  const listEl = activityListRef.value;
  if (!listEl) {
    return;
  }
  const currentScrollTop = listEl.scrollTop;
  const scrollingUp = currentScrollTop < lastActivityScrollTop;
  lastActivityScrollTop = currentScrollTop;
  if (scrollingUp && currentScrollTop <= 24) {
    loadOlderActivities().catch(console.error);
  }
}

watch(
  () => [props.open, props.agentId] as const,
  () => {
    stickToBottom.scrollToLatest();
    loadActivities().catch(console.error);
  },
  { immediate: true },
);

watch(
  () => connectionState.value,
  (state, previousState) => {
    if (
      !props.open
      || props.agentId === null
      || state !== 'connected'
      || previousState === 'connected'
      || previousState === 'connecting'
    ) {
      return;
    }
    loadActivities().catch(console.error);
  },
);

// 列表元素会在 loading / empty / 正常三态间出现和消失，出现时重新绑定跟随控制器。
watch(
  () => [activityListRef.value, activityContentRef.value] as const,
  ([listEl, contentEl]) => {
    stickToBottom.bind(listEl ?? null, contentEl ?? null);
  },
  { flush: 'post', immediate: true },
);
</script>

<template>
  <div class="agent-activity-panel-body">
    <div v-if="activitiesErrorMessage" class="error-banner">{{ activitiesErrorMessage }}</div>
    <div v-else-if="activitiesLoading && !visibleActivities.length" class="loading-card">{{ t('agent.loadingActivities') }}</div>
    <!--
      空态只在「既没有活动、也没有待读」时出现：督导后第一帧活动表还是空的，但待读卡片
      已经在下面了，这时说「暂无活动记录」会自相矛盾。
    -->
    <div
      v-else-if="!activitiesLoading && !visibleActivities.length && !pendingItems.length"
      class="agent-activity-empty"
    >
      {{ t('agent.noActivities') }}
    </div>
    <div v-else-if="!visibleActivities.length" class="agent-activity-list-spacer"></div>
    <div
      v-else
      ref="activityListRef"
      class="agent-activity-list sidebar-scroll"
      :class="{ 'agent-activity-list--with-pending': pendingItems.length > 0 }"
      :data-activity-follow="activityFollowing ? '1' : '0'"
      @scroll="handleActivityListScroll"
    >
      <div ref="activityContentRef" class="agent-activity-list__content">
        <div v-if="activitiesLoadingOlder" class="agent-activity-list__loading-more">
          {{ t('agent.loadingEarlierActivities') }}
        </div>
        <AgentActivityItem
          v-for="activity in visibleActivities"
          :key="activity.id"
          :activity="activity"
        />
      </div>
    </div>
    <button
      v-if="hasNewActivitiesBelow"
      type="button"
      class="agent-activity-new-below"
      @click="stickToBottom.scrollToLatest()"
    >{{ t('agent.newActivitiesBelow') }}</button>
    <!--
      待读区固定在活动列表与输入框之间：它不在列表的滚动容器里，所以不会被历史分页影响，
      也不会随列表一起滚走。条目标题、展开、折叠、限高全由共用组件负责。
    -->
    <PendingMessageDock
      v-if="pendingItems.length"
      class="agent-activity-pending"
      :items="pendingItems"
      :escalating-ids="escalatingPendingIds"
      @escalate="handlePendingEscalate"
    />
  </div>
</template>

<style scoped>
.agent-activity-panel-body {
  position: relative;
  min-height: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
}

.agent-activity-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: visible;
  padding: 0;
  scroll-padding-bottom: 16px;
  box-sizing: border-box;
}

.agent-activity-list__content {
  display: flex;
  flex-direction: column;
  gap: 4px;
  /* 底部 6px：supervise 分隔线定在自身顶部 -2px，最后一条卡片到分隔线的视觉间距
     正好是 6 - 2 = 4px，与条目之间的 gap 一致。不能再小，否则会贴线。 */
  padding: 4px 0 6px;
}

/* 有待读条紧跟其后时收窄到条目间距：此时下方是待读条而不是分隔线。 */
.agent-activity-list--with-pending .agent-activity-list__content {
  padding-bottom: 4px;
}

.agent-activity-list__loading-more {
  padding: 4px 0 6px;
  color: var(--text-secondary);
  font-size: 0.72rem;
  text-align: center;
}

/*
 * 待读区：贴在活动列表下方、输入框上方。
 *
 * 间距必须与列表内部同一个节奏（条目之间 4px），所以要把 dock 自带的内边距
 * （8px 上 / 2px 下）清零，否则叠加后比列表条目之间的缝隙大一截。
 * `.agent-activity-pending` 与 `.pending-message-dock` 是同一个元素，组合选择器
 * 提高优先级才能压过子组件自己的 scoped 样式。
 */
.agent-activity-pending.agent-activity-pending {
  flex-shrink: 0;
  /* 不能留左右外边距：活动条目是通栏的，留 2px 会让待读条右边短一截。 */
  margin: 0 0 8px;
  padding: 0;
}

/*
 * 待读卡片与活动条目同属一个列表语境，外观必须一致：`PendingMessageBar` 的默认样式
 * 是给聊天页「悬浮待读条」用的（14px 圆角 + `--chat-floating-message-bg`），放进活动
 * 列表会像另一套设计。这里只在活动面板语境下把它对齐 `.agent-activity-item` 的口径，
 * 聊天页不受影响。
 */
.agent-activity-pending :deep(.pending-message-bar) {
  padding: 6px 8px;
  border-radius: 10px;
  background: var(--surface-activity-item);
  border-color: var(--border-activity-item);
}

.agent-activity-pending :deep(.pending-message-dock__list) {
  gap: 4px;
  /* `.sidebar-scroll` 自带 4px 右内边距（给滚动条让位），但活动列表这边已被
     `.agent-activity-list` 的 `padding: 0` 覆盖。这里也清零，待读条才能和
     活动条目、输入分隔线右缘对齐，否则右边短 4px。 */
  padding-right: 0;
}

/* IDLE 状态下新内容到达时的可见出口：点一下回到底部并恢复跟随。 */
.agent-activity-new-below {
  position: absolute;
  left: 50%;
  bottom: 10px;
  transform: translateX(-50%);
  z-index: 5;
  padding: 4px 10px;
  border-radius: 999px;
  border: 1px solid var(--panel-border);
  background: var(--surface-elevated);
  color: var(--text-strong);
  font-size: 0.72rem;
  font-weight: 600;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.22);
  cursor: pointer;
}

.agent-activity-new-below:hover {
  border-color: var(--border-subtle, var(--panel-border));
  color: var(--accent, var(--text-strong));
}

.agent-activity-empty {
  min-height: 120px;
  display: grid;
  place-items: center;
  color: var(--muted);
  margin: 0 0 10px;
}

/* 有未读但还没有活动：占住列表区剩余空间，把待读区顶到底部。 */
.agent-activity-list-spacer {
  flex: 1;
  min-height: 0;
}

.loading-card,
.error-banner {
  padding: 14px;
  margin: 0 0 10px;
}

.loading-card {
  border: 1px solid var(--panel-border);
  border-radius: 14px;
  background: var(--surface-soft);
}

.error-banner {
  border-radius: 10px;
  background: var(--banner-error-bg);
  color: var(--banner-error-text);
  border: 1px solid var(--banner-error-border);
}
</style>

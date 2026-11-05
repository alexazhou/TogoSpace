<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import { getAgentActivitiesPage } from '../../api';
import { connectionState } from '../../appUiState';
import { useAgentActivities } from '../../realtime/selectors';
import { useStickToBottom } from '../../composables/useStickToBottom';
import type { AgentActivity } from '../../types';
import AgentActivityItem from './AgentActivityItem.vue';

const props = defineProps<{
  open: boolean;
  agentId: number | null;
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
    <div v-else-if="!activitiesLoading && !visibleActivities.length" class="agent-activity-empty">
      {{ t('agent.noActivities') }}
    </div>
    <div
      v-else
      ref="activityListRef"
      class="agent-activity-list sidebar-scroll"
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
  padding: 4px 0 10px;
}

.agent-activity-list__loading-more {
  padding: 4px 0 6px;
  color: var(--text-secondary);
  font-size: 0.72rem;
  text-align: center;
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

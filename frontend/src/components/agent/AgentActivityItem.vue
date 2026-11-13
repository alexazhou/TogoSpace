<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import { getTeamRooms } from '../../realtime/runtimeStore';
import type { AgentActivity } from '../../types';
import { useResizeObserver } from '../../composables/useResizeObserver';
import AgentActivityToken from './AgentActivityToken.vue';
import ActivityBashResult from './activity/ActivityBashResult.vue';
import ActivityChatReply from './activity/ActivityChatReply.vue';
import ActivityReceivedMessages from './activity/ActivityReceivedMessages.vue';
import ActivityToolResult from './activity/ActivityToolResult.vue';
import {
  buildActivityView,
  resolveTaskRoomNameFromRooms,
  type Translate,
} from './activity/activityViewModel';

const { t } = useI18n();

const props = defineProps<{
  activity: AgentActivity;
}>();

// 发言内容（send_chat_msg / chat_reply）默认展开，长内容仍可手动收起。
const chatReplyCollapsed = ref(false);
// 思考内容默认只露出三行，可展开看全文。
const reasoningCollapsed = ref(true);
const reasoningBodyRef = ref<HTMLElement | null>(null);
const reasoningOverflows = ref(false);

/**
 * 判断思考内容是否确实被三行裁掉了（决定要不要给「展开」按钮）。
 *
 * 有布局时按真实溢出判断；无布局环境（jsdom / 首帧）`clientHeight` 为 0，
 * 退回字数启发式，避免出现「被裁掉却没有展开入口」的情况。
 */
function measureReasoningOverflow(): void {
  const el = reasoningBodyRef.value;
  if (!el) {
    reasoningOverflows.value = false;
    return;
  }
  // 展开态不做判定：此时 scrollHeight == clientHeight，会误把按钮撤掉，导致收不回去。
  if (!reasoningCollapsed.value) {
    return;
  }
  if (el.clientHeight === 0) {
    const content = props.activity.activity_type === 'reasoning' ? props.activity.detail.trim() : '';
    reasoningOverflows.value = content.split('\n').length > 3 || content.length > 120;
    return;
  }
  reasoningOverflows.value = el.scrollHeight - el.clientHeight > 1;
}

function toggleReasoning(): void {
  reasoningCollapsed.value = !reasoningCollapsed.value;
}

// 思考内容块是 `v-if` 渲染的、节点会换新，所以交给 `useResizeObserver` 跟随 ref：
// ref 换成新节点时会自动重挂，组件卸载时自动清理。
useResizeObserver(reasoningBodyRef, () => measureReasoningOverflow());

watch(
  [() => props.activity, reasoningBodyRef, reasoningCollapsed],
  async () => {
    await nextTick();
    measureReasoningOverflow();
  },
  { immediate: true },
);

// 视图模型全部搬到 activity/activityViewModel.ts（纯函数、可单测）；
// 这里只注入 i18n 与「房间名解析」两件外部依赖。
const activityView = computed(() => buildActivityView({
  activity: props.activity,
  t: t as unknown as Translate,
  resolveTaskRoomName: (teamId, taskRoomId) =>
    resolveTaskRoomNameFromRooms(getTeamRooms(teamId), taskRoomId),
}));
</script>

<template>
  <article
    class="agent-activity-item"
    :class="{
      'agent-activity-item--expanded': activityView.expandedContent,
      'agent-activity-item--message': activityView.expandedMessage,
      'agent-activity-item--tool-result': activityView.expandedToolResult,
      'agent-activity-item--bash-result': activityView.executeBashResult,
    }"
    :data-status="activity.status"
    :data-activity-type="activity.activity_type"
  >
    <!-- 思考条目不留「思考」标题文字（💭 已足够标识），但对勾与正文同行保留，
         和其它卡片一致。内容为空时退回显示标题，避免出现空白卡片。 -->
    <div
      class="agent-activity-item__row"
      :class="{ 'agent-activity-item__row--reasoning': Boolean(activityView.reasoningContent) }"
    >
      <span class="agent-activity-item__state-anchor" tabindex="0">
        <span class="agent-activity-item__state" :data-status="activity.status">{{ activityView.stateSymbol }}</span>
        <span class="agent-activity-item__state-popover">
          <span class="agent-activity-item__state-row">
            <span class="agent-activity-item__state-row-left">
              <span class="agent-activity-item__state-title">{{ activityView.title }}</span>
              <span class="agent-activity-item__state-meta agent-activity-item__state-meta--strong">{{ activityView.durationText }}</span>
            </span>
            <span class="agent-activity-item__state-meta">{{ activityView.startedAtText }}</span>
          </span>
          <span v-if="activityView.exitCode" class="agent-activity-item__state-extra">
            {{ activityView.exitCode }}
          </span>
        </span>
      </span>
      <div
        v-if="activityView.reasoningContent"
        ref="reasoningBodyRef"
        class="agent-activity-item__reasoning"
        :class="{ 'agent-activity-item__reasoning--collapsed': reasoningCollapsed }"
      ><span class="agent-activity-item__reasoning-mark" aria-hidden="true">💭</span>{{ activityView.reasoningContent }}</div>
      <strong v-if="activityView.inlineTitle" class="agent-activity-item__title">{{ activityView.title }}</strong>
      <span
        v-if="activityView.showToolName"
        class="agent-activity-item__chip agent-activity-item__chip--mono"
        :class="{ 'agent-activity-item__chip--danger': activity.status === 'failed' }"
        :title="activityView.displayToolName"
      >{{ activityView.displayToolName }}</span>
      <span
        v-if="activityView.execEnvBadge"
        class="agent-activity-item__chip agent-activity-item__chip--env"
        :title="activityView.execEnvBadgeTitle"
        data-exec-env="container"
      >{{ activityView.execEnvBadge }}</span>
      <span
        v-if="activityView.showToolArguments"
        class="agent-activity-item__summary agent-activity-item__summary--code agent-activity-item__tool-args"
        :title="activityView.toolArguments"
      >{{ activityView.toolArguments }}</span>
      <span
        v-if="activityView.showModelInLlmInfer"
        class="agent-activity-item__chip agent-activity-item__chip--mono"
        :title="activityView.model"
      >{{ activityView.model }}</span>
      <span
        v-if="activityView.retryHistoryText"
        class="agent-activity-item__chip agent-activity-item__chip--danger"
        :title="activityView.retryHistoryText"
      >{{ activityView.retryHistoryText }}</span>
      <span
        v-if="activityView.sendMessagePrefix"
        class="agent-activity-item__direction"
        aria-hidden="true"
      >➡️</span>
      <span
        v-if="activityView.sendMessagePrefix"
        class="agent-activity-item__chip"
        :title="activityView.sendMessagePrefix"
      >{{ activityView.sendMessagePrefix }}</span>
      <span
        v-if="activityView.startChatTarget"
        class="agent-activity-item__chip"
        :title="activityView.startChatTarget"
      >{{ activityView.startChatTarget }}</span>
      <span
        v-if="activityView.taskTitle"
        class="agent-activity-item__chip"
        :title="activityView.taskTitle"
      >{{ activityView.taskTitle }}</span>
      <span
        v-if="activityView.retryText"
        class="agent-activity-item__summary agent-activity-item__summary--danger"
        :title="activityView.retryText"
      >{{ activityView.retryText }}</span>
      <span
        v-if="activityView.showSummary"
        class="agent-activity-item__summary"
        :class="{
          'agent-activity-item__summary--code': activityView.summaryIsCode,
          'agent-activity-item__summary--bash-description': activityView.executeBashResult && Boolean(activityView.executeBashDescription),
        }"
        :title="activityView.summaryTitle"
      >{{ activityView.summary }}</span>
      <span
        v-if="activityView.showModelOutsideLlmInfer"
        class="agent-activity-item__chip agent-activity-item__chip--mono"
        :title="activityView.model"
      >{{ activityView.model }}</span>
      <span
        v-if="activityView.showMetadataToolName"
        class="agent-activity-item__chip agent-activity-item__chip--mono"
        :title="activityView.metadataToolName"
      >{{ activityView.metadataToolName }}</span>
      <AgentActivityToken :activity="activity" />
      <span v-if="activityView.taskRoomLabel" class="agent-activity-item__tail">
        <span
          class="agent-activity-item__finish-turn-room"
          :title="activityView.taskRoomLabel"
        >{{ activityView.taskRoomLabel }}</span>
      </span>
    </div>
    <button
      v-if="activityView.reasoningContent && reasoningOverflows"
      type="button"
      class="agent-activity-item__chat-reply-toggle agent-activity-item__reasoning-toggle"
      @click="toggleReasoning"
    >{{ reasoningCollapsed ? t('common.expand') : t('common.collapse') }}</button>
    <ActivityChatReply
      v-if="activityView.sendChatMsgContent && !activityView.sendChatMsgError"
      :content="activityView.sendChatMsgContent"
      :collapsed="chatReplyCollapsed"
      :collapsible="activityView.sendChatMsgTruncated"
      @toggle="chatReplyCollapsed = !chatReplyCollapsed"
    />
    <p v-if="activityView.sendChatMsgError" class="agent-activity-item__error">调用失败：{{ activityView.sendChatMsgError }}</p>
    <p v-if="activityView.showRetryErrorMessage" class="agent-activity-item__error">{{ activityView.retryErrorText }}</p>
    <ActivityBashResult
      v-if="activityView.executeBashResult"
      :command="activityView.executeBashCommand"
      :stdout="activityView.stdout"
      :stderr="activityView.stderr"
    />
    <ActivityToolResult
      v-else-if="activityView.expandedToolResult"
      :content="activityView.toolResult"
    />
    <ActivityReceivedMessages
      v-if="activityView.showReceivedMessages"
      :messages="activityView.receivedMessages"
    />
    <p v-if="activityView.showErrorMessage" class="agent-activity-item__error">{{ activity.error_message }}</p>
    <ActivityChatReply
      v-if="activityView.chatReplyContent"
      :content="activityView.chatReplyContent"
      :collapsed="chatReplyCollapsed"
      :collapsible="activityView.chatReplyIsLong"
      @toggle="chatReplyCollapsed = !chatReplyCollapsed"
    />
  </article>
</template>

<style scoped>
.agent-activity-item {
  display: grid;
  gap: 3px;
  padding: 6px 8px;
  border-radius: 10px;
  background: var(--surface-activity-item);
  border: 1px solid var(--border-activity-item);
  flex-shrink: 0;
  position: relative;
  min-width: 0;
  overflow-wrap: anywhere;
}

.agent-activity-item:hover,
.agent-activity-item:focus-within {
  z-index: 50;
}

.agent-activity-item[data-status='started'] {
  background: var(--surface-activity-item-active);
  border-color: var(--border-subtle);
}

.agent-activity-item[data-status='failed'] {
  border-color: var(--border-danger-item);
  background: var(--surface-danger-item);
}

.agent-activity-item__row {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  flex-wrap: nowrap;
  overflow: visible;
}

.agent-activity-item__state-anchor {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  outline: none;
}

.agent-activity-item__state {
  flex: none;
  width: 14px;
  text-align: center;
  font-size: 0.78rem;
  font-weight: 600;
  line-height: 1;
  color: var(--muted);
}

.agent-activity-item__state[data-status='started'] {
  color: var(--good);
}

.agent-activity-item__state[data-status='succeeded'] {
  color: var(--good);
}

.agent-activity-item__state[data-status='failed'],
.agent-activity-item__state[data-status='cancelled'] {
  color: var(--danger, #f85149);
}

.agent-activity-item__state-popover {
  position: absolute;
  top: 50%;
  left: calc(100% + 12px);
  z-index: 100;
  display: grid;
  gap: 8px;
  min-width: 236px;
  max-width: 280px;
  padding: 10px 12px;
  border-radius: 12px;
  border: 1px solid color-mix(in srgb, var(--panel-border) 84%, transparent);
  background: var(--surface-elevated);
  box-shadow:
    0 4px 20px rgba(0, 0, 0, 0.15),
    0 2px 8px rgba(0, 0, 0, 0.1),
    inset 0 0 0 1px rgba(255, 255, 255, 0.05);
  opacity: 0;
  visibility: hidden;
  transform: translateY(-50%) translateX(-4px);
  pointer-events: auto;
  transition:
    opacity 120ms ease,
    transform 120ms ease,
    visibility 120ms ease;
}

.agent-activity-item__state-title {
  color: var(--text-strong);
  font-size: 0.78rem;
  line-height: 1.25;
  font-weight: 600;
  white-space: nowrap;
}

.agent-activity-item__state-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
}

.agent-activity-item__state-row-left {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.agent-activity-item__state-anchor:hover .agent-activity-item__state-popover,
.agent-activity-item__state-anchor:focus-visible .agent-activity-item__state-popover,
.agent-activity-item__state-anchor:focus-within .agent-activity-item__state-popover {
  opacity: 1;
  visibility: visible;
  transform: translateY(-50%) translateX(0);
}

.agent-activity-item__state-meta {
  color: var(--text);
  font-size: 0.72rem;
  line-height: 1.2;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  opacity: 0.88;
}

.agent-activity-item__state-meta--strong {
  font-weight: 600;
  opacity: 1;
}

.agent-activity-item__state-extra {
  color: var(--muted);
  font-size: 0.7rem;
  line-height: 1.2;
  font-variant-numeric: tabular-nums;
}

.agent-activity-item__title {
  flex: none;
  display: inline-flex;
  align-items: center;
  height: 18px;
  color: var(--text-strong);
  font-size: 0.82rem;
  line-height: 1;
  font-weight: 600;
}

.agent-activity-item__row span {
  color: var(--text-strong);
  font-size: 0.7rem;
  line-height: 1.2;
}

.agent-activity-item__chip {
  flex: none;
  display: inline-flex;
  align-items: center;
  box-sizing: border-box;
  max-width: 180px;
  min-width: 0;
  height: 18px;
  padding: 0 6px;
  line-height: 1;
  border-radius: 4px;
  background: var(--surface-chip);
  color: var(--text-strong);
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.agent-activity-item__chip--mono {
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  font-size: 0.66rem;
  padding-bottom: 1px;
}

.agent-activity-item__chip--danger {
  background: var(--surface-chip-danger);
  color: var(--danger);
}

/* 执行环境徽标：只在容器态渲染，用 info 色与普通 chip 区分。 */
.agent-activity-item__chip--env {
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  font-size: 0.66rem;
  font-variant-numeric: tabular-nums;
  font-weight: 400;
  padding-bottom: 1px;
  max-width: 140px;
  border: 1px solid color-mix(in srgb, var(--state-info) 24%, var(--border-default) 76%);
  background: color-mix(in srgb, var(--state-info) 12%, var(--surface-panel) 88%);
  color: color-mix(in srgb, var(--state-info) 84%, var(--text-primary) 16%);
}

.agent-activity-item__direction {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 18px;
  color: var(--text-secondary);
  font-size: 0.76rem;
  line-height: 1;
}

.agent-activity-item__status {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 42px;
  height: 18px;
  padding: 0 6px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--accent) 12%, transparent);
  color: color-mix(in srgb, var(--accent) 76%, var(--text) 24%);
  font-weight: 600;
}

.agent-activity-item__tail {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  flex: none;
  min-width: max-content;
  padding-left: 8px;
}

.agent-activity-item__tail > * {
  flex: none;
}

.agent-activity-item__finish-turn-room {
  min-width: 0;
  max-width: 220px;
  text-align: right;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.agent-activity-item[data-status='started'] .agent-activity-item__status {
  background: color-mix(in srgb, var(--good) 16%, transparent);
  color: color-mix(in srgb, var(--good) 84%, var(--text) 16%);
}

.agent-activity-item[data-status='failed'] .agent-activity-item__status {
  background: var(--surface-chip-danger);
  color: var(--danger);
}

.agent-activity-item__summary {
  min-width: 0;
  flex: 1 1 auto;
  color: var(--text-description);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.agent-activity-item__tool-name,
.agent-activity-item__tool-args {
  min-width: 0;
  white-space: nowrap;
}

.agent-activity-item__tool-name {
  max-width: 150px;
}

.agent-activity-item__tool-args {
  flex: 1 1 auto;
  max-width: none;
  overflow: hidden;
  text-overflow: ellipsis;
}

.agent-activity-item[data-activity-type='tool_call'] .agent-activity-item__tail {
  flex: 0 1 auto;
  min-width: 0;
}

.agent-activity-item[data-status='started'] .agent-activity-item__summary {
  color: var(--muted);
}

.agent-activity-item__summary--code {
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  font-size: 0.69rem;
  font-weight: 600;
  color: var(--muted);
}

.agent-activity-item__summary--danger {
  color: var(--danger);
}

.agent-activity-item[data-status='failed'] .agent-activity-item__send-chat-content {
  color: var(--danger);
}



.agent-activity-item__error {
  margin: 0;
  color: var(--danger);
  font-size: 0.7rem;
  font-weight: 600;
  line-height: 1.35;
  padding-left: 22px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.agent-activity-item[data-status='failed'] .agent-activity-item__title {
  color: var(--text-danger-strong);
}

.agent-activity-item[data-status='failed'] .agent-activity-item__row span {
  color: var(--text-danger-muted);
}

.agent-activity-item[data-status='failed'] .agent-activity-item__summary {
  color: var(--danger);
}

.agent-activity-item[data-status='failed'] .agent-activity-item__chip {
  background: var(--surface-chip-danger);
  color: var(--danger);
}

.agent-activity-item[data-status='failed'] .agent-activity-item__tool-result,
.agent-activity-item[data-status='failed'] :deep(.agent-activity-item__received-message) {
  color: var(--danger);
}

.agent-activity-item[data-status='failed'] :deep(.agent-activity-item__tool-result--code) {
  color: var(--danger);
}

.agent-activity-item[data-status='failed'].agent-activity-item--bash-result .agent-activity-item__summary--code {
  color: var(--danger);
}

.agent-activity-item[data-status='failed'].agent-activity-item--bash-result .agent-activity-item__summary--bash-description {
  color: var(--danger);
}

.agent-activity-item[data-status='failed'].agent-activity-item--bash-result :deep(.agent-activity-item__tool-result--code) {
  color: var(--danger);
}

.agent-activity-item[data-status='failed'].agent-activity-item--bash-result :deep(.agent-activity-item__tool-result--stdout) {
  color: var(--danger);
}

.agent-activity-item[data-status='failed'] :deep(.agent-activity-item__received-sender) {
  color: var(--danger);
}

.agent-activity-item--expanded .agent-activity-item__row {
  flex-wrap: nowrap;
  align-items: flex-start;
}

.agent-activity-item--expanded .agent-activity-item__state-anchor {
  margin-top: 0.18rem;
}

.agent-activity-item--expanded .agent-activity-item__summary {
  flex: 1 1 auto;
  min-width: 0;
  padding-left: 0;
  white-space: pre-wrap;
  overflow: visible;
  text-overflow: clip;
  font-size: 0.8rem;
  line-height: 1.55;
  color: var(--text-description);
}

.agent-activity-item--expanded .agent-activity-item__tail {
  margin-left: auto;
  width: auto;
  padding-left: 8px;
}

.agent-activity-item--message .agent-activity-item__row {
  flex-wrap: wrap;
  align-items: flex-start;
}

.agent-activity-item--message .agent-activity-item__summary {
  flex: 1 0 100%;
  order: 10;
  padding-left: 22px;
}

.agent-activity-item--message .agent-activity-item__tail {
  order: 11;
}

.agent-activity-item--tool-result .agent-activity-item__row {
  flex-wrap: nowrap;
  align-items: center;
}

.agent-activity-item--tool-result .agent-activity-item__summary {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  line-height: 1.2;
  color: var(--text-description);
}

/* bash 描述要和左侧 18px 高的 chip 落在同一条视觉中线上。
   expanded（0.8rem/1.55，flex-start）与 tool-result（1.2）给 summary 的行盒都比 chip 高，
   于是右侧「查版本号位置」比左侧「执行命令」低 1.5~2.5px。这里把描述钉成 18px 行盒、
   垂直居中并单行省略，执行中/已完成两种状态都贴合 chip 中线。
   字号故意不写：沿用各状态原有字号（执行中 0.7rem、展开态 0.8rem），只改行盒和居中方式，
   否则展开态的字会莫名变小。
   注意本规则必须在 --expanded / --tool-result 的 summary 规则之后，靠顺序覆盖它们。 */
.agent-activity-item--bash-result .agent-activity-item__summary--bash-description {
  align-self: center;
  height: 18px;
  line-height: 18px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

:deep(.agent-activity-item__tool-result) {
  margin: 0;
  padding-left: 22px;
  color: var(--text);
  font-size: 0.76rem;
  line-height: 1.45;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 5;
  overflow: hidden;
  white-space: pre-wrap;
  word-break: break-all;
}

:deep(.agent-activity-item__tool-result--description) {
  display: block;
  -webkit-line-clamp: unset;
}

:deep(.agent-activity-item__tool-result--code) {
  display: block;
  -webkit-line-clamp: unset;
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  font-size: 0.72rem;
  color: var(--muted);
}

:deep(.agent-activity-item__tool-result--stdout) {
  color: var(--text);
}

:deep(.agent-activity-item__tool-result--stderr) {
  color: var(--danger);
}

:deep(.agent-activity-item__received-message) {
  margin: 0;
  padding-left: 22px;
  color: var(--text);
  font-size: 0.76rem;
  line-height: 1.45;
  white-space: pre-wrap;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
}

:deep(.agent-activity-item__received-sender) {
  font-weight: 600;
  color: var(--text-strong);
  margin-right: 4px;
}

:deep(.agent-activity-item__chat-reply) {
  padding-left: 22px;
  padding-right: 4px;
  font-size: 0.8rem;
  line-height: 1.55;
  min-width: 0;
  color: var(--text);
}

/* 思考内容：与对勾同一行（不在下面另起一行），默认只露三行、超出可展开。
   正文用 💭 起头，样式比正文更弱，避免抢注意力。 */
.agent-activity-item__row--reasoning {
  align-items: flex-start;
}

.agent-activity-item__row--reasoning .agent-activity-item__state-anchor {
  margin-top: 0.18rem;
}

.agent-activity-item__reasoning {
  flex: 1 1 auto;
  padding-left: 0;
  padding-right: 4px;
  font-size: 0.78rem;
  line-height: 1.5;
  min-width: 0;
  color: var(--text-description);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.agent-activity-item__reasoning-mark {
  margin-right: 3px;
}


.agent-activity-item__reasoning--collapsed {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
  overflow: hidden;
}

:deep(.agent-activity-item__chat-reply--collapsed) {
  max-height: 7.5em;
  overflow: hidden;
  position: relative;
}

:deep(.agent-activity-item__chat-reply--collapsed)::after {
  content: '';
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  height: 2.5em;
  background: linear-gradient(transparent, color-mix(in srgb, var(--panel-bg) 94%, var(--surface-soft) 6%));
  pointer-events: none;
}

:deep(.agent-activity-item__chat-reply-toggle) {
  background: none;
  border: none;
  color: var(--accent);
  font-size: 0.72rem;
  cursor: pointer;
  padding: 0 0 0 22px;
  line-height: 1.4;
}

:deep(.agent-activity-item__chat-reply-toggle:hover) {
  text-decoration: underline;
}

/* 思考内容的展开按钮与正文左对齐（对勾宽 14px + gap 6px），必须放在
   .chat-reply-toggle 之后，否则同优先级下 padding-left: 22px 会覆盖回来。 */
.agent-activity-item__reasoning-toggle {
  padding-left: 20px;
}

</style>

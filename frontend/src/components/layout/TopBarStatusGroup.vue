<script setup lang="ts">
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';
import type { ConnectionState } from '../../utils';

/**
 * 顶栏「连接状态」区块：团队停用提示、调度状态胶囊（含原因气泡与恢复按钮）、
 * 实时连接状态胶囊（含重连进度环）、活跃 Agent 计数胶囊（含名单气泡）。
 *
 * 从 `TopBar.vue` 拆出（P4）：纯展示 + 单个 `resumeSchedule` 事件，不持有可写状态。
 * 注意本组件是**多根**（fragment）：四个元素直接作为父级 `.status-group` 的 flex 子项，
 * 与拆分前的 DOM 层级、顺序完全一致（`.status-group` 容器仍归 `TopBar.vue` 所有）。
 */
const { t } = useI18n();

const props = defineProps<{
  connectionState: ConnectionState;
  statusLabel: string;
  reconnectProgress: number;
  activeAgentCount: number;
  activeAgentNames: string[];
  activeTeamEnabled: boolean;
  showTeamDisabledPill?: boolean;
  showConnectionStatus?: boolean;
  scheduleState?: string;
  scheduleNotRunningReason?: string;
  scheduleResumePending?: boolean;
}>();

const emit = defineEmits<{
  resumeSchedule: [];
}>();

const scheduleLabel = computed(() => {
  switch (props.scheduleState) {
    case 'blocked': return t('topbar.scheduleStopped');
    case 'stopped': return t('topbar.scheduleStopped');
    default: return '';
  }
});
const scheduleTooltip = computed(() => {
  if (!props.scheduleState || props.scheduleState === 'running') {
    return '';
  }
  return props.scheduleNotRunningReason || (props.scheduleState === 'blocked' ? t('topbar.scheduleBlockedReason') : t('topbar.scheduleStoppedReason'));
});
</script>

<template>
  <div v-if="showTeamDisabledPill && !activeTeamEnabled" class="team-disabled-pill">{{ t('topbar.teamDisabled') }}</div>
  <div
    v-if="scheduleLabel"
    class="schedule-state-pill-wrapper"
  >
    <div
      class="schedule-state-pill"
      :data-state="scheduleState"
    >
      <span class="schedule-dot"></span>
      {{ scheduleLabel }}
    </div>
    <div v-if="scheduleTooltip" class="schedule-tooltip" role="dialog" aria-live="polite">
      <p class="schedule-tooltip__label">{{ t('topbar.scheduleReasonLabel') }}</p>
      <p class="schedule-tooltip__reason">{{ scheduleTooltip }}</p>
      <button
        type="button"
        class="schedule-tooltip__action"
        :disabled="scheduleResumePending"
        @click="emit('resumeSchedule')"
      >
        {{ scheduleResumePending ? t('topbar.resumeSchedulePending') : t('topbar.resumeSchedule') }}
      </button>
    </div>
  </div>
  <div v-if="showConnectionStatus" class="status-pill" :data-state="connectionState">
    <span
      v-if="connectionState === 'waiting_reconnect'"
      class="reconnect-indicator"
      :style="{ '--reconnect-progress': reconnectProgress.toString() }"
      aria-hidden="true"
    >
      <svg viewBox="0 0 16 16" class="reconnect-ring">
        <circle class="reconnect-ring-track" cx="8" cy="8" r="5.5" />
        <circle class="reconnect-ring-progress" cx="8" cy="8" r="5.5" />
      </svg>
    </span>
    <span
      v-else
      class="status-dot"
      :class="{ 'status-dot-pulse': connectionState === 'reconnecting' }"
    ></span>
    {{ statusLabel }}
  </div>
  <div v-if="showConnectionStatus" class="metric-pill-wrapper">
    <div
      class="metric-pill"
      :class="{ 'metric-pill--active': activeAgentCount > 0 }"
      tabindex="0"
      aria-describedby="active-agents-tooltip"
    >{{ t('topbar.activeAgentsCount', { count: activeAgentCount }) }}</div>
    <div id="active-agents-tooltip" class="metric-tooltip" role="tooltip">
      <p class="metric-tooltip__label">{{ t('topbar.activeAgentsLabel') }}</p>
      <ul v-if="activeAgentNames.length" class="metric-tooltip__list">
        <li v-for="(name, index) in activeAgentNames" :key="`${name}-${index}`" class="metric-tooltip__item">
          <span class="metric-tooltip__dot" aria-hidden="true"></span>
          <span class="metric-tooltip__name">{{ name }}</span>
        </li>
      </ul>
      <p v-else class="metric-tooltip__empty">{{ t('topbar.noActiveAgents') }}</p>
    </div>
  </div>
</template>

<style scoped>
.team-disabled-pill,
.status-pill,
.metric-pill,
.schedule-state-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 28px;
  box-sizing: border-box;
  border: 1px solid var(--border-subtle);
  border-radius: 8px;
  padding: 3px 8px;
  background: var(--surface-pill);
  color: var(--text-secondary);
  font-size: 0.78rem;
  line-height: 1.2;
  transition:
    border-color 140ms ease,
    background 140ms ease,
    color 140ms ease;
}

.team-disabled-pill:hover,
.status-pill:hover,
.metric-pill:hover,
.schedule-state-pill:hover {
  border-color: var(--interactive-focus-border);
  background: color-mix(in srgb, var(--interactive-selected) 18%, var(--surface-pill) 82%);
}

.metric-pill-wrapper {
  position: relative;
  display: inline-flex;
  align-items: center;
}

.metric-pill {
  cursor: default;
  outline: none;
}

.metric-pill--active {
  color: color-mix(in srgb, var(--state-success) 82%, var(--text-primary) 18%);
}

.metric-tooltip {
  position: absolute;
  top: calc(100% + 6px);
  right: 0;
  min-width: 160px;
  max-width: 240px;
  padding: 9px 10px;
  border-radius: 10px;
  background: var(--surface-overlay);
  border: 1px solid var(--border-subtle);
  color: var(--text-primary);
  font-size: 0.75rem;
  white-space: normal;
  opacity: 0;
  visibility: hidden;
  transform: translateY(-3px);
  transition: opacity 0.15s ease, transform 0.15s ease, visibility 0.15s ease;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
  z-index: 100;
  display: grid;
  gap: 7px;
  pointer-events: none;
}

.metric-pill-wrapper:hover .metric-tooltip,
.metric-pill-wrapper:focus-within .metric-tooltip {
  opacity: 1;
  visibility: visible;
  transform: translateY(0);
}

.metric-tooltip__label,
.metric-tooltip__empty {
  margin: 0;
}

.metric-tooltip__label {
  color: var(--text-tertiary);
  font-size: 0.64rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.metric-tooltip__list {
  display: grid;
  gap: 6px;
  max-height: 220px;
  margin: 0;
  padding: 0;
  overflow-y: auto;
  list-style: none;
}

.metric-tooltip__item {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.metric-tooltip__dot {
  width: 6px;
  height: 6px;
  flex: none;
  border-radius: 999px;
  background: var(--state-success);
}

.metric-tooltip__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.metric-tooltip__empty {
  color: var(--text-secondary);
}

.team-disabled-pill {
  border-color: color-mix(in srgb, var(--state-warning) 28%, var(--border-subtle) 72%);
  background: color-mix(in srgb, var(--state-warning) 16%, var(--surface-pill) 84%);
}

.schedule-state-pill-wrapper {
  position: relative;
  display: inline-flex;
  align-items: center;
}

.schedule-state-pill {
  border-color: color-mix(in srgb, var(--state-warning) 28%, var(--border-subtle) 72%);
  background: color-mix(in srgb, var(--state-warning) 12%, var(--surface-pill) 88%);
  color: color-mix(in srgb, var(--state-warning) 82%, var(--text-primary) 18%);
}

.schedule-state-pill[data-state='stopped'] {
  border-color: color-mix(in srgb, var(--state-danger) 28%, var(--border-subtle) 72%);
  background: color-mix(in srgb, var(--state-danger) 12%, var(--surface-pill) 88%);
  color: color-mix(in srgb, var(--state-danger) 82%, var(--text-primary) 18%);
}

.schedule-tooltip {
  position: absolute;
  top: calc(100% + 6px);
  left: 50%;
  transform: translateX(-50%) scale(0.95);
  min-width: 220px;
  max-width: 280px;
  padding: 10px;
  border-radius: 10px;
  background: var(--surface-overlay);
  border: 1px solid var(--border-subtle);
  color: var(--text-primary);
  font-size: 0.75rem;
  white-space: normal;
  opacity: 0;
  visibility: hidden;
  transition: opacity 0.15s ease, transform 0.15s ease, visibility 0.15s ease;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
  z-index: 100;
  display: grid;
  gap: 8px;
}

.schedule-state-pill-wrapper:hover .schedule-tooltip {
  opacity: 1;
  visibility: visible;
  transform: translateX(-50%) scale(1);
}

.schedule-state-pill-wrapper:focus-within .schedule-tooltip {
  opacity: 1;
  visibility: visible;
  transform: translateX(-50%) scale(1);
}

.schedule-tooltip__label,
.schedule-tooltip__reason {
  margin: 0;
}

.schedule-tooltip__label {
  color: var(--text-tertiary);
  font-size: 0.64rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.schedule-tooltip__action {
  justify-self: start;
  min-height: 28px;
  border: 1px solid var(--interactive-focus-border);
  border-radius: 8px;
  background: color-mix(in srgb, var(--interactive-selected) 58%, var(--surface-pill) 42%);
  color: var(--text-primary);
  padding: 0 10px;
  font: inherit;
  cursor: pointer;
  transition:
    border-color 140ms ease,
    background 140ms ease,
    opacity 140ms ease;
}

.schedule-tooltip__action:hover:not(:disabled) {
  background: color-mix(in srgb, var(--interactive-selected) 72%, var(--surface-pill) 28%);
}

.schedule-tooltip__action:disabled {
  cursor: wait;
  opacity: 0.72;
}

.schedule-dot {
  width: 7px;
  height: 7px;
  border-radius: 999px;
  background: var(--state-warning);
}

.schedule-state-pill[data-state='stopped'] .schedule-dot {
  background: var(--state-danger);
}

.status-pill[data-state='connected'] {
  color: var(--state-success);
}

.status-pill[data-state='connected'] .status-dot {
  background: var(--state-success);
  box-shadow: none;
}

.status-pill[data-state='waiting_reconnect'] .status-dot,
.status-pill[data-state='reconnecting'] .status-dot,
.status-pill[data-state='connecting'] .status-dot {
  background: var(--state-warning);
  box-shadow: none;
}

.status-pill[data-state='waiting_reconnect'],
.status-pill[data-state='reconnecting'] {
  color: var(--state-warning);
}

.status-pill[data-state='disconnected'] .status-dot {
  background: var(--state-danger);
}

.status-dot {
  width: 7px;
  height: 7px;
  border-radius: 999px;
  background: var(--status-dot-idle);
}

.status-dot-pulse {
  width: 6px;
  height: 6px;
  background: var(--state-warning);
  animation: reconnect-dot-pulse 2s ease-in-out infinite;
}

.reconnect-indicator {
  display: inline-flex;
}

.reconnect-ring {
  width: 14px;
  height: 14px;
  transform: rotate(-90deg);
}

.reconnect-ring-track,
.reconnect-ring-progress {
  fill: none;
  stroke-width: 2;
}

.reconnect-ring-track {
  stroke: var(--state-warning-track);
}

.reconnect-ring-progress {
  stroke: var(--state-warning);
  stroke-dasharray: 34.56;
  stroke-dashoffset: calc(34.56 * (1 - var(--reconnect-progress)));
}

@keyframes reconnect-dot-pulse {
  0%,
  100% {
    transform: scale(0.85);
    opacity: 0.55;
  }

  50% {
    transform: scale(1.35);
    opacity: 1;
  }
}

:global(html.bp-console-mobile) .topbar.topbar-console .metric-pill {
  display: none;
}

:global(html.bp-console-mobile) .topbar.topbar-console .team-disabled-pill,
:global(html.bp-console-mobile) .topbar.topbar-console .status-pill,
:global(html.bp-console-mobile) .topbar.topbar-console .schedule-state-pill {
  min-height: 32px;
  padding: 0 10px;
  white-space: nowrap;
  flex: 0 0 auto;
}

:global(html.bp-console-short) .topbar.topbar-console .team-disabled-pill,
:global(html.bp-console-short) .topbar.topbar-console .status-pill,
:global(html.bp-console-short) .topbar.topbar-console .metric-pill,
:global(html.bp-console-short) .topbar.topbar-console .schedule-state-pill {
  min-height: 28px;
  padding: 0 8px;
  font-size: 0.72rem;
}
</style>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import { getAgentDetail, resumeAgent, stopAgent, superviseAgent } from '../../api';
import { connectionState, showGlobalSuccessToast } from '../../appUiState';
import { displayName, formatConnectionState } from '../../utils';
import { useAgentStatus } from '../../realtime/selectors';
import AgentCardBase from './AgentCardBase.vue';
import AgentActivityDialogShell from './AgentActivityDialogShell.vue';
import AgentActivityPanel from './AgentActivityPanel.vue';
import AgentTaskPanel from './AgentTaskPanel.vue';
import AgentPropertiesPanel from './AgentPropertiesPanel.vue';
import type { AgentDetail, AgentStatus } from '../../types';

const { t } = useI18n();

const props = defineProps<{
  open: boolean;
  agentId: number | null;
  agentName: string | null;
  agentStatus?: AgentStatus | null;
  roleTemplateName?: string | null;
}>();

const emit = defineEmits<{
  close: [];
  'navigate-to-chat': [agentId: number];
}>();

const agent = ref<AgentDetail | null>(null);
const loading = ref(false);
const errorMessage = ref('');
const resuming = ref(false);
const stopping = ref(false);
const activeTab = ref<'activities' | 'tasks' | 'properties'>('activities');
const taskPanelCount = ref(0);
const superviseContent = ref('');
const supervising = ref(false);
const superviseError = ref('');
const superviseFocused = ref(false);
const superviseTextareaRef = ref<HTMLTextAreaElement | null>(null);
const isActivitiesFollowing = ref(true);

const runtimeStatus = useAgentStatus(() => props.agentId);

const displayAgent = computed<AgentDetail | null>(() => {
  if (!agent.value || agent.value.id !== props.agentId) {
    return null;
  }
  return agent.value;
});

const currentStatus = computed<AgentStatus | null>(() => {
  if (runtimeStatus.value) {
    return runtimeStatus.value;
  }
  if (props.agentStatus) {
    return props.agentStatus;
  }
  return displayAgent.value?.status ?? null;
});

const statusLabel = computed(() => {
  if (!currentStatus.value) {
    return '';
  }
  if (currentStatus.value === 'active') return t('agent.status.active');
  if (currentStatus.value === 'failed') return t('agent.status.failed');
  if (currentStatus.value === 'closed') return t('agent.status.closed');
  return t('agent.status.idle');
});

const failureMessage = computed(() => {
  if (currentStatus.value !== 'failed') {
    return '';
  }
  return agent.value?.error_message?.trim() ?? '';
});

const failurePreview = computed(() => {
  const message = failureMessage.value;
  if (!message) {
    return '';
  }
  const preview = message.slice(0, 320).trimEnd();
  if (preview.length === message.length) {
    return preview;
  }
  return `${preview}...`;
});

const agentTemplateLabel = computed(() => {
  if (props.roleTemplateName?.trim()) {
    return props.roleTemplateName.trim();
  }
  if (!displayAgent.value) {
    return t('agent.noTemplate');
  }
  if (typeof displayAgent.value.role_template_id === 'number' && displayAgent.value.role_template_id > 0) {
    return t('agent.templateFallback', { id: displayAgent.value.role_template_id });
  }
  return t('agent.noTemplate');
});

const displayAgentName = computed(() => {
  if (displayAgent.value) {
    return displayName(displayAgent.value);
  }
  return props.agentName ?? 'Agent';
});

const displayEmployeeNumber = computed(() => String(displayAgent.value?.employee_number ?? ''));
const activityRealtimeState = computed(() => connectionState.value);
const activityBadgeLabel = computed(() =>
  activityRealtimeState.value === 'connected' ? t('agent.realtimeConnected') : formatConnectionState(activityRealtimeState.value),
);
const taskCountLabel = computed(() => t('agent.taskCount', { count: taskPanelCount.value }));

async function loadDetail(): Promise<void> {
  if (!props.open || props.agentId === null) {
    agent.value = null;
    errorMessage.value = '';
    loading.value = false;
    return;
  }

  loading.value = true;
  errorMessage.value = '';

  try {
    agent.value = await getAgentDetail(props.agentId);
  } catch (error) {
    errorMessage.value = t('agent.infoLoadFailed');
    console.error(error);
  } finally {
    loading.value = false;
  }
}

async function handleResume(): Promise<void> {
  if (props.agentId === null || currentStatus.value !== 'failed' || resuming.value) {
    return;
  }

  resuming.value = true;

  try {
    await resumeAgent(props.agentId);
    showGlobalSuccessToast(t('agent.resumeSuccess'));
  } catch (error) {
    console.error(error);
  } finally {
    resuming.value = false;
  }
}

async function handleStop(): Promise<void> {
  if (props.agentId === null || currentStatus.value !== 'active' || stopping.value) {
    return;
  }

  stopping.value = true;

  try {
    await stopAgent(props.agentId);
    showGlobalSuccessToast(t('agent.stopSuccess'));
  } catch (error) {
    console.error(error);
  } finally {
    stopping.value = false;
  }
}

async function copyFailureMessage(): Promise<void> {
  if (!failureMessage.value) {
    return;
  }

  try {
    await navigator.clipboard.writeText(failureMessage.value);
    showGlobalSuccessToast(t('agent.copiedError'));
  } catch (error) {
    console.error(error);
  }
}

async function sendSupervise(): Promise<void> {
  if (!props.agentId || !superviseContent.value.trim() || supervising.value) {
    return;
  }
  supervising.value = true;
  superviseError.value = '';
  try {
    await superviseAgent(props.agentId, superviseContent.value.trim());
    superviseContent.value = '';
    if (superviseTextareaRef.value) {
      superviseTextareaRef.value.style.height = '';
    }
  } catch (error) {
    superviseError.value = error instanceof Error ? error.message : String(error);
  } finally {
    supervising.value = false;
  }
}

function autoGrowSupervise(): void {
  const el = superviseTextareaRef.value;
  if (!el) {
    return;
  }
  el.style.height = 'auto';
  el.style.height = `${el.scrollHeight}px`;
}

watch(
  () => [props.open, props.agentId, props.agentName],
  () => {
    activeTab.value = 'activities';
    taskPanelCount.value = 0;
    loadDetail().catch(console.error);
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
    loadDetail().catch(console.error);
  },
);

watch(
  () => currentStatus.value,
  (status, previousStatus) => {
    if (
      props.open
      && props.agentId !== null
      && status === 'failed'
      && previousStatus !== 'failed'
    ) {
      loadDetail().catch(console.error);
    }
  },
);
</script>

<template>
    <AgentActivityDialogShell
    :open="open"
    :close-label="t('common.close')"
    :active-tab="activeTab"
    :panel-tabs-label="t('agent.panelTabs')"
    :activities-label="t('agent.activities')"
    :tasks-label="t('agent.tasks')"
    :properties-label="t('agent.properties')"
    :activity-badge-label="activityBadgeLabel"
    :activity-realtime-state="activityRealtimeState"
    :task-count-label="taskCountLabel"
    :error-message="errorMessage"
    :loading-message="t('agent.loadingInfo')"
    :show-loading="loading && !displayAgent && !agentName"
    :show-stage="Boolean(displayAgent || agentName)"
    :show-supervise="activeTab === 'activities'"
    @close="$emit('close')"
    @update:active-tab="activeTab = $event"
  >
    <template #left>
      <div class="agent-detail-stage__card-stack">
        <AgentCardBase
          :title="displayAgentName"
          :subtitle="agentTemplateLabel"
          :employee-number="displayEmployeeNumber"
          :avatar-name="displayAgentName"
          variant="profile"
          readonly
        />
        <div class="agent-status-panel" :data-status="currentStatus ?? undefined">
          <span class="status-dot"></span>
          <span class="agent-status-panel__value">{{ statusLabel }}</span>
        </div>
        <button
          v-if="currentStatus === 'active' && props.agentId"
          type="button"
          class="secondary-button button-danger agent-action-button"
          :disabled="stopping"
          @click="handleStop"
        >
          {{ stopping ? t('agent.stopping') : t('agent.stop') }}
        </button>
        <button
          v-if="currentStatus === 'failed' && props.agentId"
          type="button"
          class="secondary-button agent-action-button"
          :disabled="resuming"
          @click="handleResume"
        >
          {{ resuming ? t('agent.resuming') : t('agent.resume') }}
        </button>
        <button
          v-if="props.agentId"
          type="button"
          class="secondary-button agent-action-button"
          @click="emit('navigate-to-chat', props.agentId!)"
        >
          {{ t('agent.goToChat', '打开房间') }}
        </button>
        <div v-if="failureMessage" class="agent-error-panel">
          <p class="agent-error-message">{{ failurePreview }}</p>
          <button type="button" class="agent-error-panel__copy" @click="copyFailureMessage">
            {{ t('agent.copyAll') }}
          </button>
        </div>
      </div>
    </template>

    <template #panel>
      <AgentActivityPanel
        v-show="activeTab === 'activities'"
        :open="open"
        :agent-id="agentId"
        @follow-change="isActivitiesFollowing = $event"
      />
      <AgentTaskPanel
        v-show="activeTab === 'tasks'"
        :open="open"
        :agent-id="agentId"
        :team-id="displayAgent?.team_id ?? null"
        @count-change="taskPanelCount = $event"
      />
      <AgentPropertiesPanel
        v-show="activeTab === 'properties'"
        :agent-id="agentId"
        :initial-agent="displayAgent"
        @saved="agent = $event"
      />
    </template>

    <template #supervise>
      <section class="agent-supervise-section" :class="{ 'is-following': isActivitiesFollowing && activeTab === 'activities' }">
        <div class="agent-supervise-section__input-row">
          <div class="agent-supervise-section__editor" :class="{ 'is-focused': superviseFocused }">
            <textarea
              ref="superviseTextareaRef"
              v-model="superviseContent"
              class="agent-supervise-section__textarea"
              :placeholder="t('agent.supervise.placeholder')"
              rows="1"
              :disabled="supervising"
              @focus="superviseFocused = true"
              @blur="superviseFocused = false"
              @input="autoGrowSupervise"
              @keydown.ctrl.enter.prevent="sendSupervise"
              @keydown.meta.enter.prevent="sendSupervise"
            />
          </div>
          <button
            type="button"
            class="agent-supervise-section__send"
            :disabled="supervising || !superviseContent.trim()"
            @click="sendSupervise"
          >
            {{ supervising ? t('agent.supervise.sending') : t('agent.supervise.send') }}
          </button>
        </div>
        <p v-if="superviseError" class="agent-supervise-section__error">{{ superviseError }}</p>
      </section>
    </template>
  </AgentActivityDialogShell>
</template>

<style scoped>
.agent-detail-stage__card-stack {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  justify-content: center;
  min-height: 100%;
  width: fit-content;
  max-width: 100%;
}

.agent-detail-stage__card-stack :deep(.entity-card) {
  cursor: default;
  margin: 0 auto;
  transform: scale(0.96);
  transform-origin: top center;
}

.agent-detail-stage__card-stack :deep(.entity-card:hover) {
  transform: scale(0.96);
}

.agent-status-panel {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-wrap: wrap;
  gap: 8px;
  min-height: 42px;
  width: auto;
  max-width: 100%;
  padding: 4px 0 0;
  border: 0;
  border-radius: 0;
  background: transparent;
  color: var(--muted);
  font-size: 0.82rem;
  line-height: 1;
}

.agent-status-panel__value {
  color: inherit;
  font-size: inherit;
  font-weight: 600;
  line-height: inherit;
}

.agent-status-panel[data-status='failed'],
.agent-status-panel[data-status='closed'] {
  color: var(--danger, #f85149);
}

.agent-action-button {
  width: 100%;
  min-width: 0;
  height: 28px;
  min-height: 28px;
  padding: 0 10px;
  font-size: 0.78rem;
  border-radius: 6px;
  margin-top: 0;
}

.agent-error-panel {
  width: min(260px, 100%);
  margin: 0;
  padding: 10px 12px;
  border-radius: 14px;
  border: 1px solid color-mix(in srgb, var(--danger, #f85149) 18%, var(--panel-border) 82%);
  background: color-mix(in srgb, var(--danger, #f85149) 5%, var(--panel-bg) 95%);
}

.agent-error-message {
  margin: 0;
  max-height: 132px;
  overflow: hidden;
  color: color-mix(in srgb, var(--danger, #f85149) 88%, var(--text) 12%);
  font-size: 0.72rem;
  line-height: 1.4;
  text-align: left;
  white-space: pre-wrap;
  word-break: break-word;
  display: -webkit-box;
  -webkit-line-clamp: 8;
  -webkit-box-orient: vertical;
}

.agent-error-panel__copy {
  margin-top: 6px;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--accent);
  font-size: 0.7rem;
  line-height: 1;
  cursor: pointer;
}

.status-dot {
  width: 7px;
  height: 7px;
  border-radius: 999px;
  background: var(--status-dot-idle);
}

.agent-status-panel[data-status='active'] .status-dot {
  background: var(--state-success);
  box-shadow: none;
}

.agent-status-panel[data-status='failed'] .status-dot,
.agent-status-panel[data-status='closed'] .status-dot {
  background: var(--danger, #f85149);
  box-shadow: none;
}

.agent-supervise-section {
  position: relative;
  margin-top: 0;
  padding: 4px 0 0;
  flex-shrink: 0;
  background: transparent;
  border: 0;
  border-radius: 0;
  box-shadow: none;
}

.agent-supervise-section::after {
  content: '';
  position: absolute;
  top: -2px;
  left: 0;
  right: 0;
  height: 1px;
  background: var(--agent-divider-emphasis, color-mix(in srgb, var(--panel-border) 94%, var(--border-subtle) 6%));
  box-shadow: 0 1px 0 color-mix(in srgb, var(--agent-divider-emphasis, var(--panel-border)) 24%, transparent);
  pointer-events: none;
  transition: background 0.2s ease, box-shadow 0.2s ease;
}

.agent-supervise-section.is-following::after {
  background: var(--accent);
  box-shadow: 0 1px 0 color-mix(in srgb, var(--accent) 30%, transparent);
}

.agent-supervise-section__input-row {
  display: flex;
  align-items: flex-end;
  gap: 8px;
}

.agent-supervise-section__editor {
  flex: 1;
  min-width: 0;
  background: var(--surface-input);
  border: 1px solid color-mix(in srgb, var(--border-subtle) 78%, var(--border-default) 22%);
  border-radius: 8px;
  overflow: hidden;
  transition:
    border-color 160ms ease,
    box-shadow 160ms ease;
}

.agent-supervise-section__editor.is-focused {
  border-color: var(--input-focus-border);
  box-shadow: 0 0 0 2px var(--input-focus-ring);
}

.agent-supervise-section__textarea {
  display: block;
  width: 100%;
  resize: none;
  border: none;
  border-radius: 0;
  padding: 7px 10px;
  font-size: 0.8rem;
  font-family: inherit;
  background: transparent;
  color: var(--text-primary);
  line-height: 1.4;
  min-height: 30px;
  max-height: 160px;
  overflow-y: auto;
  outline: none;
}

.agent-supervise-section__textarea::placeholder {
  color: var(--text-secondary);
}

.agent-supervise-section__textarea:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.agent-supervise-section__send {
  flex-shrink: 0;
  border: 0;
  border-radius: 6px;
  padding: 5px 10px;
  background: var(--interactive-selected);
  color: var(--text-primary);
  font-weight: 700;
  cursor: pointer;
  font-size: 0.74rem;
  white-space: nowrap;
  transition: opacity 0.15s;
}

.agent-supervise-section__send:disabled {
  cursor: not-allowed;
  opacity: 0.4;
}

.agent-supervise-section__error {
  margin: 5px 4px 0;
  font-size: 0.75rem;
  color: var(--color-error, #ef4444);
}

@media (max-width: 720px) {
  .agent-detail-stage__card-stack {
    width: 100%;
  }
}
</style>

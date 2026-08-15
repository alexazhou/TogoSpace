<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue';
import { useI18n } from 'vue-i18n';
import TeamMemberGraph from './TeamMemberGraph.vue';
import type { TeamGraphNode } from './teamGraphTypes';

type MemberPanelAction = {
  key: string;
  label: string;
  disabled?: boolean;
  primary?: boolean;
  showBadge?: boolean;
};

withDefaults(defineProps<{
  teamName: string;
  selectedAgents: string[];
  selectedAgentIds?: Record<string, number | null>;
  memberTemplates?: Record<string, string>;
  rootNode?: TeamGraphNode | null;
  statusMessage?: string;
  readonly?: boolean;
  actions?: MemberPanelAction[];
  showEditAction?: boolean;
}>(), {
  memberTemplates: () => ({}),
  rootNode: null,
  statusMessage: '',
  readonly: false,
  actions: () => [],
  showEditAction: false,
});

const emit = defineEmits<{
  toggleAgent: [nodeId: string];
  viewAgent: [agentId: number | null, nodeId: string, agentName: string];
  editAgent: [nodeId: string];
  editDepartment: [nodeId: string];
  viewDepartment: [nodeId: string];
  addSubordinate: [nodeId: string];
  editPendingSlot: [slotId: string];
  removePendingSlot: [slotId: string];
  action: [key: string];
}>();

const { t } = useI18n();

const isFullscreen = ref(false);

function toggleFullscreen(): void {
  isFullscreen.value = !isFullscreen.value;
}

function handleKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape' && isFullscreen.value) {
    isFullscreen.value = false;
  }
}

onMounted(() => {
  window.addEventListener('keydown', handleKeydown);
});

onBeforeUnmount(() => {
  window.removeEventListener('keydown', handleKeydown);
});
</script>

<template>
  <Teleport to="body" :disabled="!isFullscreen">
    <section class="member-panel" :class="{ 'member-panel--fullscreen': isFullscreen }">
      <div class="member-panel-head">
        <div class="member-panel-head-segment member-panel-head-segment--label">
          <span class="panel-label">{{ t('agent.teamMembersLabel') }}</span>
        </div>
        <button
          type="button"
          class="member-panel-fullscreen-button"
          :title="isFullscreen ? t('common.exitFullscreen') : t('common.fullscreen')"
          @click="toggleFullscreen"
        >
          <svg v-if="!isFullscreen" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M3 3h7v2H5v5H3V3zm11 0h7v7h-2V5h-5V3zM3 14h2v5h5v2H3v-7zm18 0v7h-7v-2h5v-5h2z" fill="currentColor" />
          </svg>
          <svg v-else viewBox="0 0 24 24" aria-hidden="true">
            <path d="M10 3v7H3V8h5V3h2zm4 0h2v5h5v2h-7V3zM3 14h7v7H8v-5H3v-2zm11 0h7v2h-5v5h-2v-7z" fill="currentColor" />
          </svg>
        </button>
      </div>

      <div v-if="actions.length" class="member-panel-actions">
          <button
            v-for="action in actions"
            :key="action.key"
            type="button"
            class="secondary-button member-panel-action"
            :class="{ 'member-panel-action--primary': action.primary }"
            :disabled="action.disabled"
            @click="emit('action', action.key)"
          >
            <span v-if="action.showBadge" class="member-panel-action__badge" aria-hidden="true"></span>
            {{ action.label }}
          </button>
      </div>

      <div v-if="statusMessage" class="member-panel-status">
        <strong>{{ statusMessage }}</strong>
      </div>

      <TeamMemberGraph
        v-else
        :team-name="teamName"
        :selected-agents="selectedAgents"
        :selected-agent-ids="selectedAgentIds"
        :member-templates="memberTemplates"
        :root-node="rootNode"
        :readonly="readonly"
        :show-edit-action="showEditAction"
        @toggle-agent="emit('toggleAgent', $event)"
        @view-agent="(agentId, nodeId, agentName) => emit('viewAgent', agentId, nodeId, agentName)"
        @edit-agent="emit('editAgent', $event)"
        @edit-department="emit('editDepartment', $event)"
        @view-department="emit('viewDepartment', $event)"
        @add-subordinate="emit('addSubordinate', $event)"
        @edit-pending-slot="emit('editPendingSlot', $event)"
        @remove-pending-slot="emit('removePendingSlot', $event)"
      />
    </section>
  </Teleport>
</template>

<style scoped>
.member-panel {
  position: relative;
  display: grid;
  gap: 8px;
  border: 1px solid var(--team-create-panel-border);
  border-radius: 20px;
  background: var(--panel-bg);
  box-shadow: var(--panel-shadow);
  padding: 10px 12px;
  min-height: 0;
  overflow: hidden;
  align-self: stretch;
  padding-bottom: 10px;
  transition:
    border-color 0.18s ease,
    box-shadow 0.18s ease,
    background-color 0.18s ease;
}

.member-panel--fullscreen {
  position: fixed;
  inset: 0;
  z-index: 40;
  border-radius: 0;
  border: none;
  padding: 16px 20px;
  overflow: hidden;
  grid-template-rows: minmax(0, 1fr);
  animation: member-panel-expand 0.28s cubic-bezier(0.22, 0.61, 0.36, 1);
}

.member-panel--fullscreen :deep(.member-graph) {
  height: 100%;
}

@keyframes member-panel-expand {
  from {
    opacity: 0.6;
    transform: scale(0.96);
  }
  to {
    opacity: 1;
    transform: scale(1);
  }
}

.member-panel:focus-within {
  border-color: color-mix(in srgb, var(--focus-border) 88%, #ffffff 12%);
  box-shadow:
    var(--panel-shadow),
    0 0 0 4px color-mix(in srgb, var(--focus-border) 28%, transparent);
  background: color-mix(in srgb, var(--panel-bg) 84%, var(--selected) 16%);
}

.member-panel--fullscreen:focus-within {
  box-shadow: none;
}

.member-panel-head {
  position: absolute;
  top: 10px;
  left: 12px;
  right: 12px;
  z-index: 2;
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 36px;
}

.member-panel--fullscreen .member-panel-head {
  top: 16px;
  left: 20px;
  right: 20px;
}

.member-panel-fullscreen-button {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  padding: 0;
  border: 1px solid color-mix(in srgb, var(--focus-border) 28%, var(--panel-border) 72%);
  border-radius: 8px;
  background: color-mix(in srgb, var(--panel-bg) 90%, transparent);
  color: var(--muted);
  cursor: pointer;
  transition:
    border-color 0.16s ease,
    background 0.16s ease,
    color 0.16s ease,
    transform 0.16s ease;
}

.member-panel-fullscreen-button:hover {
  border-color: var(--focus-border);
  background: color-mix(in srgb, var(--selected) 32%, var(--panel-bg) 68%);
  color: var(--text-strong);
  transform: scale(1.06);
}

.member-panel-fullscreen-button:active {
  transform: scale(0.96);
}

.member-panel-fullscreen-button svg {
  width: 16px;
  height: 16px;
}

.member-panel-head-segment {
  display: inline-flex;
  align-items: center;
  min-height: 36px;
  background: color-mix(in srgb, var(--panel-bg) 90%, transparent);
  padding: 0 8px;
}

.panel-label {
  display: inline-flex;
  align-items: center;
  padding: 0;
  border-radius: 0;
  background: transparent;
  color: var(--text-strong);
  font-size: 1rem;
  font-weight: 600;
  letter-spacing: 0.01em;
}

.member-panel-actions {
  position: absolute;
  right: 12px;
  bottom: 10px;
  z-index: 2;
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 8px;
  min-height: 40px;
  background: color-mix(in srgb, var(--panel-bg) 90%, transparent);
  padding: 4px 10px 0;
}

.member-panel-action {
  height: 30px;
  min-width: 108px;
  padding: 0 12px;
  font-size: 0.82rem;
}

.member-panel-action:disabled {
  opacity: 1;
  cursor: not-allowed;
  color: var(--hint-text);
  border-color: color-mix(in srgb, var(--panel-border) 76%, transparent 24%);
  background: color-mix(in srgb, var(--surface-soft) 82%, var(--panel-bg) 18%);
  box-shadow: none;
}

.member-panel-action--primary {
  border-color: color-mix(in srgb, var(--focus-border) 45%, var(--team-create-control-border) 55%);
  background: color-mix(in srgb, var(--selected) 28%, var(--panel-bg) 72%);
}

.member-panel-action--primary:disabled {
  border-color: color-mix(in srgb, var(--panel-border) 76%, transparent 24%);
  background: color-mix(in srgb, var(--surface-soft) 82%, var(--panel-bg) 18%);
}

.member-panel-action {
  position: relative;
}

.member-panel-action__badge {
  position: absolute;
  top: -2px;
  right: -2px;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--state-danger);
  border: 1px solid var(--panel-bg);
}

.member-panel-status {
  min-height: 452px;
  display: grid;
  place-items: center;
  padding: 48px 20px 20px;
  text-align: center;
}

.member-panel-status strong {
  min-width: 280px;
  min-height: 220px;
  padding: 24px 28px;
  border: 1px dashed color-mix(in srgb, var(--focus-border) 26%, var(--panel-border) 74%);
  border-radius: 20px;
  background: color-mix(in srgb, var(--panel-bg) 72%, var(--surface-soft) 28%);
  display: grid;
  place-items: center;
  color: var(--text-strong);
  font-size: 1rem;
  font-weight: 600;
}
</style>

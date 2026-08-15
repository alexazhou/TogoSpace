<script setup lang="ts">
import { useI18n } from 'vue-i18n';
import { getAgentAvatarUrl } from '../../avatar';
import { displayName } from '../../utils';
import type { AgentInfo } from '../../types';

defineProps<{
  agents: Array<AgentInfo & { departmentPath?: string | null; isDepartmentLeader?: boolean }>;
}>();

const emit = defineEmits<{
  selectAgent: [agentId: number];
}>();

const { t } = useI18n();

function statusLabel(status: AgentInfo['status']): string {
  if (status === 'active') {
    return t('agent.status.active');
  }
  if (status === 'failed') {
    return t('agent.status.failed');
  }
  return t('agent.status.idle');
}

function selectAgent(agent: AgentInfo): void {
  emit('selectAgent', agent.id as number);
}
</script>

<template>
  <section class="sidebar-card panel">
    <div class="block-head">
      <h2>{{ t('agent.teamMembersLabel') }}</h2>
      <span>{{ agents.length }}</span>
    </div>

    <div class="sidebar-scroll agent-list">
      <button
        v-for="agent in agents"
        :key="agent.id ?? agent.name"
        class="agent-card sidebar-item-card"
        type="button"
        @click="selectAgent(agent)"
      >
        <div class="agent-primary">
          <img class="agent-avatar" :src="getAgentAvatarUrl(agent.name)" :alt="`${displayName(agent)} avatar`" />
          <div class="agent-copy">
            <strong class="agent-name-line">
              <span class="agent-name">{{ displayName(agent) }}</span>
              <span v-if="agent.isDepartmentLeader" class="agent-leader-badge">{{ t('agent.departmentLeader') }}</span>
            </strong>
            <p>{{ agent.departmentPath || agent.model }}</p>
          </div>
        </div>
        <div class="agent-state" :data-state="agent.status">
          <span class="status-dot" :class="{ 'status-dot-pulse': agent.status === 'active' }"></span>
          {{ statusLabel(agent.status) }}
        </div>
      </button>
    </div>
  </section>
</template>

<style scoped>
.sidebar-card.panel {
  box-shadow: inset 0 0 0 1px var(--panel-border-soft);
}

.agent-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  scrollbar-width: thin;
  scrollbar-color: var(--scrollbar-thumb) var(--scrollbar-track);
}

.agent-list::-webkit-scrollbar {
  width: 10px;
}

.agent-list::-webkit-scrollbar-track {
  background: var(--scrollbar-track);
  border-radius: 6px;
}

.agent-list::-webkit-scrollbar-thumb {
  background: var(--scrollbar-thumb);
  border-radius: 999px;
  border: 2px solid var(--scrollbar-track);
}

.agent-list::-webkit-scrollbar-thumb:hover {
  background: var(--scrollbar-thumb-hover);
}

.agent-card {
  width: 100%;
  display: flex;
  justify-content: space-between;
  gap: 8px;
  align-items: center;
  padding: 8px 10px;
  border: none;
  cursor: pointer;
  color: inherit;
  text-align: left;
  transition:
    background 120ms ease,
    box-shadow 120ms ease;
}

.agent-card:hover,
.agent-card:focus-visible {
  background: var(--interactive-selected);
  box-shadow: inset 0 0 0 1px var(--room-card-border-active);
  outline: none;
}

.agent-primary {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.agent-copy {
  min-width: 0;
}

.agent-avatar {
  width: 28px;
  height: 28px;
  border-radius: 8px;
  flex-shrink: 0;
  object-fit: cover;
  background: color-mix(in srgb, var(--surface-elevated) 84%, var(--border-default) 16%);
  box-shadow: 0 0 0 1px color-mix(in srgb, var(--border-strong) 30%, transparent);
}

.agent-name-line {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.84rem;
  line-height: 1.1;
  min-width: 0;
}

.agent-name {
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.agent-leader-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  height: 18px;
  padding: 0 7px;
  border-radius: 6px;
  border: 1px solid color-mix(in srgb, var(--state-success) 24%, var(--border-default) 76%);
  background: color-mix(in srgb, var(--state-success) 12%, var(--surface-panel) 88%);
  color: color-mix(in srgb, var(--state-success) 84%, var(--text-primary) 16%);
  font-size: 0.66rem;
  font-weight: 400;
  line-height: 1.1;
  white-space: nowrap;
}

.agent-template {
  color: var(--text-tertiary);
  font-size: 0.72rem;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.agent-card p {
  margin: 0;
  color: var(--text-secondary);
  font-size: 0.72rem;
  white-space: nowrap;
  transform: translateY(2px);
}

.agent-state {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--text-secondary);
  white-space: nowrap;
  font-size: 0.72rem;
}

.agent-state[data-state='active'] .status-dot {
  background: var(--state-success);
  box-shadow: none;
}

.agent-state[data-state='failed'] {
  color: var(--state-danger);
}

.agent-state[data-state='failed'] .status-dot {
  background: var(--state-danger);
  box-shadow: none;
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
  background: var(--state-success);
  animation: agent-dot-pulse 2s ease-in-out infinite;
}

@keyframes agent-dot-pulse {
  0%,
  100% {
    transform: scale(0.85);
    opacity: 0.85;
  }

  50% {
    transform: scale(1.35);
    opacity: 1;
  }
}
</style>

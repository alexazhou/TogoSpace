<script setup lang="ts">
import { useI18n } from 'vue-i18n';
import AgentTemplateCard from './AgentTemplateCard.vue';

defineProps<{
  keyword: string;
  filteredAgents: string[];
  selectedAgents: string[];
}>();

const emit = defineEmits<{
  'update:keyword': [value: string];
  toggleAgent: [agentName: string];
}>();

const { t } = useI18n();
</script>

<template>
  <section class="library-panel">
    <div class="library-head">
      <span class="panel-label">{{ t('agent.backupLabel') }}</span>
      <label class="search-box">
        <input
          :value="keyword"
          type="text"
          :placeholder="t('agent.searchPlaceholder')"
          @input="emit('update:keyword', ($event.target as HTMLInputElement).value)"
        />
      </label>
    </div>

    <div class="agent-grid">
      <AgentTemplateCard
        v-for="agentName in filteredAgents"
        :key="agentName"
        :title="agentName"
        :subtitle="t('agent.cardSubtitle')"
        :avatar-name="agentName"
        :avatar-seed="agentName"
        :selected="selectedAgents.includes(agentName)"
        @click="emit('toggleAgent', agentName)"
      />

      <div v-if="!filteredAgents.length" class="empty-state">
        {{ t('agent.agentLoadFailed') }}
      </div>
    </div>
  </section>
</template>

<style scoped>
.library-panel {
  display: grid;
  gap: 8px;
  border: 1px solid var(--team-create-panel-border);
  border-radius: 20px;
  background: var(--panel-bg);
  box-shadow: var(--panel-shadow);
  padding: 10px 12px;
  grid-column: 1 / -1;
  grid-row: 2;
  min-height: 120px;
  overflow: hidden;
  grid-template-rows: auto minmax(0, 1fr) auto;
}

.library-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  align-items: center;
  margin: -2px 0 0;
}

.library-head > .panel-label {
  padding-top: 0;
}

.panel-label {
  color: var(--text-strong);
  font-size: 1rem;
  font-weight: 600;
  letter-spacing: 0.01em;
}

.search-box {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 180px;
  color: var(--text-strong);
}

.search-box input {
  width: 180px;
  height: 28px;
  font-size: 0.8rem;
  border-radius: 10px;
  border: 1px solid var(--team-create-control-border);
  background: var(--surface-soft);
  color: var(--text-strong);
  padding: 0 10px;
  outline: none;
  box-shadow: none;
  transition:
    border-color 0.18s ease,
    box-shadow 0.18s ease,
    background 0.18s ease;
}

.search-box input:focus {
  border-color: var(--focus-border);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--focus-border) 18%, transparent);
  background: var(--panel-bg);
}

.agent-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, 78px);
  justify-content: start;
  gap: 8px 12px;
  min-height: 0;
  overflow: hidden;
  align-content: start;
  padding-right: 4px;
}

.agent-grid:has(.empty-state:only-child) {
  display: flex;
  align-items: center;
  justify-content: center;
}

.empty-state {
  min-height: 88px;
  min-width: min(280px, 100%);
  border-radius: 18px;
  display: grid;
  place-items: center;
  padding: 12px;
  color: var(--muted);
  background: color-mix(in srgb, var(--surface-soft) 70%, transparent);
  text-align: center;
}

@media (max-width: 960px) {
  .agent-grid {
    grid-template-columns: repeat(auto-fill, 78px);
  }

  .library-head {
    align-items: flex-start;
    flex-direction: column;
  }

  .search-box {
    min-width: 100%;
    width: 100%;
  }

  .search-box input {
    width: 100%;
  }
}

@media (max-width: 640px) {
  .agent-grid {
    grid-template-columns: repeat(auto-fill, 78px);
    justify-content: center;
  }

  .search-box {
    min-width: 100%;
  }
}
</style>

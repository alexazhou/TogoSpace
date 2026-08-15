<script setup lang="ts">
import { computed } from 'vue';
import { useTeamAgentsWithDepartmentPath } from '../../realtime/selectors';
import AgentListSection from '../agent/AgentListSection.vue';

const props = defineProps<{
  teamId: number | null;
}>();

const emit = defineEmits<{
  selectAgent: [agentId: number];
}>();

const agents = useTeamAgentsWithDepartmentPath(() => props.teamId);
const visibleAgents = computed(() =>
  agents.value.filter((agent) =>
    !agent.special && String(agent.employ_status ?? '').toUpperCase() !== 'OFF_BOARD',
  ),
);
</script>

<template>
  <div class="console-panel">
    <AgentListSection
      :agents="visibleAgents"
      @select-agent="emit('selectAgent', $event)"
    />
  </div>
</template>

<style scoped>
.console-panel {
  min-height: 0;
  min-width: 0;
  display: flex;
}

.console-panel > * {
  flex: 1 1 auto;
  min-height: 0;
  min-width: 0;
  height: 100%;
  width: 100%;
}
</style>

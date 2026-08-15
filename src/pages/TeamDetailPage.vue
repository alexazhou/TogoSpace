<script setup lang="ts">
import '../theme/legacy-aliases.css';
import { computed, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { totalMessageCount } from '../appUiState';
import { getAgentsByTeamId, getTeamDetail } from '../api';
import AgentActivityDialog from '../components/agent/AgentActivityDialog.vue';
import TeamInfoCard from '../components/team/TeamInfoCard.vue';
import TeamMembersCard from '../components/team/TeamMembersCard.vue';
import { loadRoleTemplates } from '../realtime/runtimeStore';
import { useRoleTemplates } from '../realtime/selectors';
import { displayName, i18nText } from '../utils';
import type { AgentInfo, AgentStatus, TeamDetail } from '../types';

const route = useRoute();
const router = useRouter();

const team = ref<TeamDetail | null>(null);
const loading = ref(true);
const errorMessage = ref('');
const agentDetailOpen = ref(false);
const teamAgents = ref<AgentInfo[]>([]);
const selectedAgentId = ref<number | null>(null);

const teamId = computed(() => Number(route.params.teamId));
const roleTemplates = useRoleTemplates();
const activeTeamAgents = computed(() => (
  teamAgents.value.filter((agent) => agent.employ_status === 'ON_BOARD' || !agent.employ_status)
));
const selectedAgents = computed(() => activeTeamAgents.value.map((agent) => agent.name));
const selectedAgentIds = computed<Record<string, number | null>>(() => (
  Object.fromEntries(activeTeamAgents.value.map((agent) => [agent.name, agent.id ?? null]))
));
const selectedAgentName = computed<string | null>(() => {
  const agent = teamAgents.value.find((item) => item.id === selectedAgentId.value);
  return agent ? displayName(agent) : null;
});
const selectedAgentStatus = computed<AgentStatus | null>(() =>
  teamAgents.value.find((agent) => agent.id === selectedAgentId.value)?.status ?? null,
);
const selectedAgentTemplateName = computed<string | null>(() => {
  const roleTemplateId = teamAgents.value.find((agent) => agent.id === selectedAgentId.value)?.role_template_id;
  if (typeof roleTemplateId !== 'number') {
    return null;
  }
  const template = roleTemplates.value.find((item) => item.id === roleTemplateId);
  return template ? displayName(template) : `模板 #${roleTemplateId}`;
});
const selectedAgentTemplates = computed<Record<string, string>>(() =>
  Object.fromEntries(activeTeamAgents.value.map((agent) => [
    agent.name,
    (() => {
      const template = roleTemplates.value.find((item) => item.id === agent.role_template_id);
      return template ? displayName(template) : `模板 #${agent.role_template_id}`;
    })(),
  ])),
);

function roomMemberNames(roomId: number): string[] {
  const room = team.value?.rooms.find((item) => item.id === roomId);
  if (!room) {
    return [];
  }

  const memberMap = new Map((team.value?.members ?? []).map((member) => [member.id, member]));
  return (room.agent_ids ?? []).map((agentId) => {
    const member = memberMap.get(agentId);
    return member ? displayName(member) : String(agentId);
  });
}

async function loadDetail(): Promise<void> {
  loading.value = true;
  errorMessage.value = '';
  totalMessageCount.value = 0;

  try {
    const [detail, nextAgents] = await Promise.all([
      getTeamDetail(teamId.value),
      getAgentsByTeamId(teamId.value),
      loadRoleTemplates(),
    ]);
    team.value = detail;
    teamAgents.value = nextAgents;
  } catch (error) {
    errorMessage.value = '团队详情加载失败。';
    console.error(error);
  } finally {
    loading.value = false;
  }
}

function openRoom(roomId: number): void {
  router.push({ name: 'console', params: { teamId: teamId.value, roomId } }).catch(console.error);
}

function openAgentDetail(agentId: number | null, _nodeId: string, agentName: string): void {
  selectedAgentId.value = agentId ?? teamAgents.value.find((agent) => agent.name === agentName)?.id ?? null;
  agentDetailOpen.value = true;
}

function closeAgentDetail(): void {
  agentDetailOpen.value = false;
  selectedAgentId.value = null;
}

watch(() => route.params.teamId, () => {
  loadDetail().catch(console.error);
});

onMounted(() => {
  loadDetail().catch(console.error);
});
</script>

<template>
  <section class="page panel">
    <div v-if="errorMessage" class="error-banner">{{ errorMessage }}</div>
    <div v-else-if="loading" class="loading-card">正在加载团队详情…</div>

    <template v-else-if="team">
      <div class="form-grid">
        <TeamInfoCard
          :name="displayName(team)"
          :working-directory="team.working_directory || ''"
          :slogan="String(team.config?.slogan || '')"
          :rules="String(team.config?.rules || '')"
          readonly
        />

        <TeamMembersCard
          :team-name="team.name"
          :selected-agents="selectedAgents"
          :selected-agent-ids="selectedAgentIds"
          :member-templates="selectedAgentTemplates"
          readonly
          @view-agent="openAgentDetail"
        />

        <section class="rooms-panel">
          <div class="rooms-head">
            <div>
              <span class="panel-label">团队房间</span>
              <small>{{ team.rooms.length }} 个房间</small>
            </div>
            <button type="button" class="secondary-button" @click="$router.back()">返回</button>
          </div>

          <div class="rooms-grid">
            <button
              v-for="room in team.rooms"
              :key="room.id"
              type="button"
              class="room-tile"
              @click="openRoom(room.id)"
            >
              <div class="room-tile-head">
                <strong>{{ i18nText(room.i18n, 'display_name', room.name) }}</strong>
                <span>{{ room.agents.length }} 人</span>
              </div>
              <p>{{ i18nText(room.i18n, 'initial_topic', room.initial_topic || '无初始话题') }}</p>
              <div class="room-tile-meta">
                <span>max_turns {{ room.max_turns ?? '未设置' }}</span>
                <span>{{ roomMemberNames(room.id).join(' / ') }}</span>
              </div>
            </button>

            <div v-if="!team.rooms.length" class="empty-state">
              当前团队还没有房间。
            </div>
          </div>
        </section>
      </div>
    </template>

    <AgentActivityDialog
      :open="agentDetailOpen"
      :agent-id="selectedAgentId"
      :agent-name="selectedAgentName"
      :agent-status="selectedAgentStatus"
      :role-template-name="selectedAgentTemplateName"
      @close="closeAgentDetail"
    />
  </section>
</template>

<style scoped>
.page {
  height: 100%;
  overflow: hidden;
  padding: 0;
  display: grid;
  grid-template-rows: minmax(0, 1fr);
  gap: 8px;
  background: transparent;
  border: none;
  box-shadow: none;
  border-radius: 0;
}

.form-grid {
  display: grid;
  grid-template-columns: minmax(280px, 320px) minmax(0, 1fr);
  grid-template-rows: minmax(0, auto) minmax(0, 1fr);
  gap: 8px;
  max-width: none;
  height: 100%;
  min-height: 0;
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

.rooms-panel {
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
  grid-template-rows: auto minmax(0, 1fr);
}

.rooms-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  margin: -2px 0 0;
}

.rooms-head > div {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.panel-label {
  color: var(--text-strong);
  font-size: 1rem;
  font-weight: 600;
  letter-spacing: 0.01em;
}

.rooms-head small {
  color: var(--muted);
  font-size: 0.76rem;
}

.rooms-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 8px;
  min-height: 0;
  overflow: auto;
  align-content: start;
  padding-right: 4px;
}

.room-tile {
  border: 1px solid var(--team-create-control-border);
  border-radius: 14px;
  background: var(--surface-soft);
  color: var(--text-strong);
  padding: 12px;
  text-align: left;
  cursor: pointer;
  transition:
    border-color 0.18s ease,
    background 0.18s ease,
    transform 0.18s ease;
}

.room-tile:hover {
  border-color: var(--focus-border);
  background: var(--backend-selected-strong, color-mix(in srgb, var(--selected) 70%, var(--surface-soft) 30%));
  transform: translateY(-1px);
}

.room-tile-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.room-tile-head strong {
  font-size: 0.9rem;
}

.room-tile-head span,
.room-tile p,
.room-tile-meta {
  color: var(--muted);
}

.room-tile p {
  margin: 8px 0;
  font-size: 0.78rem;
  line-height: 1.4;
}

.room-tile-meta {
  display: flex;
  flex-direction: column;
  gap: 3px;
  font-size: 0.72rem;
}

.team-detail-head .secondary-button {
  min-width: 72px;
  height: 28px;
  padding: 0 12px;
}

.loading-card,
.error-banner {
  padding: 14px;
}

.error-banner {
  border-radius: 10px;
  background: var(--banner-error-bg);
  color: var(--banner-error-text);
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
  .form-grid {
    grid-template-columns: 1fr;
    grid-template-rows: auto auto minmax(0, 1fr);
  }

  .rooms-head {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>

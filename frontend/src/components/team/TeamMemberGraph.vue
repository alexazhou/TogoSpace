<script setup lang="ts">
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';
import TeamMemberTreeNode from './TeamMemberTreeNode.vue';
import type { TeamGraphNode } from './teamGraphTypes';
import { useTeamGraphLayout } from './useTeamGraphLayout';

const props = defineProps<{
  teamName: string;
  selectedAgents: string[];
  selectedAgentIds?: Record<string, number | null>;
  memberTemplates?: Record<string, string>;
  rootNode?: TeamGraphNode | null;
  readonly?: boolean;
  showEditAction?: boolean;
}>();

const emit = defineEmits<{
  toggleAgent: [nodeId: string];
  viewAgent: [agentId: number | null, nodeId: string, agentName: string];
  editAgent: [nodeId: string];
  editDepartment: [nodeId: string];
  viewDepartment: [nodeId: string];
  addSubordinate: [nodeId: string];
  editPendingSlot: [slotId: string];
  removePendingSlot: [slotId: string];
}>();

const { t } = useI18n();
const readonly = computed(() => !!props.readonly);
const memberTemplates = computed(() => props.memberTemplates ?? {});
const selectedAgentIds = computed(() => props.selectedAgentIds ?? {});

function buildFallbackRootNode(): TeamGraphNode | null {
  const leaderName = props.selectedAgents[0] ?? '';
  if (!leaderName) {
    return null;
  }

  const buildMemberAvatarSeed = (memberName: string): string => `${props.teamName}::${memberName}`;
  const resolveMemberSubtitle = (memberName: string): string => memberTemplates.value[memberName] || t('teamTree.noTemplate');

  return {
    id: leaderName,
    agentId: selectedAgentIds.value[leaderName] ?? null,
    kind: 'member',
    name: leaderName,
    departmentName: leaderName,
    hasDepartment: true,
    subtitle: resolveMemberSubtitle(leaderName),
    avatarName: leaderName,
    avatarSeed: buildMemberAvatarSeed(leaderName),
    children: props.selectedAgents.slice(1).map((agentName) => ({
      id: agentName,
      agentId: selectedAgentIds.value[agentName] ?? null,
      kind: 'member',
      name: agentName,
      departmentName: agentName,
      hasDepartment: false,
      subtitle: resolveMemberSubtitle(agentName),
      avatarName: agentName,
      avatarSeed: buildMemberAvatarSeed(agentName),
      children: [],
    })),
  };
}

const graphRootNode = computed(() => props.rootNode ?? buildFallbackRootNode());

function collectGraphNodeNames(node: TeamGraphNode | null): string[] {
  if (!node) {
    return [];
  }

  const names: string[] = [];
  const stack = [node];
  while (stack.length) {
    const current = stack.pop()!;
    if (current.kind === 'member' && current.name) {
      names.push(current.name);
    }
    for (let index = current.children.length - 1; index >= 0; index -= 1) {
      stack.push(current.children[index]);
    }
  }
  return names;
}

const {
  graphRef,
  canvasRef,
  memberTreeRef,
  isPanning,
  canvasStyle,
  startPan,
  movePan,
  endPan,
  handleWheelZoom,
} = useTeamGraphLayout({
  readonly,
  selectedAgents: computed(() => collectGraphNodeNames(graphRootNode.value)),
  contentVersion: computed(() => JSON.stringify(graphRootNode.value ?? null)),
});
</script>

<template>
  <div
    ref="graphRef"
    class="member-graph"
    :class="{ 'is-panning': isPanning, 'is-editing': !props.readonly, 'is-empty': !graphRootNode }"
    tabindex="0"
    @pointerdown="startPan"
    @pointermove="movePan"
    @pointerup="endPan"
    @pointercancel="endPan"
    @pointerleave="endPan"
    @wheel="handleWheelZoom"
  >
    <div v-if="!graphRootNode" class="member-empty-state">
      <strong>{{ t('teamTree.noMembers') }}</strong>
      <p>{{ props.readonly ? t('teamTree.noMembersHintReadonly') : t('teamTree.noMembersHint') }}</p>
    </div>

    <div v-else ref="canvasRef" class="member-canvas" :style="canvasStyle">
      <div ref="memberTreeRef">
        <TeamMemberTreeNode
          :node="graphRootNode"
          :readonly="readonly"
          :show-edit-action="!!props.showEditAction"
          :root="true"
          @toggle-agent="emit('toggleAgent', $event)"
          @view-agent="(agentId, nodeId, agentName) => emit('viewAgent', agentId, nodeId, agentName)"
          @edit-agent="emit('editAgent', $event)"
          @edit-department="emit('editDepartment', $event)"
          @view-department="emit('viewDepartment', $event)"
          @add-subordinate="emit('addSubordinate', $event)"
          @edit-pending-slot="emit('editPendingSlot', $event)"
          @remove-pending-slot="emit('removePendingSlot', $event)"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.member-graph {
  --member-grid-size: 28px;
  --member-grid-line: rgba(148, 163, 184, 0.16);
  --member-connector-line: color-mix(in srgb, var(--focus-border) 72%, var(--panel-border) 28%);
  position: relative;
  height: 452px;
  padding: 8px 6px 0;
  display: grid;
  justify-items: center;
  align-content: start;
  background-image: none;
  overflow: hidden;
  touch-action: none;
  user-select: none;
  cursor: grab;
}

.member-graph.is-empty {
  display: flex;
  align-items: center;
  justify-content: center;
}

.member-graph.is-editing {
  background-color: rgba(148, 163, 184, 0.12);
  background-image:
    linear-gradient(to right, var(--member-grid-line) 1px, transparent 1px),
    linear-gradient(to bottom, var(--member-grid-line) 1px, transparent 1px);
  background-size: var(--member-grid-size) var(--member-grid-size);
  background-position: 0 0;
}

.member-graph.is-panning {
  cursor: grabbing;
}

.member-canvas {
  --member-card-width: 102px;
  --member-gap: 18px;
  position: relative;
  left: 50%;
  min-height: 260px;
  width: max-content;
  background: transparent;
  padding: 10px 6px 0;
  display: grid;
  justify-items: center;
  gap: 28px;
  will-change: transform;
  transform-origin: center center;
  z-index: 1;
}

.member-empty-state {
  min-width: 280px;
  min-height: 220px;
  padding: 24px 28px;
  border: 1px dashed color-mix(in srgb, var(--focus-border) 26%, var(--panel-border) 74%);
  border-radius: 20px;
  background: color-mix(in srgb, var(--panel-bg) 72%, var(--surface-soft) 28%);
  display: grid;
  place-items: center;
  gap: 8px;
  text-align: center;
}

.member-empty-state strong {
  color: var(--text-strong);
  font-size: 1rem;
}

.member-empty-state p {
  margin: 0;
  color: var(--muted);
  font-size: 0.78rem;
  line-height: 1.5;
}

.member-graph.is-panning :deep(.team-root.is-readonly),
.member-graph.is-panning :deep(.member-node.is-readonly) {
  cursor: grabbing;
}

:deep(.member-action-button) {
  width: 78px;
  min-width: 0;
  height: 24px;
  border: 1px solid color-mix(in srgb, var(--focus-border) 48%, var(--panel-border) 52%);
  border-radius: 999px;
  background: color-mix(in srgb, var(--panel-bg) 76%, var(--selected) 24%);
  color: var(--text-strong);
  padding: 0 8px;
  font-size: 0.72rem;
  line-height: 1;
  cursor: pointer;
  transition:
    border-color 0.16s ease,
    background 0.16s ease;
}

:deep(.member-action-button:hover) {
  border-color: var(--focus-border);
  background: var(--selected);
}

:deep(.member-action-button--danger) {
  border-color: color-mix(in srgb, var(--danger) 34%, var(--team-create-control-border) 66%);
  background: color-mix(in srgb, var(--danger) 18%, var(--panel-bg) 82%);
  color: color-mix(in srgb, var(--text-strong) 82%, var(--danger) 18%);
}

:deep(.member-action-button--danger:hover) {
  border-color: color-mix(in srgb, #ef4444 62%, var(--focus-border) 38%);
  background: color-mix(in srgb, var(--danger) 26%, var(--panel-bg) 74%);
  color: color-mix(in srgb, var(--text-strong) 72%, var(--danger) 28%);
}

@media (max-width: 640px) {
  :deep(.member-child-rail),
  :deep(.member-child-link),
  :deep(.member-child-tree::before) {
    display: none;
  }

  .member-graph {
    min-height: auto;
  }
}
</style>

<script setup lang="ts">
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';
import AgentCardBase from '../agent/AgentCardBase.vue';
import type { TeamGraphNode } from './teamGraphTypes';

const props = defineProps<{
  node: TeamGraphNode;
  readonly: boolean;
  showEditAction: boolean;
  root?: boolean;
  topLevel?: boolean;
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

const showDepartmentAction = computed(() => props.node.kind === 'member' && !!props.node.children.length);
const nodeOverline = computed(() => {
  if (props.node.kind === 'pending') {
    return '';
  }
  if (props.node.hasDepartment === false) {
    return props.node.departmentName || t('teamTree.unassignedDepartment');
  }
  if (!showDepartmentAction.value) {
    return '';
  }
  return props.node.departmentName || '';
});

function handlePrimaryAction(): void {
  if (props.node.kind !== 'member' || props.readonly || props.showEditAction) {
    return;
  }

  emit('toggleAgent', props.node.id);
}

function handleViewAction(): void {
  if (props.node.kind !== 'member') {
    return;
  }

  if (props.readonly) {
    emit('viewAgent', props.node.agentId ?? null, props.node.id, props.node.name);
    return;
  }

  emit('toggleAgent', props.node.id);
}

function handleEditAction(): void {
  if (props.node.kind === 'pending') {
    emit('editPendingSlot', props.node.id);
    return;
  }

  emit('editAgent', props.node.id);
}

function getNodeSpan(node: TeamGraphNode): number {
  if (!node.children.length) {
    return 1;
  }

  return node.children.reduce((total, child) => total + getNodeSpan(child), 0);
}

function formatSpanWidth(span: number): string {
  const gapCount = Math.max(span - 1, 0);
  return `calc(${span} * var(--member-card-width) + ${gapCount} * var(--member-gap))`;
}

function formatHalfSpanWidth(span: number): string {
  return `calc((${formatSpanWidth(span)}) / 2)`;
}

const childSpans = computed(() => props.node.children.map((child) => getNodeSpan(child)));
const totalChildSpan = computed(() => childSpans.value.reduce((total, span) => total + span, 0));
const firstChildSpan = computed(() => childSpans.value[0] ?? 1);
const lastChildSpan = computed(() => childSpans.value[childSpans.value.length - 1] ?? 1);

const childTreeStyle = computed<Record<string, string>>(() => ({
  '--child-rail-left': formatHalfSpanWidth(firstChildSpan.value),
  '--child-rail-right': formatHalfSpanWidth(lastChildSpan.value),
}));

const childListStyle = computed<Record<string, string>>(() => ({
  gridTemplateColumns: `repeat(${Math.max(totalChildSpan.value, 1)}, var(--member-card-width))`,
}));

function buildChildShellStyle(child: TeamGraphNode): Record<string, string> {
  return {
    gridColumn: `span ${getNodeSpan(child)}`,
  };
}
</script>

<template>
  <div
    class="member-card-shell member-node-shell"
    :class="{
      'has-action': node.kind === 'pending' || !!node.name,
      'has-children': !!node.children.length,
      'is-root-node': root,
    }"
  >
    <div class="member-card-anchor">
      <AgentCardBase
        class="member-node member-card-button"
        :class="{
          'top-level-node': topLevel,
          'team-root': root,
          'member-node--unassigned': node.kind === 'member' && node.hasDepartment === false,
        }"
        :empty="node.kind === 'pending'"
        :readonly="readonly"
        :title="node.kind === 'pending' ? '+' : node.name"
        :overline="nodeOverline"
        :subtitle="node.subtitle"
        :employee-number="node.employeeNumber"
        :avatar-name="node.kind === 'pending' ? '' : node.avatarName"
        :avatar-seed="node.kind === 'pending' ? '' : (node.avatarSeed || node.avatarName)"
        :variant="root ? 'leader' : 'graph'"
        @click="handlePrimaryAction"
      />

      <div class="member-action-group">
        <template v-if="node.kind === 'pending' && !readonly && showEditAction">
          <button
            class="member-action-button"
            type="button"
            @pointerdown.stop
            @click.stop="emit('editPendingSlot', node.id)"
          >
            {{ t('teamTree.edit') }}
          </button>
          <button
            class="member-action-button member-action-button--danger"
            type="button"
            @pointerdown.stop
            @click.stop="emit('removePendingSlot', node.id)"
          >
            {{ t('teamTree.delete') }}
          </button>
        </template>

        <template v-else-if="node.kind === 'member'">
          <template v-if="readonly">
            <button
              v-if="showDepartmentAction"
              class="member-action-button"
              type="button"
              @pointerdown.stop
              @click.stop="emit('viewDepartment', node.id)"
            >
              {{ t('teamTree.viewDept') }}
            </button>
            <button
              class="member-action-button"
              type="button"
              @pointerdown.stop
              @click.stop="handleViewAction"
            >
              {{ t('teamTree.viewMember') }}
            </button>
          </template>
          <template v-else-if="showEditAction">
            <button
              v-if="showDepartmentAction"
              class="member-action-button"
              type="button"
              @pointerdown.stop
              @click.stop="emit('editDepartment', node.id)"
            >
              {{ t('teamTree.editDept') }}
            </button>
            <button
              class="member-action-button"
              type="button"
              @pointerdown.stop
              @click.stop="handleEditAction"
            >
              {{ t('teamTree.editMember') }}
            </button>
            <button
              class="member-action-button"
              type="button"
              @pointerdown.stop
              @click.stop="emit('addSubordinate', node.id)"
            >
              {{ t('teamTree.addSubordinate') }}
            </button>
            <button
              v-if="!root"
              class="member-action-button member-action-button--danger"
              type="button"
              @pointerdown.stop
              @click.stop="emit('toggleAgent', node.id)"
            >
              {{ t('teamTree.removeMember') }}
            </button>
          </template>
          <button
            v-else
            class="member-action-button"
            type="button"
            @pointerdown.stop
            @click.stop="emit('toggleAgent', node.id)"
          >
            {{ t('teamTree.removeMember') }}
          </button>
        </template>
      </div>
    </div>

    <div
      v-if="node.children.length"
      class="member-child-tree"
      :class="{ 'is-single-child': node.children.length === 1 }"
      :style="childTreeStyle"
    >
      <div
        v-if="node.children.length > 1"
        class="member-child-rail"
        aria-hidden="true"
      ></div>

      <div
        class="member-child-list"
        :style="childListStyle"
      >
        <div
          v-for="child in node.children"
          :key="child.id"
          class="member-child-shell"
          :style="buildChildShellStyle(child)"
        >
          <span class="member-child-link" aria-hidden="true"></span>
          <TeamMemberTreeNode
            :node="child"
            :readonly="readonly"
            :show-edit-action="showEditAction"
            :top-level="root"
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
  </div>
</template>

<style scoped>
.member-card-shell,
.member-node-shell,
.member-child-shell {
  position: relative;
  display: grid;
  justify-items: center;
  align-content: start;
}

.member-node--unassigned {
  border-style: dashed;
  border-color: color-mix(in srgb, var(--focus-border) 38%, var(--team-create-node-border) 62%);
  background: color-mix(in srgb, var(--surface-soft) 94%, var(--selected) 6%);
}

.member-node--unassigned:hover {
  border-color: color-mix(in srgb, var(--focus-border) 62%, var(--team-create-node-border) 38%);
  background: color-mix(in srgb, var(--surface-soft) 88%, var(--selected) 12%);
}

.member-card-button {
  justify-self: center;
}

.member-card-anchor {
  position: relative;
  display: grid;
  justify-items: center;
  align-content: start;
  width: max-content;
}

.member-child-tree {
  --member-child-offset: 18px;
  position: relative;
  display: grid;
  justify-items: center;
  justify-self: center;
  width: max-content;
  margin-top: 18px;
  padding-top: var(--member-child-offset);
}

.member-child-tree::before {
  content: '';
  position: absolute;
  top: calc(-1 * var(--member-child-offset));
  left: 50%;
  width: 2px;
  height: var(--member-child-offset);
  transform: translateX(-50%);
  background: var(--member-connector-line);
}

.member-child-tree.is-single-child::before {
  height: calc(var(--member-child-offset) * 2);
}

.member-child-list {
  position: relative;
  display: grid;
  gap: var(--member-gap);
  justify-items: center;
  width: max-content;
}

.member-child-rail {
  position: absolute;
  top: 0;
  left: var(--child-rail-left, calc(var(--member-card-width) / 2));
  right: var(--child-rail-right, calc(var(--member-card-width) / 2));
  height: var(--member-child-offset);
  border-top: 2px solid var(--member-connector-line);
}

.member-child-link {
  position: absolute;
  top: calc(-1 * var(--member-child-offset));
  left: 50%;
  width: 2px;
  height: var(--member-child-offset);
  transform: translateX(-50%);
  background: var(--member-connector-line);
}

.member-action-group {
  position: absolute;
  top: 10px;
  left: 50%;
  width: max-content;
  display: grid;
  justify-items: center;
  gap: 6px;
  opacity: 0;
  transform: translate(-50%, -4px);
  transition:
    opacity 0.16s ease,
    transform 0.16s ease;
  z-index: 3;
}

.member-card-shell.has-action > .member-card-anchor > .member-card-button:hover + .member-action-group,
.member-card-shell.has-action > .member-card-anchor > .member-card-button:focus-visible + .member-action-group,
.member-card-shell.has-action > .member-card-anchor > .member-action-group:hover,
.member-card-shell.has-action > .member-card-anchor > .member-action-group:focus-within {
  opacity: 1;
  transform: translate(-50%, 0);
}

.member-child-shell {
  width: 100%;
}

@media (max-width: 960px) {
  .member-card-shell.is-root-node > .member-child-tree > .member-child-list {
    width: 100%;
    grid-template-columns: repeat(2, minmax(180px, 1fr)) !important;
  }
}

@media (max-width: 640px) {
  .member-card-shell.is-root-node > .member-child-tree {
    width: 100%;
    padding-top: 0;
  }

  .member-card-shell.is-root-node > .member-child-tree > .member-child-list {
    grid-template-columns: 1fr !important;
  }
}
</style>

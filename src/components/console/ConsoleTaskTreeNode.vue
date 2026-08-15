<script setup lang="ts">
import { computed } from 'vue';
import AgentTaskCard from '../agent/AgentTaskCard.vue';
import type { TaskTreeNode } from '../../utils/taskTree';

defineOptions({
  name: 'ConsoleTaskTreeNode',
});

const props = defineProps<{
  node: TaskTreeNode;
  isChild?: boolean;
  agentLabels: Map<number, string>;
}>();

const emit = defineEmits<{
  selectTask: [taskId: number];
}>();

const assigneeLabel = computed(() => {
  const assigneeId = props.node.task.assignee_id;
  return props.agentLabels.get(assigneeId) || `#${assigneeId}`;
});

const managerLabel = computed(() => {
  const managerId = props.node.task.manager_id;
  if (typeof managerId !== 'number' || managerId <= 0) {
    return null;
  }
  return props.agentLabels.get(managerId) || `#${managerId}`;
});

function handleSelect(): void {
  emit('selectTask', props.node.task.id);
}
</script>

<template>
  <div
    class="task-branch"
    :class="{
      'task-branch--child': isChild,
      'task-branch--has-children': node.children.length > 0,
    }"
  >
    <AgentTaskCard
      class="task-card"
      :task="node.task"
      :assignee-label="assigneeLabel"
      :manager-label="managerLabel"
      clickable
      variant="tree"
      @select="handleSelect"
    />

    <div v-if="node.children.length" class="task-branch__children">
      <ConsoleTaskTreeNode
        v-for="child in node.children"
        :key="child.task.id"
        :node="child"
        :is-child="true"
        :agent-labels="agentLabels"
        @select-task="emit('selectTask', $event)"
      />
    </div>
  </div>
</template>

<style scoped>
.task-branch {
  --task-tree-link-gap: 20px;
  --task-tree-link-indent: 20px;
  position: relative;
  display: flex;
  align-items: center;
  gap: var(--task-tree-link-gap);
  min-width: max-content;
}

.task-card {
  position: relative;
}

/* Horizontal connector: vertical bar → child card */
.task-branch--child > .task-card::before {
  content: '';
  position: absolute;
  left: calc(var(--task-tree-link-indent) * -1);
  top: 50%;
  width: var(--task-tree-link-indent);
  border-top: 1px solid color-mix(in srgb, var(--panel-border-strong) 78%, transparent);
  transform: translateY(-0.5px);
}

.task-branch__children {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding-left: var(--task-tree-link-indent);
}

/* Horizontal connector: parent card → vertical bar (at children midpoint) */
.task-branch__children::before {
  content: '';
  position: absolute;
  left: calc(var(--task-tree-link-gap) * -1);
  top: 50%;
  width: var(--task-tree-link-gap);
  border-top: 1px solid color-mix(in srgb, var(--panel-border-strong) 74%, transparent);
  transform: translateY(-0.5px);
}

/*
 * Vertical bar: drawn as two half-segments on each child branch rather than
 * one bar on the container. Using top/bottom: 50% anchors to the branch's own
 * vertical center, which equals the card center because .task-branch uses
 * align-items: center. This is correct even when a branch is tall due to
 * having grandchildren.
 * 3px = half of the 6px sibling gap, so adjacent segments meet seamlessly.
 */
.task-branch--child:not(:last-child)::after {
  content: '';
  position: absolute;
  left: calc(var(--task-tree-link-indent) * -1);
  top: 50%;
  bottom: -3px;
  border-left: 1px solid color-mix(in srgb, var(--panel-border-strong) 74%, transparent);
}

.task-branch--child:not(:first-child)::before {
  content: '';
  position: absolute;
  left: calc(var(--task-tree-link-indent) * -1);
  top: -3px;
  bottom: 50%;
  border-left: 1px solid color-mix(in srgb, var(--panel-border-strong) 74%, transparent);
}

</style>

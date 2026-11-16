<script setup lang="ts">
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';
import type { AgentTask, AgentTaskEvent, AgentTaskStage, AgentTaskStageStatus } from '../../types';

type WorkflowVisualState = 'approved' | 'active' | 'todo' | 'hold' | 'rejected' | 'waiting' | 'cancelled';

interface WorkflowNode {
  key: string;
  kind: 'creator' | 'stage';
  stageLabel: string;
  ownerName: string;
  statusText: string;
  visualState: WorkflowVisualState;
  isCurrentHolder: boolean;
}

const props = withDefaults(defineProps<{
  task: AgentTask;
  events?: AgentTaskEvent[];
  creatorLabel: string;
  assigneeLabel: string;
  reviewerLabel: string;
  actorLabel: (id: number) => string;
}>(), {
  events: () => [],
});

const { t } = useI18n();

function fallbackStages(task: AgentTask): AgentTaskStage[] {
  const isDone = task.status === 'DONE';
  const hasReviewer = typeof task.reviewer_id === 'number' && task.reviewer_id > 0;
  const isReviewing = task.status === 'REVIEWING' && hasReviewer;

  const executionStatus: AgentTaskStageStatus = isDone || isReviewing ? 'APPROVED' : 'ACTIVE';
  const stages: AgentTaskStage[] = [
    {
      stage_index: 0,
      node_type: 'EXECUTION',
      name: '',
      owner_id: task.assignee_id,
      owner_name: props.assigneeLabel || props.actorLabel(task.assignee_id),
      stage_status: executionStatus,
    },
  ];

  if (hasReviewer && task.reviewer_id !== null) {
    const reviewStatus: AgentTaskStageStatus = isDone
      ? 'APPROVED'
      : isReviewing
        ? 'ACTIVE'
        : 'WAITING';
    stages.push({
      stage_index: 1,
      node_type: 'REVIEW',
      name: '',
      owner_id: task.reviewer_id,
      owner_name: props.reviewerLabel || props.actorLabel(task.reviewer_id),
      stage_status: reviewStatus,
    });
  }

  return stages;
}

const rejectedStages = computed<Set<number>>(() => {
  const rejected = new Set<number>();
  const sorted = [...props.events].sort((a, b) => a.id - b.id);
  for (const event of sorted) {
    if (event.action_type === 'REJECT' && typeof event.from_stage === 'number') {
      rejected.add(event.from_stage);
    } else if (event.action_type === 'ADVANCE' && typeof event.from_stage === 'number') {
      rejected.delete(event.from_stage);
    }
  }
  return rejected;
});

function resolveStagePresentation(
  stage: AgentTaskStage,
  taskStatus: AgentTask['status'],
  rejectedSet: Set<number>,
): { visualState: WorkflowVisualState; statusText: string; isCurrentHolder: boolean } {
  if (stage.stage_status === 'APPROVED' || taskStatus === 'DONE') {
    return {
      visualState: 'approved',
      statusText:
        stage.node_type === 'REVIEW'
          ? t('agent.taskStageStatusApproved')
          : t('agent.taskStageStatusCompleted'),
      isCurrentHolder: false,
    };
  }

  if (stage.stage_status === 'ACTIVE') {
    if (taskStatus === 'PENDING') {
      return {
        visualState: 'waiting',
        statusText: t('agent.taskStatus.PENDING'),
        isCurrentHolder: false,
      };
    }
    if (taskStatus === 'TODO') {
      return {
        visualState: 'todo',
        statusText: t('agent.taskWorkflowPendingClaim'),
        isCurrentHolder: true,
      };
    }
    if (taskStatus === 'ON_HOLD') {
      return {
        visualState: 'hold',
        statusText: t('agent.taskStatus.ON_HOLD'),
        isCurrentHolder: true,
      };
    }
    if (taskStatus === 'CANCELLED') {
      return {
        visualState: 'cancelled',
        statusText: t('agent.taskStatus.CANCELLED'),
        isCurrentHolder: false,
      };
    }
    return {
      visualState: 'active',
      statusText:
        stage.node_type === 'REVIEW'
          ? t('agent.taskStatus.REVIEWING')
          : t('agent.taskStatus.IN_PROGRESS'),
      isCurrentHolder: true,
    };
  }

  if (taskStatus === 'CANCELLED') {
    return {
      visualState: 'cancelled',
      statusText: t('agent.taskStatus.CANCELLED'),
      isCurrentHolder: false,
    };
  }

  if (rejectedSet.has(stage.stage_index)) {
    return {
      visualState: 'rejected',
      statusText: t('agent.taskStageStatusRejected'),
      isCurrentHolder: false,
    };
  }

  return {
    visualState: 'waiting',
    statusText: t('agent.taskStageStatusWaiting'),
    isCurrentHolder: false,
  };
}

const nodes = computed<WorkflowNode[]>(() => {
  const task = props.task;
  const creatorNode: WorkflowNode = {
    key: 'creator',
    kind: 'creator',
    stageLabel: t('agent.taskWorkflowCreate'),
    ownerName: props.creatorLabel || props.actorLabel(task.creator_id),
    statusText: t('agent.taskWorkflowCreated'),
    visualState: 'approved',
    isCurrentHolder: false,
  };

  const stages = Array.isArray(task.stages) && task.stages.length > 0
    ? task.stages
    : fallbackStages(task);

  const rejectedSet = rejectedStages.value;
  const stageNodes: WorkflowNode[] = stages.map((stage) => {
    const presentation = resolveStagePresentation(stage, task.status, rejectedSet);
    const defaultRoleLabel =
      stage.node_type === 'REVIEW'
        ? t('agent.taskStageTypeReview')
        : t('agent.taskStageTypeExecution');
    return {
      key: `stage-${stage.stage_index}`,
      kind: 'stage',
      stageLabel: stage.name || defaultRoleLabel,
      ownerName: stage.owner_name || props.actorLabel(stage.owner_id),
      statusText: presentation.statusText,
      visualState: presentation.visualState,
      isCurrentHolder: presentation.isCurrentHolder,
    };
  });

  return [creatorNode, ...stageNodes];
});

function isConnectorCompleted(node: WorkflowNode): boolean {
  return node.visualState === 'approved';
}
</script>

<template>
  <div class="task-workflow-graph" role="list">
    <template v-for="(node, index) in nodes" :key="node.key">
      <div
        v-if="index > 0"
        class="task-workflow-graph__edge"
        :class="{ 'is-done': isConnectorCompleted(nodes[index - 1]) }"
        aria-hidden="true"
      >
        <span class="task-workflow-graph__line" />
        <span class="task-workflow-graph__arrow" />
      </div>

      <div
        class="task-workflow-graph__node"
        :data-state="node.visualState"
        :data-kind="node.kind"
        :class="{ 'is-current': node.isCurrentHolder }"
        :title="`${node.stageLabel} · ${node.ownerName}（${node.statusText}）`"
        role="listitem"
      >
        <div class="task-workflow-graph__vertex">
          <span class="task-workflow-graph__stage">{{ node.stageLabel }}</span>
        </div>

        <div class="task-workflow-graph__owner" :title="node.ownerName">
          {{ node.ownerName }}
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.task-workflow-graph {
  display: flex;
  align-items: flex-start;
  flex-wrap: nowrap;
  padding: 12px 14px;
  border-radius: 12px;
  background: color-mix(in srgb, var(--surface-soft) 84%, transparent);
  overflow-x: auto;
  scrollbar-width: thin;
  scrollbar-color: var(--scrollbar-thumb) transparent;
}

.task-workflow-graph::-webkit-scrollbar {
  height: 6px;
}

.task-workflow-graph::-webkit-scrollbar-thumb {
  background: var(--scrollbar-thumb);
  border-radius: 999px;
}

.task-workflow-graph__node {
  flex: 0 0 auto;
  width: 68px;
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: 5px;
}

.task-workflow-graph__vertex {
  width: 36px;
  height: 36px;
  padding: 3px;
  border-radius: 999px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px dashed color-mix(in srgb, var(--panel-border-strong) 78%, var(--muted) 22%);
  background: var(--panel-bg);
  color: var(--text-secondary);
  transition: border-color 160ms ease, background 160ms ease, box-shadow 160ms ease;
}

.task-workflow-graph__stage {
  font-size: 0.6rem;
  font-weight: 600;
  line-height: 1.12;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-word;
}

/* 已执行：绿色实线边框 */
.task-workflow-graph__node[data-state='approved'] .task-workflow-graph__vertex {
  border-style: solid;
  border-color: var(--good);
  background: color-mix(in srgb, var(--good) 10%, var(--panel-bg) 90%);
  color: var(--good);
}

/* 当前节点（进行中 / 待认领 / 暂停）：蓝色加粗实线边框 + 右上角活跃圆点 */
.task-workflow-graph__node[data-state='active'] .task-workflow-graph__vertex,
.task-workflow-graph__node[data-state='todo'] .task-workflow-graph__vertex,
.task-workflow-graph__node[data-state='hold'] .task-workflow-graph__vertex {
  position: relative;
  border-style: solid;
  border-width: 1px;
  border-color: var(--state-info);
  background: color-mix(in srgb, var(--state-info) 16%, var(--panel-bg) 84%);
  color: var(--state-info);
}

.task-workflow-graph__node[data-state='active'] .task-workflow-graph__vertex::after,
.task-workflow-graph__node[data-state='todo'] .task-workflow-graph__vertex::after,
.task-workflow-graph__node[data-state='hold'] .task-workflow-graph__vertex::after {
  content: '';
  position: absolute;
  top: -2px;
  right: -2px;
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: var(--state-info);
  border: 1.5px solid var(--panel-bg);
}

.task-workflow-graph__node[data-state='active'] .task-workflow-graph__owner,
.task-workflow-graph__node[data-state='todo'] .task-workflow-graph__owner,
.task-workflow-graph__node[data-state='hold'] .task-workflow-graph__owner {
  color: var(--state-info);
  font-weight: 700;
}

/* 被打回后等待重审的节点：红色实线边框 */
.task-workflow-graph__node[data-state='rejected'] .task-workflow-graph__vertex {
  border-style: solid;
  border-color: var(--danger);
  background: color-mix(in srgb, var(--danger) 12%, var(--panel-bg) 88%);
  color: var(--danger);
}

.task-workflow-graph__node[data-state='rejected'] .task-workflow-graph__owner {
  color: var(--danger);
  font-weight: 600;
}

/* 未执行：灰色虚线边框 */
.task-workflow-graph__node[data-state='waiting'] .task-workflow-graph__vertex {
  border-style: dashed;
  border-color: color-mix(in srgb, var(--panel-border-strong) 82%, var(--muted) 18%);
  color: var(--muted);
}

/* 已取消：灰色实线边框 */
.task-workflow-graph__node[data-state='cancelled'] .task-workflow-graph__vertex {
  border-style: solid;
  border-color: color-mix(in srgb, var(--text-secondary) 36%, var(--panel-border) 64%);
  color: var(--text-secondary);
}

.task-workflow-graph__owner {
  max-width: 100%;
  font-size: 0.74rem;
  font-weight: 600;
  color: var(--text-strong);
  line-height: 1.25;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.task-workflow-graph__node[data-state='waiting'] .task-workflow-graph__owner,
.task-workflow-graph__node[data-state='cancelled'] .task-workflow-graph__owner {
  color: var(--text-secondary);
  font-weight: 500;
}

.task-workflow-graph__edge {
  flex: 0 0 28px;
  height: 36px;
  display: flex;
  align-items: center;
  margin: 0 2px;
}

.task-workflow-graph__line {
  flex: 1;
  height: 0;
  border-top: 1px dashed color-mix(in srgb, var(--panel-border-strong) 72%, transparent);
}

.task-workflow-graph__arrow {
  width: 0;
  height: 0;
  border-top: 3.5px solid transparent;
  border-bottom: 3.5px solid transparent;
  border-left: 5px solid color-mix(in srgb, var(--panel-border-strong) 72%, transparent);
}

.task-workflow-graph__edge.is-done .task-workflow-graph__line {
  border-top-style: solid;
  border-top-color: color-mix(in srgb, var(--good) 65%, transparent);
}

.task-workflow-graph__edge.is-done .task-workflow-graph__arrow {
  border-left-color: color-mix(in srgb, var(--good) 65%, transparent);
}
</style>

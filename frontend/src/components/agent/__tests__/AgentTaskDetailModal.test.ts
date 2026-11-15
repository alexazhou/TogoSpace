import { beforeEach, describe, expect, it, vi } from 'vitest';
import { flushPromises, mount } from '@vue/test-utils';
import AgentTaskDetailModal from '../AgentTaskDetailModal.vue';
import i18n from '../../../i18n';
import type { AgentTask, AgentTaskDependencySummary } from '../../../types';

const { getTaskDetailMock, getTaskEventsPageMock } = vi.hoisted(() => ({
  getTaskDetailMock: vi.fn(),
  getTaskEventsPageMock: vi.fn(),
}));

vi.mock('../../../api', () => ({
  getTaskDetail: getTaskDetailMock,
  getTaskEventsPage: getTaskEventsPageMock,
}));

vi.mock('../../../realtime/wsClient', () => ({
  subscribeRealtimeEvents: () => () => {},
}));

function baseTask(overrides: Partial<AgentTask> = {}): AgentTask {
  return {
    id: 155,
    team_id: 1,
    title: '测试任务',
    description: '',
    assignee_id: 2,
    creator_id: 1,
    reviewer_id: null,
    status: 'PENDING',
    priority: 'HIGH',
    parent_id: null,
    depends_on: [],
    room_id: null,
    created_at: '2026-11-08 01:36:02',
    updated_at: '2026-11-08 01:36:02',
    depends_on_details: [],
    ...overrides,
  };
}

function dependency(overrides: Partial<AgentTaskDependencySummary> = {}): AgentTaskDependencySummary {
  return {
    task_id: 153,
    title: '托管容器加 note 说明字段：create_container 可传、list_containers 可见（DB+迁移+工具契约）',
    status: 'IN_PROGRESS',
    ...overrides,
  };
}

async function mountModal(task: AgentTask) {
  getTaskDetailMock.mockResolvedValue(task);
  getTaskEventsPageMock.mockResolvedValue({ events: [], total: 0 });

  const wrapper = mount(AgentTaskDetailModal, {
    props: {
      taskId: task.id,
      task,
      creatorLabel: '创建人',
      assigneeLabel: '处理人',
      reviewerLabel: '验收人',
      roomLabel: '房间',
      actorLabel: () => 'actor',
    },
    global: { plugins: [i18n], stubs: { teleport: true } },
  });
  await flushPromises();
  return wrapper;
}

describe('AgentTaskDetailModal 依赖任务列表', () => {
  beforeEach(() => {
    getTaskDetailMock.mockReset();
    getTaskEventsPageMock.mockReset();
  });

  it('每个依赖单独一行，带编号、标题与状态', async () => {
    const wrapper = await mountModal(baseTask({
      depends_on: [153, 154],
      depends_on_details: [
        dependency(),
        dependency({ task_id: 154, title: '第二个依赖', status: 'DONE' }),
      ],
    }));

    const rows = wrapper.findAll('.task-detail-modal__dep');
    expect(rows).toHaveLength(2);
    // 编号单独渲染（弱化色、等宽数字），与正文样式区分
    expect(rows[0].find('.task-detail-modal__dep-id').text()).toBe('#153');
    expect(rows[0].find('.task-detail-modal__dep-text').text()).toBe(
      '托管容器加 note 说明字段：create_container 可传、list_containers 可见（DB+迁移+工具契约）（进行中）',
    );
    // 单行展示会被省略号截断，完整内容放 title 供 hover 查看
    expect(rows[0].attributes('title')).toBe(
      '#153 托管容器加 note 说明字段：create_container 可传、list_containers 可见（DB+迁移+工具契约）（进行中）',
    );
    expect(rows[1].find('.task-detail-modal__dep-id').text()).toBe('#154');
    expect(rows[1].find('.task-detail-modal__dep-text').text()).toBe('第二个依赖（已完成）');
  });

  it('已删除的依赖仍显示编号与标题', async () => {
    const wrapper = await mountModal(baseTask({
      depends_on: [153],
      depends_on_details: [dependency({ status: 'UNKNOWN' })],
    }));

    const rows = wrapper.findAll('.task-detail-modal__dep');
    expect(rows).toHaveLength(1);
    expect(rows[0].text()).toContain('#153');
    expect(rows[0].text()).toContain('（已删除）');
  });

  it('没有依赖详情时回退为编号列表', async () => {
    const wrapper = await mountModal(baseTask({ depends_on: [153, 154] }));

    expect(wrapper.findAll('.task-detail-modal__dep')).toHaveLength(0);
    const sectionText = wrapper.text();
    expect(sectionText).toContain('#153, #154');
  });
});

describe('AgentTaskDetailModal 多节点流转图', () => {
  beforeEach(() => {
    getTaskDetailMock.mockReset();
    getTaskEventsPageMock.mockReset();
  });

  it('用多节点连线图替代创建人/处理人/验收人字段，并展示多级节点与状态', async () => {
    const wrapper = await mountModal(baseTask({
      status: 'REVIEWING',
      current_stage: 1,
      stages: [
        {
          stage_index: 0,
          node_type: 'EXECUTION',
          name: '后端开发',
          owner_id: 2,
          owner_name: '网关开发',
          stage_status: 'APPROVED',
        },
        {
          stage_index: 1,
          node_type: 'REVIEW',
          name: '组长初审',
          owner_id: 3,
          owner_name: '网关测试',
          stage_status: 'ACTIVE',
        },
        {
          stage_index: 2,
          node_type: 'REVIEW',
          name: '总监终审',
          owner_id: 4,
          owner_name: '技术总监',
          stage_status: 'WAITING',
        },
      ],
    }));

    // 原来的创建人/处理人/验收人已不在顶部元数据网格中
    const dtLabels = wrapper.findAll('.task-detail-modal__grid dt').map((el) => el.text());
    expect(dtLabels).toEqual(['任务 ID', '创建时间', '房间']);

    // 流转图渲染 4 个节点（创建人 + 3 个 stage）与 3 条连线
    const nodes = wrapper.findAll('.task-workflow-graph__node');
    const edges = wrapper.findAll('.task-workflow-graph__edge');
    expect(nodes).toHaveLength(4);
    expect(edges).toHaveLength(3);

    expect(nodes[0].find('.task-workflow-graph__stage').text()).toBe('创建');
    expect(nodes[0].find('.task-workflow-graph__owner').text()).toBe('创建人');
    expect(nodes[0].attributes('data-state')).toBe('approved');
    expect(edges[0].classes()).toContain('is-done');

    expect(nodes[1].find('.task-workflow-graph__stage').text()).toBe('后端开发');
    expect(nodes[1].find('.task-workflow-graph__owner').text()).toBe('网关开发');
    expect(nodes[1].attributes('data-state')).toBe('approved');
    expect(edges[1].classes()).toContain('is-done');

    expect(nodes[2].find('.task-workflow-graph__stage').text()).toBe('组长初审');
    expect(nodes[2].find('.task-workflow-graph__owner').text()).toBe('网关测试');
    expect(nodes[2].attributes('data-state')).toBe('active');
    expect(edges[2].classes()).not.toContain('is-done');

    expect(nodes[3].find('.task-workflow-graph__stage').text()).toBe('总监终审');
    expect(nodes[3].find('.task-workflow-graph__owner').text()).toBe('技术总监');
    expect(nodes[3].attributes('data-state')).toBe('waiting');
  });

  it('缺少 stages 时按 assignee_id / reviewer_id 回退生成节点链', async () => {
    const wrapper = await mountModal(baseTask({
      status: 'IN_PROGRESS',
      assignee_id: 2,
      reviewer_id: 3,
    }));

    const nodes = wrapper.findAll('.task-workflow-graph__node');
    expect(nodes).toHaveLength(3);
    expect(nodes[1].find('.task-workflow-graph__stage').text()).toBe('执行');
    expect(nodes[1].attributes('data-state')).toBe('active');
    expect(nodes[2].find('.task-workflow-graph__stage').text()).toBe('验收');
    expect(nodes[2].attributes('data-state')).toBe('waiting');
  });

  it('任务处于 TODO / ON_HOLD / DONE 时正确映射当前节点状态', async () => {
    const todoWrapper = await mountModal(baseTask({
      status: 'TODO',
      stages: [
        {
          stage_index: 0,
          node_type: 'EXECUTION',
          name: '',
          owner_id: 2,
          owner_name: '小马哥',
          stage_status: 'ACTIVE',
        },
      ],
    }));
    expect(todoWrapper.findAll('.task-workflow-graph__node')[1].attributes('data-state')).toBe('todo');

    const holdWrapper = await mountModal(baseTask({
      status: 'ON_HOLD',
      stages: [
        {
          stage_index: 0,
          node_type: 'EXECUTION',
          name: '',
          owner_id: 2,
          owner_name: '小马哥',
          stage_status: 'ACTIVE',
        },
      ],
    }));
    expect(holdWrapper.findAll('.task-workflow-graph__node')[1].attributes('data-state')).toBe('hold');

    const doneWrapper = await mountModal(baseTask({
      status: 'DONE',
      stages: [
        {
          stage_index: 0,
          node_type: 'EXECUTION',
          name: '',
          owner_id: 2,
          owner_name: '小马哥',
          stage_status: 'APPROVED',
        },
        {
          stage_index: 1,
          node_type: 'REVIEW',
          name: '',
          owner_id: 3,
          owner_name: '验收员',
          stage_status: 'APPROVED',
        },
      ],
    }));
    const doneNodes = doneWrapper.findAll('.task-workflow-graph__node');
    expect(doneNodes[1].attributes('data-state')).toBe('approved');
    expect(doneNodes[2].attributes('data-state')).toBe('approved');
  });
});

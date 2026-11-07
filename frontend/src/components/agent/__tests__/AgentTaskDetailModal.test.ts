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

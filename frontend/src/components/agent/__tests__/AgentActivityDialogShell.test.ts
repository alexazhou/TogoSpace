import { describe, expect, it } from 'vitest';
import { mount } from '@vue/test-utils';
import AgentActivityDialogShell from '../AgentActivityDialogShell.vue';

function mountShell(activeTab: 'activities' | 'tasks' | 'memories' | 'properties' | 'container' = 'activities') {
  return mount(AgentActivityDialogShell, {
    props: {
      open: true,
      closeLabel: '关闭',
      activeTab,
      panelTabsLabel: 'Agent 面板分页',
      activitiesLabel: '运行活动',
      tasksLabel: '任务',
      memoriesLabel: '记忆',
      propertiesLabel: '属性',
      containerLabel: '容器',
      activityBadgeLabel: '已连接',
      activityRealtimeState: 'connected',
      taskCountLabel: '0 个任务',
      memoryCountLabel: '0 条记忆',
      errorMessage: '',
      loadingMessage: '加载中',
      showLoading: false,
      showStage: true,
      showSupervise: false,
    },
    global: {
      stubs: {
        Teleport: { template: '<div><slot /></div>' },
      },
    },
  });
}

function tabLabels(wrapper: ReturnType<typeof mountShell>): string[] {
  return wrapper.findAll('.agent-activity-panel__tab').map((tab) => tab.text());
}

describe('AgentActivityDialogShell 容器 Tab', () => {
  it('renders the container tab after properties', () => {
    expect(tabLabels(mountShell())).toEqual(['运行活动', '任务', '记忆', '属性', '容器']);
  });

  it('emits update:activeTab with container when clicked', async () => {
    const wrapper = mountShell();
    const containerTab = wrapper.findAll('.agent-activity-panel__tab').find((tab) => tab.text() === '容器');
    expect(containerTab).toBeTruthy();

    await containerTab!.trigger('click');

    const emitted = wrapper.emitted('update:activeTab') ?? [];
    expect(emitted[emitted.length - 1]).toEqual(['container']);
  });

  it('marks only the active tab as selected', () => {
    const wrapper = mountShell('container');
    const selected = wrapper
      .findAll('.agent-activity-panel__tab')
      .filter((tab) => tab.attributes('aria-selected') === 'true')
      .map((tab) => tab.text());

    expect(selected).toEqual(['容器']);
  });
});

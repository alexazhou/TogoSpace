import { describe, expect, it } from 'vitest';
import { mount } from '@vue/test-utils';
import AgentContainerPanel from '../AgentContainerPanel.vue';
import i18n from '../../../i18n';
import type { AgentDetail, ContainerRuntimeInfo } from '../../../types';

function createContainer(overrides: Partial<ContainerRuntimeInfo> = {}): ContainerRuntimeInfo {
  return {
    record_id: 1,
    agent_id: 5,
    container_id: 'abc123def456',
    container_name: 'agent-sandbox',
    image: 'python:3.11',
    state: 'running',
    is_entered: false,
    created_at: '2026-11-05T10:00:00',
    ...overrides,
  };
}

function createAgent(overrides: Partial<AgentDetail> = {}): AgentDetail {
  return {
    id: 5,
    name: 'Alice',
    i18n: {},
    model: '',
    status: 'idle',
    exec_env: 'container',
    current_container_id: 'abc123def456',
    container_count: 1,
    containers: [createContainer()],
    ...overrides,
  };
}

function mountPanel(agent: AgentDetail | null) {
  return mount(AgentContainerPanel, {
    props: { initialAgent: agent },
    global: { plugins: [i18n] },
  });
}

describe('AgentContainerPanel', () => {
  it('uses 环境 as the section title and 执行环境 as the row label', () => {
    const wrapper = mountPanel(createAgent());
    expect(wrapper.find('.section-title').text()).toBe('环境');
    expect(wrapper.find('.row-label').text()).toBe('执行环境');
  });

  it('renders the environment value as 执行环境：<value>', () => {
    const wrapper = mountPanel(createAgent({ exec_env: 'host', current_container_id: null }));
    const row = wrapper.findAll('.config-mode-row')[0];
    expect(row.text()).toBe('执行环境宿主');
    expect(row.find('.env-value').text()).toBe('宿主');
  });

  it('shows the container environment value and current container id', () => {
    const wrapper = mountPanel(createAgent());
    expect(wrapper.text()).toContain('容器');
    expect(wrapper.text()).toContain('abc123def456');
  });

  it('renders one table row per container with the raw docker state', () => {
    const wrapper = mountPanel(createAgent({
      container_count: 2,
      containers: [
        createContainer({ record_id: 1, container_id: 'abc123def456', is_entered: true }),
        createContainer({
          record_id: 2,
          container_id: null,
          container_name: 'pending-box',
          image: 'ubuntu:24.04',
          state: 'created',
        }),
      ],
    }));

    const rows = wrapper.findAll('.container-table tbody tr');
    expect(rows).toHaveLength(2);
    // 预记录（container_id 为 null）显示「创建中」，而不是空白。
    expect(rows[1].text()).toContain('创建中');
    expect(rows[1].text()).toContain('created');
    // 「当前使用」徽标只标在当前所处的容器上。
    expect(rows[0].text()).toContain('当前使用');
    expect(rows[1].text()).not.toContain('当前使用');
  });

  it('falls back to the empty hint when the agent has no containers', () => {
    const wrapper = mountPanel(createAgent({ containers: [], container_count: 0 }));
    expect(wrapper.find('.container-table').exists()).toBe(false);
    expect(wrapper.text()).toContain('暂无容器');
  });

  it('treats a missing agent as host environment', () => {
    const wrapper = mountPanel(null);
    expect(wrapper.text()).toContain('宿主');
    expect(wrapper.find('.mono').exists()).toBe(false);
  });
});

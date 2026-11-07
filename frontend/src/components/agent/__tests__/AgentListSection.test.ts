import { describe, expect, it } from 'vitest';
import { mount } from '@vue/test-utils';
import AgentListSection from '../AgentListSection.vue';
import i18n from '../../../i18n';
import type { AgentInfo } from '../../../types';

function createAgent(overrides: Partial<AgentInfo> = {}): AgentInfo {
  return {
    id: 8,
    name: '小美',
    i18n: {},
    model: 'mock-model',
    status: 'idle',
    ...overrides,
  };
}

function mountSection(agents: AgentInfo[]) {
  return mount(AgentListSection, {
    props: { agents },
    global: { plugins: [i18n] },
  });
}

describe('AgentListSection 团队成员徽标', () => {
  it('宿主态不渲染容器徽标', () => {
    const wrapper = mountSection([createAgent({ exec_env: 'host' })]);

    expect(wrapper.find('.agent-env-badge').exists()).toBe(false);
  });

  it('容器态只显示「容器」，具体容器名放在 hover title', () => {
    const wrapper = mountSection([
      createAgent({
        exec_env: 'container',
        current_container_id: 'bd0fe74c76d4',
        current_container_name: 'agent-t2-xiao-mei-7',
        container_count: 2,
      }),
    ]);

    const badge = wrapper.find('.agent-env-badge');
    expect(badge.text()).toBe('容器');
    // 短 ID 不再占徽标位置；名称与容器总数只在 hover 时才出现
    expect(badge.attributes('title')).toBe('容器 · agent-t2-xiao-mei-7\n容器数: 2');
  });

  it('没有容器名时回退到短 ID；没有自建容器时不追加数量', () => {
    const wrapper = mountSection([
      createAgent({
        exec_env: 'container',
        current_container_id: 'bd0fe74c76d4',
        current_container_name: null,
        container_count: 0,
      }),
    ]);

    const badge = wrapper.find('.agent-env-badge');
    expect(badge.text()).toBe('容器');
    expect(badge.attributes('title')).toBe('容器 · bd0fe74c76d4');
  });
});

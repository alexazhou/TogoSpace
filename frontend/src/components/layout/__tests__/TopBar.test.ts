import { describe, expect, it } from 'vitest';
import { mount } from '@vue/test-utils';
import TopBar from '../TopBar.vue';
import i18n from '../../../i18n';
import type { ConnectionState } from '../../../utils';

function mountTopBar(overrides: {
  activeAgentCount?: number;
  activeAgentNames?: string[];
} = {}) {
  return mount(TopBar, {
    props: {
      connectionState: 'connected' as ConnectionState,
      isLightMode: false,
      statusLabel: '已连接',
      reconnectProgress: 0,
      activeAgentCount: 2,
      activeAgentNames: ['Alice', 'Bob'],
      teams: [],
      activeTeamId: null,
      activeTeamEnabled: false,
      activeTeamEnabledPending: false,
      showConnectionStatus: true,
      ...overrides,
    },
    global: { plugins: [i18n] },
  });
}

describe('TopBar active agents metric', () => {
  it('highlights the pill and lists active agents in the tooltip', () => {
    const wrapper = mountTopBar();
    const pill = wrapper.find('.metric-pill');

    expect(pill.classes()).toContain('metric-pill--active');
    expect(pill.text()).toContain('2 活跃');
    expect(wrapper.find('.metric-tooltip').text()).toContain('Alice');
    expect(wrapper.find('.metric-tooltip').text()).toContain('Bob');
  });

  it('keeps the pill muted and shows an empty hint when no agent is active', () => {
    const wrapper = mountTopBar({ activeAgentCount: 0, activeAgentNames: [] });
    const pill = wrapper.find('.metric-pill');

    expect(pill.classes()).not.toContain('metric-pill--active');
    expect(pill.text()).toContain('0 活跃');
    expect(wrapper.find('.metric-tooltip').text()).toContain('暂无活跃 Agent');
  });
});

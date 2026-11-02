import { beforeEach, describe, expect, it } from 'vitest';
import { nextTick } from 'vue';
import { mount } from '@vue/test-utils';
import ConsoleAgentListPanel from '../ConsoleAgentListPanel.vue';
import i18n from '../../../i18n';
import {
  applyRealtimeEvent,
  clearRuntimeStore,
  seedDeptTree,
  seedTeamAgents,
} from '../../../realtime/runtimeStore';
import type { AgentInfo, DeptTreeNode } from '../../../types';

const TEAM_ID = 7;

function createAgent(overrides: Partial<AgentInfo>): AgentInfo {
  return {
    id: 1,
    name: 'Agent',
    i18n: {},
    model: '',
    status: 'idle',
    ...overrides,
  };
}

function createDeptTree(): DeptTreeNode {
  return {
    id: 1,
    name: '研发部',
    responsibility: '',
    manager_id: 1,
    agent_ids: [1, 2, 3],
    children: [],
  };
}

function mountPanel() {
  return mount(ConsoleAgentListPanel, {
    props: { teamId: TEAM_ID },
    global: { plugins: [i18n] },
  });
}

function visibleNames(wrapper: ReturnType<typeof mountPanel>): string[] {
  return wrapper.findAll('.agent-name').map((item) => item.text());
}

describe('ConsoleAgentListPanel', () => {
  beforeEach(() => {
    clearRuntimeStore();
    seedDeptTree(TEAM_ID, createDeptTree());
    seedTeamAgents(TEAM_ID, [
      createAgent({ id: 1, name: 'Alice', status: 'idle' }),
      createAgent({ id: 2, name: 'Bob', status: 'active' }),
      createAgent({ id: 3, name: 'Carol', status: 'active' }),
    ]);
  });

  it('pins active members to the top and preserves the original order within each group', () => {
    expect(visibleNames(mountPanel())).toEqual(['Bob', 'Carol', 'Alice']);
  });

  it('reorders as agent status changes arrive', async () => {
    const wrapper = mountPanel();

    applyRealtimeEvent({
      type: 'agent_status',
      teamId: TEAM_ID,
      agentId: 1,
      agentName: 'Alice',
      status: 'active',
    });
    await nextTick();

    expect(visibleNames(wrapper)).toEqual(['Alice', 'Bob', 'Carol']);
  });
});

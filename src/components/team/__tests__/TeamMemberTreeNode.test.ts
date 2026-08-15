import { describe, expect, it } from 'vitest';
import { mount } from '@vue/test-utils';
import i18n from '../../../i18n';
import TeamMemberTreeNode from '../TeamMemberTreeNode.vue';
import type { TeamGraphNode } from '../teamGraphTypes';

function createMemberNode(overrides: Partial<TeamGraphNode> = {}): TeamGraphNode {
  return {
    id: 'agent-1',
    kind: 'member',
    agentId: 1,
    name: 'alice',
    departmentName: '',
    hasDepartment: true,
    subtitle: 'researcher',
    employeeNumber: '12',
    avatarName: 'alice',
    avatarSeed: 'team::alice',
    children: [],
    ...overrides,
  };
}

describe('TeamMemberTreeNode', () => {
  it('shows an explicit unassigned label for members outside the dept tree', () => {
    const wrapper = mount(TeamMemberTreeNode, {
      props: {
        node: createMemberNode({
          hasDepartment: false,
          departmentName: '未分配部门',
        }),
        readonly: true,
        showEditAction: false,
      },
      global: {
        plugins: [i18n],
      },
    });

    expect(wrapper.text()).toContain('未分配部门');
  });

  it('does not show a department label for regular leaf members', () => {
    const wrapper = mount(TeamMemberTreeNode, {
      props: {
        node: createMemberNode({
          hasDepartment: true,
          departmentName: '研发部',
        }),
        readonly: true,
        showEditAction: false,
      },
      global: {
        plugins: [i18n],
      },
    });

    expect(wrapper.text()).not.toContain('研发部');
  });
});

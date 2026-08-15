import { describe, it, expect, vi, beforeEach } from 'vitest';
import { flushPromises, mount } from '@vue/test-utils';
import TeamTreeEditor from '../TeamTreeEditor.vue';
import i18n from '../../../i18n';

// Mock dependencies
const {
  getTeamAgentsMock,
  getTeamOrgMock,
  getTeamConfigMock,
  getRoleTemplatesMock,
  getDeptTreeMock,
  getAgentsByTeamIdMock,
  getFrontendConfigMock,
} = vi.hoisted(() => ({
  getTeamAgentsMock: vi.fn(),
  getTeamOrgMock: vi.fn(),
  getTeamConfigMock: vi.fn(),
  getRoleTemplatesMock: vi.fn(),
  getDeptTreeMock: vi.fn(),
  getAgentsByTeamIdMock: vi.fn(),
  getFrontendConfigMock: vi.fn(),
}));

vi.mock('../../../api', () => ({
  getTeamAgents: getTeamAgentsMock,
  getTeamOrg: getTeamOrgMock,
  getTeamConfig: getTeamConfigMock,
  getRoleTemplates: getRoleTemplatesMock,
  getDeptTree: getDeptTreeMock,
  getAgentsByTeamId: getAgentsByTeamIdMock,
  getFrontendConfig: getFrontendConfigMock,
}));

describe('TeamTreeEditor.vue Cancel Edit Behavior', () => {
  beforeEach(() => {
    getTeamAgentsMock.mockResolvedValue([]);
    getTeamOrgMock.mockResolvedValue(null);
    getTeamConfigMock.mockResolvedValue({ models: [], drivers: [] });
    getRoleTemplatesMock.mockResolvedValue([]);
    getDeptTreeMock.mockResolvedValue({ 
      id: 1, 
      name: 'Root Dept', 
      manager_id: 1,
      agent_ids: [1],
      children: [] 
    });
    getAgentsByTeamIdMock.mockResolvedValue([
      { id: 1, name: 'Leader', employ_status: 'ON_BOARD' }
    ]);
    getFrontendConfigMock.mockResolvedValue({});
  });

  it('shows ConfirmDialog when cancelling with unsaved changes', async () => {
    getTeamOrgMock.mockResolvedValue({
      id: 'root-id',
      memberName: 'Leader',
      departmentName: 'Root',
      children: [],
    });

    const wrapper = mount(TeamTreeEditor, {
      props: {
        teamId: 1,
        teamName: 'Test Team',
        teamEnabled: false,
      },
      global: {
        plugins: [i18n],
      },
    });

    await flushPromises();

    // 1. Enter edit mode
    await wrapper.findComponent({ name: 'TeamMembersCard' }).vm.$emit('action', 'edit');
    await flushPromises();

    // 2. Trigger a change (edit department name) to make hasTeamMemberChanges true
    const card = wrapper.findComponent({ name: 'TeamMembersCard' });
    const rootNode = card.props('rootNode');
    const rootId = rootNode?.id || 'dept-1';
    
    // Fallback ID if undefined
    await card.vm.$emit('editDepartment', rootId);
    await flushPromises();
    
    const deptDialog = wrapper.findComponent({ name: 'DepartmentEditorDialog' });
    await deptDialog.vm.$emit('update:department-name', 'New Dept Name');
    await deptDialog.vm.$emit('save');
    await flushPromises();

    // 3. Trigger cancel
    await card.vm.$emit('action', 'cancel');
    await flushPromises();

    // The confirm dialog should be open with cancel-edit action type
    const confirmDialog = wrapper.findComponent({ name: 'ConfirmDialog' });
    expect(confirmDialog.exists()).toBe(true);
    expect(confirmDialog.props('open')).toBe(true);
    expect(confirmDialog.props('danger')).toBe(true);
  });

  it('does not show ConfirmDialog when cancelling with no unsaved changes', async () => {
    const errorSpy = vi.spyOn(console, 'error');
    
    const wrapper = mount(TeamTreeEditor, {
      props: {
        teamId: 1,
        teamName: 'Test Team',
        teamEnabled: false,
      },
      global: {
        plugins: [i18n],
      },
    });

    await flushPromises();

    // 1. Enter edit mode
    await wrapper.findComponent({ name: 'TeamMembersCard' }).vm.$emit('action', 'edit');
    await flushPromises();

    // 2. Trigger cancel immediately (no changes made)
    await wrapper.findComponent({ name: 'TeamMembersCard' }).vm.$emit('action', 'cancel');
    await flushPromises();

    // The confirm dialog should not be open
    const confirmDialog = wrapper.findComponent({ name: 'ConfirmDialog' });
    // confirmDialog is always rendered, but its `open` prop should be false
    expect(confirmDialog.exists()).toBe(true);
    expect(confirmDialog.props('open')).toBe(false);
  });
});

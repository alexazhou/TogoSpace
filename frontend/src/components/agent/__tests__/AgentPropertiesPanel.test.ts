import { describe, expect, it, vi, beforeEach } from 'vitest';
import { flushPromises, mount } from '@vue/test-utils';
import AgentPropertiesPanel from '../AgentPropertiesPanel.vue';
import i18n from '../../../i18n';
import type { AgentDetail } from '../../../types';

const { getRoleTemplateDetailMock } = vi.hoisted(() => ({
  getRoleTemplateDetailMock: vi.fn(),
}));

vi.mock('../../../api', () => ({
  getAvailableTools: vi.fn().mockResolvedValue([]),
  getAvailableSkills: vi.fn().mockResolvedValue([]),
  getFrontendConfig: vi.fn().mockResolvedValue({ model_slots: [] }),
  updateAgentProperties: vi.fn(),
  getRoleTemplateDetail: getRoleTemplateDetailMock,
}));

function createAgent(overrides: Partial<AgentDetail> = {}): AgentDetail {
  return {
    id: 5,
    name: 'Alice',
    agent_name: 'Alice',
    i18n: {},
    model: '',
    status: 'idle',
    driver_type: 'native',
    prompt: '',
    exec_env: 'host',
    current_container_id: null,
    role_template_id: 3,
    ...overrides,
  };
}

function mountPanel(agent: AgentDetail | null, roleTemplateName: string | null = null) {
  return mount(AgentPropertiesPanel, {
    props: { agentId: agent?.id ?? null, initialAgent: agent, roleTemplateName },
    global: { plugins: [i18n] },
  });
}

describe('AgentPropertiesPanel 角色模板', () => {
  beforeEach(() => {
    getRoleTemplateDetailMock.mockReset();
    getRoleTemplateDetailMock.mockResolvedValue({
      id: 3,
      name: 'architect',
      i18n: { display_name: '架构师' },
      soul: '# 架构师 Soul',
      type: 'system',
    });
  });

  it('渲染「角色模板」段，显示传入的模板展示名与「查看」按钮', async () => {
    const wrapper = mountPanel(createAgent(), '架构师');
    await flushPromises();

    const firstSection = wrapper.findAll('.form-section')[0];
    expect(firstSection.find('.section-title').text()).toBe('角色模板');
    expect(firstSection.find('.template-name').text()).toBe('架构师');
    expect(firstSection.find('.template-view-button').text()).toBe('查看');
  });

  it('未传展示名时退回「模板 #id」', async () => {
    const wrapper = mountPanel(createAgent(), null);
    await flushPromises();
    expect(wrapper.find('.template-name').text()).toBe('模板 #3');
  });

  it('无 role_template_id 时显示「未配置模板」且不显示「查看」按钮', async () => {
    const wrapper = mountPanel(createAgent({ role_template_id: null }), null);
    await flushPromises();
    expect(wrapper.find('.template-name').text()).toBe('未配置模板');
    expect(wrapper.find('.template-view-button').exists()).toBe(false);
  });

  it('点击「查看」按模板 ID 拉详情并展示类型标签与 Soul', async () => {
    const wrapper = mountPanel(createAgent(), '架构师');
    await flushPromises();

    await wrapper.find('.template-view-button').trigger('click');
    await flushPromises();

    expect(getRoleTemplateDetailMock).toHaveBeenCalledWith(3);
    const dialog = document.body.querySelector('.ui-modal__dialog');
    expect(dialog).not.toBeNull();
    expect(dialog?.textContent).toContain('系统模板');
    expect(dialog?.textContent).toContain('架构师 Soul');
    wrapper.unmount();
  });
});

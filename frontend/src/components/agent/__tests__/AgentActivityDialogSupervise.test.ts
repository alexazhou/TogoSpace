import { describe, expect, it, vi, beforeEach } from 'vitest';
import { flushPromises, mount } from '@vue/test-utils';
import AgentActivityDialog from '../AgentActivityDialog.vue';
import i18n from '../../../i18n';

/**
 * 追加指令输入框的键盘约定：与聊天输入框一致 —— Enter 发送、Shift+Enter 换行。
 */
const { getAgentDetailMock, superviseAgentMock } = vi.hoisted(() => ({
  getAgentDetailMock: vi.fn(),
  superviseAgentMock: vi.fn(),
}));

vi.mock('../../../api', () => ({
  getAgentDetail: getAgentDetailMock,
  resumeAgent: vi.fn(),
  stopAgent: vi.fn(),
  superviseAgent: superviseAgentMock,
}));

const childStubs = {
  AgentActivityPanel: true,
  AgentTaskPanel: true,
  AgentPropertiesPanel: true,
  AgentContainerPanel: true,
};

function mountDialog() {
  return mount(AgentActivityDialog, {
    props: {
      open: true,
      agentId: 7,
      agentName: '阿张',
      roleTemplateName: '架构师',
    },
    global: {
      plugins: [i18n],
      stubs: childStubs,
    },
    attachTo: document.body,
  });
}

function superviseTextarea(): HTMLTextAreaElement {
  const el = document.body.querySelector('.agent-supervise-section__textarea');
  expect(el).not.toBeNull();
  return el as HTMLTextAreaElement;
}

async function typeInto(el: HTMLTextAreaElement, value: string): Promise<void> {
  el.value = value;
  el.dispatchEvent(new Event('input'));
  await flushPromises();
}

function pressEnter(el: HTMLTextAreaElement, init: KeyboardEventInit = {}): KeyboardEvent {
  const event = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true, ...init });
  el.dispatchEvent(event);
  return event;
}

describe('AgentActivityDialog 追加指令输入框', () => {
  beforeEach(() => {
    document.body.innerHTML = '';
    getAgentDetailMock.mockReset();
    superviseAgentMock.mockReset();
    getAgentDetailMock.mockResolvedValue({
      id: 7,
      name: '阿张',
      agent_name: '阿张',
      i18n: {},
      model: '',
      status: 'idle',
      driver_type: 'native',
      prompt: '',
      exec_env: 'host',
      current_container_id: null,
      role_template_id: 3,
    });
    superviseAgentMock.mockResolvedValue(undefined);
  });

  it('Enter 直接发送（阻止默认换行）', async () => {
    const wrapper = mountDialog();
    await flushPromises();

    const el = superviseTextarea();
    await typeInto(el, '先跑一遍单测');
    const event = pressEnter(el);
    await flushPromises();

    expect(event.defaultPrevented).toBe(true);
    expect(superviseAgentMock).toHaveBeenCalledWith(7, '先跑一遍单测');
    wrapper.unmount();
  });

  it('Shift+Enter 不发送（留给换行）', async () => {
    const wrapper = mountDialog();
    await flushPromises();

    const el = superviseTextarea();
    await typeInto(el, '第一行');
    const event = pressEnter(el, { shiftKey: true });
    await flushPromises();

    expect(event.defaultPrevented).toBe(false);
    expect(superviseAgentMock).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it('输入法组词中的 Enter 不发送', async () => {
    const wrapper = mountDialog();
    await flushPromises();

    const el = superviseTextarea();
    await typeInto(el, 'zhong');
    const event = pressEnter(el, { isComposing: true });
    await flushPromises();

    expect(event.defaultPrevented).toBe(false);
    expect(superviseAgentMock).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it('内容为空时 Enter 不发送', async () => {
    const wrapper = mountDialog();
    await flushPromises();

    const el = superviseTextarea();
    pressEnter(el);
    await flushPromises();

    expect(superviseAgentMock).not.toHaveBeenCalled();
    wrapper.unmount();
  });
});

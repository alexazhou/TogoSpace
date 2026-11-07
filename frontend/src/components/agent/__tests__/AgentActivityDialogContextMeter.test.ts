import { beforeEach, describe, expect, it, vi } from 'vitest';
import { flushPromises, mount } from '@vue/test-utils';
import AgentActivityDialog from '../AgentActivityDialog.vue';
import i18n from '../../../i18n';
import type { AgentContextUsage } from '../../../types';
import {
  applyRealtimeEvent,
  clearRuntimeStore,
  getAgentContextUsage,
} from '../../../realtime/runtimeStore';

/**
 * 弹窗与进度条的接线：**详情接口 → store 播种 → 组件消费**。
 *
 * 这三段任意一段断掉，现象都一样（进度条不出现），只测组件本身查不出来。
 * 这里用真实 store + 真实进度条子组件（**不 stub 它**）打穿整条链路。
 *
 * 注意：弹窗外壳用了 `<Teleport to="body">`，`wrapper.find` 看不到内容，
 * 必须 `attachTo: document.body` 后查询 `document.body`。
 */

const { getAgentDetailMock } = vi.hoisted(() => ({
  getAgentDetailMock: vi.fn(),
}));

vi.mock('../../../api', () => ({
  getAgentDetail: getAgentDetailMock,
  resumeAgent: vi.fn(),
  stopAgent: vi.fn(),
  superviseAgent: vi.fn(),
  compactAgentContext: vi.fn(),
}));

const USAGE: AgentContextUsage = {
  used_tokens: 40000,
  limit_tokens: 114688,
  compact_trigger_tokens: 97484,
  context_window_tokens: 131072,
  ratio: 40000 / 114688,
};

const childStubs = {
  AgentActivityPanel: true,
  AgentTaskPanel: true,
  AgentMemoryPanel: true,
  AgentPropertiesPanel: true,
  AgentContainerPanel: true,
};

function mountDialog(agentId = 7) {
  return mount(AgentActivityDialog, {
    props: { open: true, agentId, agentName: '阿张', roleTemplateName: '工程师' },
    global: { plugins: [i18n], stubs: childStubs },
    attachTo: document.body,
  });
}

function meterEl(): Element | null {
  return document.body.querySelector('.agent-context-meter');
}

function baseDetail(overrides: Record<string, unknown> = {}) {
  return {
    id: 7,
    name: '阿张',
    i18n: {},
    model: '',
    status: 'idle',
    exec_env: 'host',
    current_container_id: null,
    role_template_id: 3,
    ...overrides,
  };
}

beforeEach(() => {
  document.body.innerHTML = '';
  clearRuntimeStore();
  getAgentDetailMock.mockReset();
});

describe('AgentActivityDialog 上下文进度条接线', () => {
  it('详情接口的 context_usage 会播种到 store 并渲染进度条', async () => {
    getAgentDetailMock.mockResolvedValue(baseDetail({ context_usage: USAGE }));
    const wrapper = mountDialog();
    await flushPromises();

    // 1) store 被播种
    expect(getAgentContextUsage(7)).toEqual(USAGE);
    // 2) 进度条真的渲染出来（不是只存了数）
    const el = meterEl();
    expect(el).not.toBeNull();
    expect(el!.getAttribute('data-tier')).toBe('normal');

    wrapper.unmount();
  });

  it('详情返回 context_usage = null 时整条不渲染（不是渲染 0%）', async () => {
    getAgentDetailMock.mockResolvedValue(baseDetail({ context_usage: null }));
    const wrapper = mountDialog();
    await flushPromises();

    expect(getAgentContextUsage(7)).toBeNull();
    expect(meterEl()).toBeNull();
    // 弹窗本身正常打开，只是没有那一条 —— 不能因为「这条画不出来」整体报错
    expect(document.body.textContent).not.toContain('加载 Agent 信息失败');

    wrapper.unmount();
  });

  it('字段整体缺省（老后端）时同样不渲染，且不报错', async () => {
    getAgentDetailMock.mockResolvedValue(baseDetail());
    const wrapper = mountDialog();
    await flushPromises();

    expect(meterEl()).toBeNull();
    // 若 `seedAgentContextUsage` 没 import 成功，这里会抛 ReferenceError 并被 catch 成
    // 「加载失败」，所以这条断言同时守住了接线完整性。
    expect(document.body.textContent).not.toContain('加载 Agent 信息失败');

    wrapper.unmount();
  });

  it('已有推送值、详情明确返 null 时进度条消失（null ≠ 字段缺省）', async () => {
    // 两种「没有数值」必须分开处理：
    // - 字段缺省（undefined）= 这条响应没带该字段，不代表旧值失效 → 保留；
    // - 明确 null = 后端表态「此刻算不出来」→ 必须清掉。
    // 混淆任一侧都会造成「显示的数值与后端不一致」：保留旧值等于在断言
    // 「上下文还是那么多」，而实际是「不知道」。
    getAgentDetailMock.mockResolvedValue(baseDetail());
    const wrapper = mountDialog();
    await flushPromises();

    applyRealtimeEvent({ type: 'agent_context_usage', teamId: 1, agentId: 7, contextUsage: USAGE });
    await flushPromises();
    expect(meterEl()).not.toBeNull();

    // 重新打开弹窗，这次详情明确返回 null（如模型配置被改坏 / Agent 未装载）
    getAgentDetailMock.mockResolvedValue(baseDetail({ context_usage: null }));
    await wrapper.setProps({ open: false });
    await flushPromises();
    await wrapper.setProps({ open: true });
    await flushPromises();

    expect(getAgentContextUsage(7)).toBeNull();
    expect(meterEl()).toBeNull();

    wrapper.unmount();
  });

  it('WS 推送先到、详情没有该字段时不会抹掉已推送的值', async () => {    // 真实竞态：推送先到、详情后到。详情缺字段不该清空 store。
    getAgentDetailMock.mockResolvedValue(baseDetail());
    const wrapper = mountDialog();
    await flushPromises();
    expect(meterEl()).toBeNull();

    applyRealtimeEvent({ type: 'agent_context_usage', teamId: 1, agentId: 7, contextUsage: USAGE });
    await flushPromises();
    expect(meterEl()).not.toBeNull();

    // 重新打开（会再拉一次详情，仍无该字段），已显示的值不能被抹掉
    await wrapper.setProps({ open: false });
    await flushPromises();
    await wrapper.setProps({ open: true });
    await flushPromises();
    expect(meterEl()).not.toBeNull();
    expect(getAgentContextUsage(7)).toEqual(USAGE);

    wrapper.unmount();
  });

  it('详情里的用量比推送旧时，以推送值为准（seed 覆盖但随后事件可再推进）', async () => {
    const newer: AgentContextUsage = { ...USAGE, used_tokens: 90000, ratio: 90000 / 114688 };
    getAgentDetailMock.mockResolvedValue(baseDetail({ context_usage: USAGE }));
    const wrapper = mountDialog();
    await flushPromises();
    expect(getAgentContextUsage(7)).toEqual(USAGE);

    applyRealtimeEvent({ type: 'agent_context_usage', teamId: 1, agentId: 7, contextUsage: newer });
    await flushPromises();

    expect(getAgentContextUsage(7)).toEqual(newer);
    expect(meterEl()!.getAttribute('data-tier')).toBe('warn');

    wrapper.unmount();
  });
});

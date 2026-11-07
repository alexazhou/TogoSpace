/**
 * 交叉点用例：**真 `AgentActivityDialog` + 真 `AgentActivityPanel`** 同时挂载。
 *
 * 来源：李哥 #143 复核时写的探针（`agent/李哥/lg143/lg143_crosspoint.test.ts`，
 * 原文件标注「不入库」，2026-11-07 Operator 授权落库）。断言语义原样保留，未放宽。
 *
 * ## 为什么需要它（问题本体）
 *
 * 仓库里原本**没有任何一条用例同时真挂 Dialog 与 Panel**，两单各自把自己的对方 stub 掉了：
 *   - `AgentActivityDialogContextMeter.test.ts`（#141）用 `stubs: { AgentActivityPanel: true }`
 *   - `AgentActivityPanel.test.ts`（#139）单独 mount Panel，完全不经过 Dialog
 * 而 `notifyControlRoom` 全树 3 处实现、0 条测试。
 *
 * ⇒ 「399 绿」证明不了「两单拼起来能跑」。这是并行开发 + 各自 stub 对方造成的固有盲区，
 *   也是本用例存在的唯一理由：它不重复测单个组件的行为，只测**跨越 Dialog→Panel 边界**那一段。
 *
 * ## 三条判据
 *   P1 两单的 UI 元素在同一棵挂载树里同时存在（证明 Panel 没被 stub）
 *   P2 `supervise` 返回 `room_id` → 经 Dialog 的 `ref` 转发到真 Panel → 真 Panel 以该 id 重拉消息
 *   P3 真实 WS 事件 `agent_context_usage` 推进进度条数字，且 Panel 仍在
 *
 * ## 变异判别力（每项都已实测，改坏了这里会红）
 *   - 删 `AgentActivityDialog.vue` 里 `notifyControlRoom` 的转发 → P2 红（`expected [] to include 4242`）
 *   - 去掉 `AgentActivityPanel` 的 `defineExpose({ notifyControlRoom })` → P2 红（同上）
 *   - 摘掉 Panel 的 `:team-id="displayAgent?.team_id ?? null"` → P2 红（同上；`reload()` 在
 *     `teamId === null` 时提前返回，`getRoomMessages` 根本不会被调用）
 *   - 摘掉 `<AgentContextCapacityMeter>` 插槽 → P1 红 + P3 红
 *
 * ⚠️ `vi.mock('../../../api')` 是**必需的**，不是图省事：P2 的观测点就是
 * `getRoomMessages` 被以哪个 `room_id` 调用。这个 mock 观测的是「跨过 Dialog→Panel 边界
 * 之后的副作用」，而不是把链路断开——`superviseAgent` / `getAgentDetail` 也都保留为可编排的 mock。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { flushPromises, mount } from '@vue/test-utils';
import AgentActivityDialog from '../AgentActivityDialog.vue';
import i18n from '../../../i18n';
import { applyRealtimeEvent, clearRuntimeStore, getAgentContextUsage } from '../../../realtime/runtimeStore';

const { getAgentDetailMock, superviseAgentMock, getRoomMessagesMock, getRoomsMock } = vi.hoisted(() => ({
  getAgentDetailMock: vi.fn(),
  superviseAgentMock: vi.fn(),
  getRoomMessagesMock: vi.fn(),
  getRoomsMock: vi.fn(),
}));

vi.mock('../../../api', () => ({
  getAgentDetail: getAgentDetailMock,
  resumeAgent: vi.fn(),
  stopAgent: vi.fn(),
  superviseAgent: superviseAgentMock,
  compactAgentContext: vi.fn(),
  getAgentActivitiesPage: vi.fn().mockResolvedValue({ activities: [], hasMore: false }),
  getRoomMessages: getRoomMessagesMock,
  getRooms: getRoomsMock,
  getRoomLastMessages: vi.fn().mockResolvedValue([]),
  escalateMessageToImmediate: vi.fn(),
}));

const USAGE = {
  used_tokens: 40000,
  limit_tokens: 114688,
  compact_trigger_tokens: 97484,
  context_window_tokens: 131072,
  ratio: 40000 / 114688,
};

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
    team_id: 2,
    ...overrides,
  };
}

/** 只 stub 掉不需要的兄弟面板；`AgentActivityPanel` 保持真组件——这是本用例的核心。 */
const stubs = {
  AgentTaskPanel: true,
  AgentMemoryPanel: true,
  AgentPropertiesPanel: true,
  AgentContainerPanel: true,
};

function mountDialog() {
  return mount(AgentActivityDialog, {
    props: { open: true, agentId: 7, agentName: '阿张', roleTemplateName: '工程师' },
    global: { plugins: [i18n], stubs },
    attachTo: document.body,
  });
}

beforeEach(() => {
  document.body.innerHTML = '';
  clearRuntimeStore();
  getAgentDetailMock.mockReset();
  superviseAgentMock.mockReset();
  getRoomMessagesMock.mockReset();
  getRoomsMock.mockReset();
  getAgentDetailMock.mockResolvedValue(baseDetail({ context_usage: USAGE }));
  getRoomMessagesMock.mockResolvedValue({ messages: [], has_more: false, max_seq: 0 });
  getRoomsMock.mockResolvedValue([]);
});

describe('#143 交叉点：真 Dialog + 真 Panel', () => {
  it('P1 两单的 UI 元素在同一棵挂载树里同时存在', async () => {
    const wrapper = mountDialog();
    await flushPromises();

    // #141：进度条
    expect(document.body.querySelector('.agent-context-meter')).not.toBeNull();
    // #139：真 AgentActivityPanel 已挂载（证明没被 stub）
    expect(document.body.querySelector('.agent-activity-panel')).not.toBeNull();
    // 真 Panel 内部组件也起来了（stub 的话不会有这个）
    expect(document.body.querySelector('.agent-activity-panel__tabs')).not.toBeNull();

    wrapper.unmount();
  });

  it('P2 supervise 返回 room_id 时，真 Panel 收到 notifyControlRoom（经 Dialog 的 ref 转发）', async () => {
    superviseAgentMock.mockResolvedValue({ room_id: 4242 });
    const wrapper = mountDialog();
    await flushPromises();

    // 真 Panel 已挂载（stub 掉的话这个断言会失败）
    const panel = wrapper.findComponent({ name: 'AgentActivityPanel' });
    expect(panel.exists()).toBe(true);

    // 记录调用前 getRoomMessages 被调过哪些 roomId
    const before = getRoomMessagesMock.mock.calls.map((c) => c[0]);
    expect(before).not.toContain(4242);

    const ta = document.body.querySelector('.agent-supervise-section__textarea') as HTMLTextAreaElement;
    expect(ta).not.toBeNull();
    ta.value = '去看一下';
    ta.dispatchEvent(new Event('input'));
    await flushPromises();
    ta.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
    await flushPromises();

    expect(superviseAgentMock).toHaveBeenCalled();
    // notifyControlRoom 的可观测副作用：把 4242 认成控制房并重拉消息
    // （若 ref 没接上 / defineExpose 没收进 / 面板被 stub / team-id 没传，这里都不会有 4242）
    const after = getRoomMessagesMock.mock.calls.map((c) => c[0]);
    expect(after).toContain(4242);

    wrapper.unmount();
  });

  it('P3 真实 WS 事件推进进度条数字，且不影响 Panel 存在', async () => {
    const wrapper = mountDialog();
    await flushPromises();

    const before = document.body.querySelector('.agent-context-meter')?.textContent ?? '';
    expect(before.length).toBeGreaterThan(0);

    applyRealtimeEvent({
      type: 'agent_context_usage',
      teamId: 2,
      agentId: 7,
      contextUsage: { ...USAGE, used_tokens: 90000, ratio: 90000 / 114688 },
    } as never);
    await flushPromises();

    expect(getAgentContextUsage(7)?.used_tokens).toBe(90000);
    const after = document.body.querySelector('.agent-context-meter')?.textContent ?? '';
    expect(after).not.toBe(before);
    expect(document.body.querySelector('.agent-activity-panel')).not.toBeNull();

    wrapper.unmount();
  });
});

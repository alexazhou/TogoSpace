import { beforeEach, describe, expect, it, vi } from 'vitest';
import { defineComponent, h } from 'vue';
import { flushPromises, mount } from '@vue/test-utils';
import AgentContextCapacityMeter from '../AgentContextCapacityMeter.vue';
import i18n from '../../../i18n';
import type { AgentContextUsage } from '../../../types';
import { normalizeWsEventPayload } from '../../../realtime/eventNormalizer';
import {
  applyRealtimeEvent,
  clearRuntimeStore,
  getAgentContextUsage,
  seedAgentContextUsage,
} from '../../../realtime/runtimeStore';
import { useAgentContextUsage } from '../../../realtime/selectors';

/**
 * 组件级约定（对应设计文档 step1 / step2 与 02:26 追加验收要求）：
 *
 * 1. 色温判据用**绝对值**（`used >= compact_trigger_tokens`），不是 `ratio >= 0.85`。
 *    所以「红区可达性」用构造 usage 直接断言 `data-tier="danger"`，不等端到端自然发生。
 * 2. `usage === null` 表示「不知道」→ **整条不渲染**，绝不能渲染成 0%。
 * 3. 先判色温、后 clamp 宽度：`used > limit` 时宽度 100%，档位仍是 danger。
 */

const { compactMock, toastMock } = vi.hoisted(() => ({
  compactMock: vi.fn(),
  toastMock: vi.fn(),
}));

vi.mock('../../../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api')>();
  return {
    ...actual,
    compactAgentContext: (...args: unknown[]) => compactMock(...args),
  };
});

vi.mock('../../../appUiState', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../appUiState')>();
  return {
    ...actual,
    showGlobalSuccessToast: (...args: unknown[]) => toastMock(...args),
  };
});

/** 默认口径：窗口 131072、预留输出 16384、ratio 0.85 → limit 114688 / trigger 97484。 */
const LIMIT = 114688;
const TRIGGER = 97484;
const WINDOW = 131072;

function createUsage(overrides: Partial<AgentContextUsage> = {}): AgentContextUsage {
  const used = overrides.used_tokens ?? 40000;
  const limit = overrides.limit_tokens ?? LIMIT;
  return {
    used_tokens: used,
    limit_tokens: limit,
    compact_trigger_tokens: overrides.compact_trigger_tokens ?? TRIGGER,
    context_window_tokens: overrides.context_window_tokens ?? WINDOW,
    ratio: overrides.ratio ?? used / limit,
  };
}

function mountMeter(options: {
  usage?: AgentContextUsage | null;
  status?: string | null;
  agentId?: number | null;
} = {}) {
  return mount(AgentContextCapacityMeter, {
    props: {
      usage: options.usage === undefined ? createUsage() : options.usage,
      status: options.status === undefined ? 'idle' : options.status,
      agentId: options.agentId === undefined ? 7 : options.agentId,
    },
    global: { plugins: [i18n] },
  });
}

/** 从 fill 元素的内联样式里取宽度百分比（组件不用 CSS 变量，读 style 最直接）。 */
function fillWidthPercent(wrapper: {
  find: (selector: string) => { attributes: (name: string) => string | undefined };
}): number {
  const style = wrapper.find('.agent-context-meter__fill').attributes('style') ?? '';
  const matched = /width:\s*([\d.]+)%/.exec(style);
  expect(matched, `未找到 fill 宽度: ${style}`).not.toBeNull();
  return Number(matched![1]);
}

beforeEach(() => {
  compactMock.mockReset();
  compactMock.mockResolvedValue(undefined);
  toastMock.mockReset();
});

describe('AgentContextCapacityMeter 三级色温', () => {
  it('未到琥珀下限 → normal', () => {
    // 50000 < 114688 * 0.7 = 80281.6
    const wrapper = mountMeter({ usage: createUsage({ used_tokens: 50000 }) });
    expect(wrapper.find('.agent-context-meter').attributes('data-tier')).toBe('normal');
  });

  it('过琥珀下限、未到压缩线 → warn', () => {
    // 81000 >= 80281.6 且 < 97484
    const wrapper = mountMeter({ usage: createUsage({ used_tokens: 81000 }) });
    expect(wrapper.find('.agent-context-meter').attributes('data-tier')).toBe('warn');
  });

  it('红区可达：used 恰等于 trigger（含等号）→ danger，且此刻 ratio < 0.85', () => {
    // 这条同时钉两件事：
    //   a) 判据含等号：`used >= trigger` 才算到线，用 `>` 会漏掉正好压线的那 1 次；
    //   b) **不能用 ratio 比 0.85**：97484 / 114688 = 84.9993%（< 85%），
    //      写成 `ratio >= 0.85` 判红时这里会错误落到 warn。自动压缩恰好发生在
    //      `used >= trigger` 那一刻，所以红区若用 ratio 判，在数学上是个 1 token 宽的
    //      窗口、几乎永远进不去（这也是红区"看不见"的唯一正确解释，不是 bug）。
    const usage = createUsage({ used_tokens: TRIGGER });
    expect(usage.ratio).toBeLessThan(0.85);
    expect(usage.ratio).toBeGreaterThan(0.84);

    const wrapper = mountMeter({ usage });
    expect(wrapper.find('.agent-context-meter').attributes('data-tier')).toBe('danger');
  });

  it('红区边界：used = trigger - 1 尚未到线 → warn', () => {
    // 与上一条互为边界：差 1 token 就从 danger 掉回 warn，说明判据确实是 `>=`。
    const wrapper = mountMeter({ usage: createUsage({ used_tokens: TRIGGER - 1 }) });
    expect(wrapper.find('.agent-context-meter').attributes('data-tier')).toBe('warn');
  });

  it('危险档优先于琥珀档（红区优先，不因 ratio 落在 warn 区间而降级）', () => {
    // used 同时满足 use>=trigger 与 use>=limit*0.7，必须是 danger
    const wrapper = mountMeter({ usage: createUsage({ used_tokens: 100000 }) });
    expect(wrapper.find('.agent-context-meter').attributes('data-tier')).toBe('danger');
  });

  it('红区跟随后端 trigger：配置 ratio 调低后 used 未到硬阈值也判 danger', () => {
    // 假设用户把 compact_trigger_ratio 改成 0.5 → trigger = 57344。
    // 前端若硬编码 0.85（97484）会把 used=60000 判成 warn，这里钉住"跟随后端"。
    const usage = createUsage({ used_tokens: 60000, compact_trigger_tokens: 57344 });
    const wrapper = mountMeter({ usage });
    expect(wrapper.find('.agent-context-meter').attributes('data-tier')).toBe('danger');
  });

  it('trigger 被调高到上限之上时琥珀区间自然为空，不做兜底压缩', () => {
    // ratio=0.9 → trigger=103219（仍 < limit）。used=85000 只满足 amber 条件。
    const usage = createUsage({ used_tokens: 85000, compact_trigger_tokens: 103219 });
    expect(mountMeter({ usage }).find('.agent-context-meter').attributes('data-tier')).toBe('warn');
  });
});

describe('AgentContextCapacityMeter 宽度与 clamp 顺序', () => {
  it('宽度直接取后端 ratio，不在前端重算 used/limit', () => {
    // ratio 刻意与 used/limit 不等（后端口径变化时前端应原样画）
    const wrapper = mountMeter({ usage: createUsage({ used_tokens: 40000, ratio: 0.25 }) });
    expect(fillWidthPercent(wrapper)).toBe(25);
  });

  it('ratio 超过 1 时宽度夹到 100%，但档位仍是 danger（先判色温后 clamp）', () => {
    const usage = createUsage({ used_tokens: 120000, ratio: 120000 / LIMIT });
    expect(usage.ratio).toBeGreaterThan(1);

    const wrapper = mountMeter({ usage });
    expect(fillWidthPercent(wrapper)).toBe(100);
    // 关键：clamp 只作用于宽度，不影响色温判定
    expect(wrapper.find('.agent-context-meter').attributes('data-tier')).toBe('danger');
  });

  it('负数 ratio 兜底为 0 宽度（不合理输入不画负宽度）', () => {
    const wrapper = mountMeter({ usage: createUsage({ used_tokens: 0, ratio: -0.2 }) });
    expect(fillWidthPercent(wrapper)).toBe(0);
  });

  it('百分比文本四舍五入到整数', () => {
    const wrapper = mountMeter({ usage: createUsage({ used_tokens: 40000, ratio: 0.3567 }) });
    expect(wrapper.find('.agent-context-meter__percent').text()).toBe('36%');
  });
});

describe('AgentContextCapacityMeter 压缩触发线', () => {
  /** 触发线位置同样读内联 style（left 百分比）。 */
  function triggerLeftPercent(wrapper: ReturnType<typeof mountMeter>): number | null {
    const trigger = wrapper.find('.agent-context-meter__trigger');
    if (!trigger.exists()) {
      return null;
    }
    const style = trigger.attributes('style') ?? '';
    const matched = /left:\s*([\d.]+)%/.exec(style);
    expect(matched, `未找到触发线位置: ${style}`).not.toBeNull();
    return Number(matched![1]);
  }

  it('位置取 trigger / limit，跟随后端阈值而不是写死 85%', () => {
    // 默认口径 97484 / 114688 ≈ 84.9993%
    expect(triggerLeftPercent(mountMeter())).toBeCloseTo(85, 1);

    // 配置 ratio 改成 0.5 → trigger 57344，线应落在 50%
    const usage = createUsage({ compact_trigger_tokens: 57344 });
    expect(triggerLeftPercent(mountMeter({ usage }))).toBeCloseTo(50, 1);
  });

  it('limit 非法（<= 0）时不画线', () => {
    const usage = createUsage({ used_tokens: 0, limit_tokens: 0, ratio: 0 });
    expect(triggerLeftPercent(mountMeter({ usage }))).toBeNull();
  });
});

describe('AgentContextCapacityMeter 不渲染的两种情形', () => {
  it('usage 为 null 时整条不渲染（不是渲染 0%）', () => {
    const wrapper = mountMeter({ usage: null });
    expect(wrapper.find('.agent-context-meter').exists()).toBe(false);
    // 连百分比文本都不该存在 —— 渲染 0% 等于断言"上下文是空的"
    expect(wrapper.text()).not.toContain('0%');
    expect(wrapper.text()).toBe('');
  });

  it('负 id（操作者 / SYSTEM）即便有 usage 也不渲染', () => {
    const wrapper = mountMeter({ usage: createUsage(), agentId: -1 });
    expect(wrapper.find('.agent-context-meter').exists()).toBe(false);
  });

  it('agentId 为 null 时不渲染', () => {
    const wrapper = mountMeter({ usage: createUsage(), agentId: null });
    expect(wrapper.find('.agent-context-meter').exists()).toBe(false);
  });
});

describe('AgentContextCapacityMeter 手动压缩按钮', () => {
  it('空闲态可点击，按 agentId 调用压缩接口', async () => {
    const wrapper = mountMeter({ agentId: 7 });
    const button = wrapper.find('.agent-context-meter__compact');
    expect(button.attributes('disabled')).toBeUndefined();

    await button.trigger('click');
    await flushPromises();

    expect(compactMock).toHaveBeenCalledWith(7);
  });

  it('成功后弹一次成功提示', async () => {
    const wrapper = mountMeter();
    await wrapper.find('.agent-context-meter__compact').trigger('click');
    await flushPromises();

    expect(toastMock).toHaveBeenCalledTimes(1);
    expect(toastMock).toHaveBeenCalledWith('上下文压缩已完成');
  });

  it('失败时绝不弹成功提示（错误文案由 requestJson 统一展示）', async () => {
    compactMock.mockRejectedValue(new Error('boom'));
    const wrapper = mountMeter();

    await wrapper.find('.agent-context-meter__compact').trigger('click');
    await flushPromises();

    expect(toastMock).not.toHaveBeenCalled();
  });

  it('agent 处于 active 时禁用，点击不发请求', async () => {
    const wrapper = mountMeter({ status: 'active' });
    const button = wrapper.find('.agent-context-meter__compact');

    expect(button.attributes('disabled')).toBeDefined();
    expect(button.attributes('title')).toBe('Agent 正在工作中，暂不支持手动压缩');

    await button.trigger('click');
    await flushPromises();
    expect(compactMock).not.toHaveBeenCalled();
  });

  it('压缩进行中禁用，请求结束后恢复', async () => {
    let resolveCompact: (() => void) | null = null;
    compactMock.mockImplementation(
      () => new Promise<void>((resolve) => {
        resolveCompact = resolve;
      }),
    );

    const wrapper = mountMeter();
    await wrapper.find('.agent-context-meter__compact').trigger('click');

    const busyButton = wrapper.find('.agent-context-meter__compact');
    expect(busyButton.attributes('disabled')).toBeDefined();

    resolveCompact!();
    await flushPromises();

    const idleButton = wrapper.find('.agent-context-meter__compact');
    expect(idleButton.attributes('disabled')).toBeUndefined();
  });
});

describe('AgentContextCapacityMeter 由 WS 推送驱动（不刷新页面）', () => {
  /** 用真实 store + selector 包一层，模拟弹窗里「先播种、后推送」的真实接线。 */
  const Host = defineComponent({
    props: {
      agentId: { type: Number, default: 7 },
    },
    setup(props) {
      const usage = useAgentContextUsage(() => props.agentId);
      return () => h(AgentContextCapacityMeter, { usage: usage.value, status: 'idle', agentId: props.agentId });
    },
  });

  beforeEach(() => {
    clearRuntimeStore();
  });

  it('未播种且无推送时整条不渲染（新打开弹窗、详情还没回来）', () => {
    const wrapper = mount(Host, { global: { plugins: [i18n] } });
    expect(wrapper.find('.agent-context-meter').exists()).toBe(false);
  });

  it('详情接口播种后立刻画出进度条', async () => {
    seedAgentContextUsage(7, createUsage({ used_tokens: 40000, ratio: 0.35 }));
    const wrapper = mount(Host, { global: { plugins: [i18n] } });
    await flushPromises();

    expect(wrapper.find('.agent-context-meter').attributes('data-tier')).toBe('normal');
    expect(fillWidthPercent(wrapper)).toBe(35);
  });

  it('收到 WS 事件后进度条自己动，无需重新挂载或重拉详情', async () => {
    seedAgentContextUsage(7, createUsage({ used_tokens: 40000, ratio: 0.35 }));
    const wrapper = mount(Host, { global: { plugins: [i18n] } });
    await flushPromises();
    expect(fillWidthPercent(wrapper)).toBe(35);

    // 走完整链路：原始 WS payload → normalizer → store → 组件
    const event = normalizeWsEventPayload({
      event: 'agent_context_usage',
      team_id: 2,
      agent_id: 7,
      context_usage: createUsage({ used_tokens: 100000 }),
    });
    expect(event).not.toBeNull();
    applyRealtimeEvent(event!);
    await flushPromises();

    // 宽度用原始 ratio（不取整），文本才四舍五入
    expect(fillWidthPercent(wrapper)).toBeCloseTo(87.19, 1);
    expect(wrapper.find('.agent-context-meter').attributes('data-tier')).toBe('danger');
    expect(wrapper.find('.agent-context-meter__percent').text()).toBe('87%');
  });

  it('其他 Agent 的推送不影响当前 Agent 的进度条', async () => {
    seedAgentContextUsage(7, createUsage({ used_tokens: 40000, ratio: 0.35 }));
    const wrapper = mount(Host, { global: { plugins: [i18n] } });
    await flushPromises();

    const event = normalizeWsEventPayload({
      event: 'agent_context_usage',
      team_id: 2,
      agent_id: 8,
      context_usage: createUsage({ used_tokens: 110000 }),
    });
    applyRealtimeEvent(event!);
    await flushPromises();

    expect(fillWidthPercent(wrapper)).toBe(35);
    expect(getAgentContextUsage(8)).not.toBeNull();
  });

  it('脏推送被丢弃，进度条保持旧值（不因一条坏数据变成空条）', async () => {
    seedAgentContextUsage(7, createUsage({ used_tokens: 40000, ratio: 0.35 }));
    const wrapper = mount(Host, { global: { plugins: [i18n] } });
    await flushPromises();

    const dirty = normalizeWsEventPayload({
      event: 'agent_context_usage',
      team_id: 2,
      agent_id: 7,
      context_usage: { used_tokens: 'oops', limit_tokens: 114688 },
    });
    expect(dirty).toBeNull();
    await flushPromises();

    expect(fillWidthPercent(wrapper)).toBe(35);
  });
});

describe('AgentContextCapacityMeter tooltip', () => {
  it('展示口径全部来自同一份 payload（不写死文档示例数字）', () => {
    const wrapper = mountMeter({ usage: createUsage({ used_tokens: 40000 }) });
    const title = wrapper.find('.agent-context-meter').attributes('title') ?? '';

    expect(title).toContain('40,000');
    expect(title).toContain('114,688');
    expect(title).toContain('97,484');
    expect(title).toContain('131,072');
    // 文档里的示例数字（36420 / 128000 / 108800）不许出现
    expect(title).not.toContain('36,420');
    expect(title).not.toContain('128,000');
    expect(title).not.toContain('108,800');
  });

  it('超限时百分比可超过 100%（tooltip 说实话，不 clamp）', () => {
    const wrapper = mountMeter({ usage: createUsage({ used_tokens: 120000 }) });
    const title = wrapper.find('.agent-context-meter').attributes('title') ?? '';

    // 120000 / 114688 = 104.6%
    expect(title).toContain('104.6');
  });
});

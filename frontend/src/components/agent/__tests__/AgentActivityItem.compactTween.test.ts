import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { mount } from '@vue/test-utils';
import { nextTick } from 'vue';
import AgentActivityItem from '../AgentActivityItem.vue';
import i18n from '../../../i18n';
import type { AgentActivity } from '../../../types';

/**
 * COMPACT「压缩中」形态的 token 补间（#176）。
 *
 * 背景：#175 实测发现 COMPACT 形态一（`↑ {before} ↓ {count}`，count 逐 chunk 递增）
 * 一个中间帧都没有——同样的组件、同样的推送节奏，LLM_INFER 有 14 帧、COMPACT 0 帧。
 * 取证结论是「补间只做了 split 分支的遗漏」，不是当年的决定。
 *
 * 本文件只验**形态一**。形态二（`tokens {before} → {after}`，大→小）按 `61a20ab9`
 * 既定口径「只对变大补间，变小直接落位」本来就该一次性落位，A4 锁住它别被顺手改坏。
 *
 * rAF 用 `vi.stubGlobal` 手动驱动（与 `AgentActivityItem.tween.test.ts` 同一手法）。
 * 不用 `vi.useFakeTimers()`：假时钟对递归 rAF 的驱动次数依赖实现细节。
 *
 * ⚠️ 量具注意：`useAnimatedNumber` 的第一帧只设 `startedAt`（elapsed=0），显示值仍是起点。
 * 所以「推 1 帧」看到的是起点值，容易被误读成「没补间」。本文件取样都从第 2 帧之后开始。
 */

type FrameCallback = (timestamp: number) => void;

let time = 0;
let queue: Array<{ id: number; callback: FrameCallback }> = [];
let nextId = 1;

function installFakeRaf() {
  time = 0;
  queue = [];
  nextId = 1;
  vi.stubGlobal('requestAnimationFrame', (callback: FrameCallback) => {
    const id = nextId++;
    queue.push({ id, callback });
    return id;
  });
  vi.stubGlobal('cancelAnimationFrame', (id: number) => {
    queue = queue.filter((entry) => entry.id !== id);
  });
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  }));
}

/** 推进一帧（16ms）。 */
async function stepFrame(): Promise<void> {
  time += 16;
  const batch = queue;
  queue = [];
  for (const entry of batch) {
    entry.callback(time);
  }
  await nextTick();
}

async function runFrames(count: number): Promise<void> {
  for (let index = 0; index < count; index += 1) {
    await stepFrame();
  }
}

function activity(overrides: Partial<AgentActivity> = {}): AgentActivity {
  return {
    id: 1,
    agent_id: 8,
    team_id: 2,
    activity_type: 'compact',
    status: 'started',
    title: '',
    detail: '',
    started_at: '2026-11-09 10:00:00.000000',
    duration_ms: null,
    metadata: {},
    ...overrides,
  };
}

function mountItem(item: AgentActivity) {
  return mount(AgentActivityItem, {
    props: { activity: item },
    global: { plugins: [i18n] },
  });
}

function tokenText(wrapper: ReturnType<typeof mountItem>): string {
  return wrapper.find('.agent-activity-item__tokens').text();
}

/** 取 `↓` 后面的数字；取不到返回 NaN（让断言直接炸出来，不静默通过）。 */
function downValue(wrapper: ReturnType<typeof mountItem>): number {
  return Number(tokenText(wrapper).match(/↓ (\d+)/)?.[1]);
}

/** 取 `↑` 后面的数字；取不到返回 NaN。 */
function upValue(wrapper: ReturnType<typeof mountItem>): number {
  return Number(tokenText(wrapper).match(/↑ (\d+)/)?.[1]);
}

beforeEach(() => {
  installFakeRaf();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('COMPACT 形态一 token 补间', () => {
  it('A1 count 递增有中间帧（现状 0 帧，本判据能咬住）', async () => {
    const wrapper = mountItem(activity({
      metadata: { context_tokens_before: 80000, estimated_completion_tokens: 120 },
    }));
    // 首值直显，不建帧。
    expect(tokenText(wrapper)).toBe('↑ 80000 ↓ 120');
    expect(queue.length).toBe(0);

    await wrapper.setProps({
      activity: activity({
        metadata: { context_tokens_before: 80000, estimated_completion_tokens: 400 },
      }),
    });
    // ① 目标变大 ⇒ 必须排帧（现状是 0，正因如此这条现在会红）。
    expect(queue.length).toBe(1);

    // ② 推 3 帧后必须严格介于起终点之间（避开第 1 帧 elapsed=0 的陷阱）。
    await runFrames(3);
    const midway = downValue(wrapper);
    expect(midway).toBeGreaterThan(120);
    expect(midway).toBeLessThan(400);
    expect(midway).not.toBe(120);
    expect(midway).not.toBe(400);

    // ③ 推满 ≥250ms（20 帧 ≈ 320ms）收敛到目标。
    await runFrames(20);
    expect(tokenText(wrapper)).toBe('↑ 80000 ↓ 400');
    expect(queue.length).toBe(0);
  });

  it('A2 before 恒定不补：逐帧采样，每一帧 ↑ 都是 80000', async () => {
    const wrapper = mountItem(activity({
      metadata: { context_tokens_before: 80000, estimated_completion_tokens: 120 },
    }));

    await wrapper.setProps({
      activity: activity({
        metadata: { context_tokens_before: 80000, estimated_completion_tokens: 400 },
      }),
    });

    // 先证明「确实在补间」：没有动画时「↑ 恒为 80000」是必然成立的假绿。
    expect(queue.length).toBe(1);

    // 逐帧采样，而不是只断首末：首末相同推不出中间没动。
    const upSamples: number[] = [upValue(wrapper)];
    const downSamples: number[] = [downValue(wrapper)];
    for (let index = 0; index < 18; index += 1) {
      await stepFrame();
      upSamples.push(upValue(wrapper));
      downSamples.push(downValue(wrapper));
    }

    expect(upSamples).toHaveLength(19);
    // 每一帧都必须是压缩前规模，不许有哪一帧跟着 count 动。
    expect(upSamples.every((value) => value === 80000)).toBe(true);
    // 对照：这 19 帧里 count 确实动过（有严格介于起终点的帧），否则上一条无意义。
    const moved = downSamples.filter((value) => value > 120 && value < 400);
    expect(moved.length).toBeGreaterThan(0);
    expect(downSamples[downSamples.length - 1]).toBe(400);
  });

  it('A3 title 用目标文案：补间中途 title 已是终点值', async () => {
    const wrapper = mountItem(activity({
      metadata: { context_tokens_before: 80000, estimated_completion_tokens: 120 },
    }));

    await wrapper.setProps({
      activity: activity({
        metadata: { context_tokens_before: 80000, estimated_completion_tokens: 400 },
      }),
    });
    await runFrames(3);

    // 先确认屏幕文本还在中间（否则「title 是终点」可能是动画已收敛的假绿）。
    const midway = downValue(wrapper);
    expect(midway).toBeLessThan(400);

    const title = wrapper.find('.agent-activity-item__tokens').attributes('title');
    expect(title).toBe('↑ 80000 ↓ 400');
  });

  it('A4 形态二不补间（回归）：零帧、一次性落位', async () => {
    const wrapper = mountItem(activity({
      status: 'succeeded',
      metadata: { context_tokens_before: 80000, context_tokens_after: 4200 },
    }));
    expect(tokenText(wrapper)).toBe('tokens 80000 → 4200');
    expect(queue.length).toBe(0);
    // 形态二不该因为「text 分支现在也能渲染 title」而凭空多出 tooltip。
    expect(wrapper.find('.agent-activity-item__tokens').attributes('title')).toBeUndefined();

    // 换成另一个「更小」的 after，仍然不建帧。
    await wrapper.setProps({
      activity: activity({
        status: 'succeeded',
        metadata: { context_tokens_before: 80000, context_tokens_after: 3000 },
      }),
    });
    expect(tokenText(wrapper)).toBe('tokens 80000 → 3000');
    expect(queue.length).toBe(0);
    expect(wrapper.find('.agent-activity-item__tokens').attributes('title')).toBeUndefined();
  });

  it('B4 估算→真值回落：真值更小则同 tick 落位，不出现倒数中间帧', async () => {
    const wrapper = mountItem(activity({
      metadata: { context_tokens_before: 80000, estimated_completion_tokens: 400 },
    }));
    expect(tokenText(wrapper)).toBe('↑ 80000 ↓ 400');

    // 真值到达且比估算小（估算按 delta 累加偏高）——这是既定口径不是 bug。
    await wrapper.setProps({
      activity: activity({
        status: 'succeeded',
        metadata: { context_tokens_before: 80000, completion_tokens: 150 },
      }),
    });

    // 同 tick（一帧都不推）就必须是 150，且不许排帧。
    expect(tokenText(wrapper)).toBe('↑ 80000 ↓ 150');
    expect(queue.length).toBe(0);

    // 后续推帧也不该再动。
    await runFrames(10);
    expect(tokenText(wrapper)).toBe('↑ 80000 ↓ 150');
  });

  it('B5 形态一→形态二切换不残留：补间进行中生切，立刻落位且不再变化', async () => {
    const wrapper = mountItem(activity({
      metadata: { context_tokens_before: 80000, estimated_completion_tokens: 120 },
    }));

    await wrapper.setProps({
      activity: activity({
        metadata: { context_tokens_before: 80000, estimated_completion_tokens: 400 },
      }),
    });
    await runFrames(2);
    // 此刻确实在补间中途。
    expect(downValue(wrapper)).toBeGreaterThan(120);
    expect(downValue(wrapper)).toBeLessThan(400);

    // 压缩结束：形态一 → 形态二。
    await wrapper.setProps({
      activity: activity({
        status: 'succeeded',
        metadata: { context_tokens_before: 80000, context_tokens_after: 4200 },
      }),
    });
    expect(tokenText(wrapper)).toBe('tokens 80000 → 4200');
    expect(queue.length).toBe(0);

    // 再推帧也不能把旧动画的残影带回来。
    await runFrames(5);
    expect(tokenText(wrapper)).toBe('tokens 80000 → 4200');
  });
});

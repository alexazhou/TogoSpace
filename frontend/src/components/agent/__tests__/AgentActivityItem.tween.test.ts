import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { mount } from '@vue/test-utils';
import { nextTick } from 'vue';
import AgentActivityItem from '../AgentActivityItem.vue';
import i18n from '../../../i18n';
import type { AgentActivity } from '../../../types';

/**
 * 组件层补间接线（#168）。
 *
 * `useAnimatedNumber` 自己的 P1–P6 在 `composables/__tests__/useAnimatedNumber.test.ts`
 * 里验；这里只验**接线**：组件把两段数值各自交给补间、并把补间后的值渲染出来。
 * 如果这层没接上，composable 全绿也和用户看到的画面无关。
 *
 * rAF 用 `vi.stubGlobal` 手动驱动（不是 `vi.useFakeTimers()`）。原因：假时钟对递归
 * `requestAnimationFrame` 的驱动次数依赖实现细节，一次 `advanceTimersByTime` 可能只
 * 消化 2 帧，拿它断言「125ms 时的中间态」会假红/假绿。手动驱动则每帧都是我们推的。
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
    activity_type: 'llm_infer',
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

beforeEach(() => {
  installFakeRaf();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('AgentActivityItem token 补间接线', () => {
  it('C1 流中数字增长有中间帧（不是文本直接跳变）', async () => {
    const wrapper = mountItem(activity({
      metadata: { estimated_prompt_tokens: 100, current_completion_tokens: 10 },
    }));
    // 首值直显。
    expect(tokenText(wrapper)).toBe('↑ 100 ↓ 10');

    await wrapper.setProps({
      activity: activity({
        metadata: { estimated_prompt_tokens: 300, current_completion_tokens: 10 },
      }),
    });
    expect(queue.length).toBeGreaterThan(0);

    // 推进到大约一半（7 帧 ≈ 112ms）：↑ 段必须已经离开 100 且还没到 300。
    await runFrames(7);
    const midway = tokenText(wrapper);
    const midwayUp = Number(midway.match(/↑ (\d+)/)?.[1]);
    expect(midwayUp).toBeGreaterThan(100);
    expect(midwayUp).toBeLessThan(300);

    // 推进到超时时长（共 20 帧 ≈ 320ms > 250ms）收敛到目标。
    await runFrames(20);
    expect(tokenText(wrapper)).toBe('↑ 300 ↓ 10');
  });

  it('C2 两段各自独立补间：↓ 不变时不被 ↑ 的动画拖着走', async () => {
    const wrapper = mountItem(activity({
      metadata: { estimated_prompt_tokens: 100, current_completion_tokens: 10 },
    }));

    await wrapper.setProps({
      activity: activity({
        metadata: { estimated_prompt_tokens: 300, current_completion_tokens: 10 },
      }),
    });
    await runFrames(7);
    // ↓ 段目标没变 ⇒ 全程就是 10，绝不会出现 10 到某个中间值的抖动。
    expect(tokenText(wrapper)).toMatch(/↓ 10$/);
  });

  it('C3 变小立即落位：同一 tick 就是新值，没有倒数中间帧', async () => {
    const wrapper = mountItem(activity({
      metadata: { estimated_prompt_tokens: 300, current_completion_tokens: 10 },
    }));
    expect(tokenText(wrapper)).toBe('↑ 300 ↓ 10');

    // 结束后真值比流中估算小（估算按 delta 累加会偏高）—— 这正是「不倒数」的场景。
    await wrapper.setProps({
      activity: activity({
        status: 'succeeded',
        metadata: {
          final_prompt_tokens: 260,
          final_completion_tokens: 8,
          estimated_prompt_tokens: 300,
          current_completion_tokens: 10,
        },
      }),
    });
    expect(tokenText(wrapper)).toBe('↑ 260 ↓ 8');

    // 后续帧也不该再动。
    await runFrames(10);
    expect(tokenText(wrapper)).toBe('↑ 260 ↓ 8');
  });

  it('C4 title 用目标文案：补间中间帧也能看到最终权威值', async () => {
    const wrapper = mountItem(activity({
      metadata: { estimated_prompt_tokens: 100, current_completion_tokens: 10 },
    }));
    await wrapper.setProps({
      activity: activity({
        metadata: { estimated_prompt_tokens: 300, current_completion_tokens: 10 },
      }),
    });
    await runFrames(3);

    const title = wrapper.find('.agent-activity-item__tokens').attributes('title');
    expect(title).toBe('↑ 300 ↓ 10');
  });

  it('C5 估算色按目标态判定，不被补间进度影响', async () => {
    const wrapper = mountItem(activity({
      metadata: { estimated_prompt_tokens: 100, current_completion_tokens: 10 },
    }));
    // 流中：两段都估算。
    expect(wrapper.findAll('.agent-activity-item__tokens-segment--estimated')).toHaveLength(2);

    await wrapper.setProps({
      activity: activity({
        status: 'succeeded',
        metadata: { final_prompt_tokens: 300, final_completion_tokens: 10 },
      }),
    });
    // 目标态已是两段真值 ⇒ 动画还在跑，但估算色立刻消失。
    expect(wrapper.findAll('.agent-activity-item__tokens-segment--estimated')).toHaveLength(0);
  });

  it('C6 COMPACT 文本分支不补间（原样文本，不建帧）', async () => {
    const wrapper = mountItem(activity({
      activity_type: 'compact',
      metadata: { context_tokens_before: 80000, current_completion_tokens: 120 },
    }));
    expect(tokenText(wrapper)).toBe('↑ 80000 ↓ 120');
    expect(queue.length).toBe(0);

    await wrapper.setProps({
      activity: activity({
        activity_type: 'compact',
        status: 'succeeded',
        metadata: { context_tokens_before: 80000, context_tokens_after: 4200 },
      }),
    });
    // 换文案也不开帧：COMPACT 不在补间范围内。
    expect(tokenText(wrapper)).toBe('tokens 80000 → 4200');
    expect(queue.length).toBe(0);
  });

  it('C7 卸载后 rAF 不再排帧', async () => {
    const wrapper = mountItem(activity({
      metadata: { estimated_prompt_tokens: 100, current_completion_tokens: 10 },
    }));
    await wrapper.setProps({
      activity: activity({
        metadata: { estimated_prompt_tokens: 300, current_completion_tokens: 10 },
      }),
    });
    await stepFrame();
    expect(queue.length).toBe(1);

    wrapper.unmount();
    // 卸载后即便还在途的帧被调用，也不能再排新帧。
    await runFrames(3);
    expect(queue.length).toBe(0);
  });
});

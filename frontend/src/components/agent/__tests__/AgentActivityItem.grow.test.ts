import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mount } from '@vue/test-utils';
import AgentActivityItem from '../AgentActivityItem.vue';
import i18n from '../../../i18n';
import type { AgentActivity } from '../../../types';

/**
 * 组件层增长放大接线（G-A1–G-A5）。
 *
 * 速度 → 峰值的映射与 500ms 衰减在 `composables/__tests__/useGrowingScale.test.ts`；
 * 这里只验「哪一段变大 → 哪一段的数字被写入 scale」以及箭头/另一段不动。
 */

function activity(overrides: Partial<AgentActivity> = {}): AgentActivity {
  return {
    id: 1,
    agent_id: 8,
    team_id: 2,
    activity_type: 'llm_infer',
    status: 'started',
    title: '',
    detail: '',
    started_at: '2026-11-10 10:00:00.000000',
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

function numbers(wrapper: ReturnType<typeof mountItem>) {
  return wrapper.findAll('.agent-activity-item__tokens-number');
}

function arrows(wrapper: ReturnType<typeof mountItem>) {
  return wrapper.findAll('.agent-activity-item__tokens-arrow');
}

function readScale(element: Element): number {
  const match = /scale\(([\d.]+)\)/.exec((element as HTMLElement).style.transform);
  return match ? Number(match[1]) : Number.NaN;
}

function scales(wrapper: ReturnType<typeof mountItem>): number[] {
  return numbers(wrapper).map((number) => readScale(number.element));
}

beforeEach(() => {
  // 补间与衰减都不在组件层验：rAF 置空，只观察值变大时立刻写入的峰值。
  vi.stubGlobal('requestAnimationFrame', () => 1);
  vi.stubGlobal('cancelAnimationFrame', () => {});
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('AgentActivityItem token 增长放大', () => {
  it('G-A1 输出变大时只有 ↓ 的数字放大', async () => {
    const wrapper = mountItem(activity({
      metadata: { estimated_prompt_tokens: 100, estimated_completion_tokens: 10 },
    }));
    expect(scales(wrapper).map((scale) => Number.isNaN(scale) ? 1 : scale)).toEqual([1, 1]);

    await wrapper.setProps({
      activity: activity({
        metadata: { estimated_prompt_tokens: 100, estimated_completion_tokens: 20 },
      }),
    });

    const [upScale, downScale] = scales(wrapper);
    expect(Number.isNaN(upScale) ? 1 : upScale).toBe(1);
    expect(downScale).toBeGreaterThan(1);
  });

  it('G-A2 输入变大时只有 ↑ 的数字放大', async () => {
    const wrapper = mountItem(activity({
      metadata: { estimated_prompt_tokens: 100, estimated_completion_tokens: 10 },
    }));

    await wrapper.setProps({
      activity: activity({
        metadata: { estimated_prompt_tokens: 160, estimated_completion_tokens: 10 },
      }),
    });

    const [upScale, downScale] = scales(wrapper);
    expect(upScale).toBeGreaterThan(1);
    expect(Number.isNaN(downScale) ? 1 : downScale).toBe(1);
  });

  it('G-A3 变小不放大', async () => {
    const wrapper = mountItem(activity({
      metadata: { estimated_prompt_tokens: 100, estimated_completion_tokens: 20 },
    }));

    await wrapper.setProps({
      activity: activity({
        status: 'succeeded',
        metadata: { prompt_tokens: 90, completion_tokens: 12 },
      }),
    });

    expect(scales(wrapper).every((scale) => Number.isNaN(scale) || scale === 1)).toBe(true);
  });

  it('G-A4 箭头不带 transform，数字变化不会重建任何节点', async () => {
    const wrapper = mountItem(activity({
      metadata: { estimated_prompt_tokens: 100, estimated_completion_tokens: 10 },
    }));
    const firstNumbers = numbers(wrapper).map((number) => number.element);
    const firstArrows = arrows(wrapper).map((arrow) => arrow.element);

    await wrapper.setProps({
      activity: activity({
        metadata: { estimated_prompt_tokens: 160, estimated_completion_tokens: 20 },
      }),
    });

    expect(numbers(wrapper).map((number) => number.element)).toEqual(firstNumbers);
    expect(arrows(wrapper).map((arrow) => arrow.element)).toEqual(firstArrows);
    // 箭头完全不受放大影响：没有 inline transform。
    expect(arrows(wrapper).every((arrow) => (arrow.element as HTMLElement).style.transform === '')).toBe(true);
  });

  it('G-A5 两段数字各自独立缩放', async () => {
    const wrapper = mountItem(activity({
      metadata: { estimated_prompt_tokens: 100, estimated_completion_tokens: 10 },
    }));

    await wrapper.setProps({
      activity: activity({
        metadata: { estimated_prompt_tokens: 160, estimated_completion_tokens: 20 },
      }),
    });

    const [upScale, downScale] = scales(wrapper);
    expect(upScale).toBeGreaterThan(1);
    expect(downScale).toBeGreaterThan(1);
  });

  // ---------------- #206 COMPACT「压缩中」也接放大 ----------------
  //
  // 背景：#206 实测确认 COMPACT 形态一（`↑ {before} ↓ {count}`）**一个 transform 节点都没有**
  // ——`92ed419c` 引入放大时只接了 split 分支，`d2a9bde3`(#176) 补 COMPACT 时只补了补间。
  // 下面三条是这条路径的护栏：形态一必须放大、且只放大数字（before 不放大）、形态二不放大。

  /** COMPACT 活动（默认形态一：`context_tokens_before` + 估算输出量）。 */
  function compact(metadata: Record<string, number>): AgentActivity {
    return activity({ activity_type: 'compact', status: 'started', metadata });
  }

  it('G-A6 形态一 count 递增时数字放大，且前缀（↑ before ↓）不带 transform', async () => {
    const wrapper = mountItem(compact({ context_tokens_before: 80000, estimated_completion_tokens: 120 }));

    // 首值是直显的定值，不出放大。
    expect(scales(wrapper).every((scale) => Number.isNaN(scale) || scale === 1)).toBe(true);

    await wrapper.setProps({
      activity: compact({ context_tokens_before: 80000, estimated_completion_tokens: 400 }),
    });

    // 恰好一个数字节点参与放大 —— 多于一个说明「整段一起放大」的偷懒实现又回来了。
    const numberNodes = numbers(wrapper);
    expect(numberNodes).toHaveLength(1);
    expect(scales(wrapper)[0]).toBeGreaterThan(1);
    // 全局只有一个节点带 transform：挡住「把 scale 挂到段/容器上，让前缀一起放大」的实现
    // （那样箭头节点自身仍无 transform，只断箭头会放过它）。
    expect(wrapper.findAll('[style*="transform"]')).toHaveLength(1);
    expect((wrapper.findAll('[style*="transform"]')[0].element as HTMLElement).className)
      .toContain('agent-activity-item__tokens-number');
    // 前缀（`↑ 80000 ↓ `）保持固定：`before` 恒定，没有任何理由跟着放大。
    expect(arrows(wrapper)).toHaveLength(1);
    expect((arrows(wrapper)[0].element as HTMLElement).style.transform).toBe('');
    // DOM 文本格式仍逐字与改造前同构（T6-a / compactTween 的一批 `.text()` 断言依赖它）。
    // 本文件把 rAF 置空（不推进补间），所以屏幕值停在补间起点 120；
    // 「目标文案」由 `title` 承载，一并断言，证明数字与箭头仍是同一串 `↑ {before} ↓ {count}`。
    expect(wrapper.find('.agent-activity-item__tokens').text()).toBe('↑ 80000 ↓ 120');
    expect(wrapper.find('.agent-activity-item__tokens').attributes('title')).toBe('↑ 80000 ↓ 400');
  });

  it('G-A7 形态一 count 不变（仅无输出量→0）不放大', async () => {
    // `context_tokens_before` 恒定的「刚创建」形态：count 已是 0 且不再变 ⇒ 不该有放大。
    const wrapper = mountItem(compact({ context_tokens_before: 80000 }));
    expect(wrapper.find('.agent-activity-item__tokens').text()).toBe('↑ 80000 ↓ 0');
    expect(scales(wrapper).every((scale) => Number.isNaN(scale) || scale === 1)).toBe(true);

    // 再喂一次完全相同的值（同一个 chunk 重播 / 上层重渲染）也不该触发。
    await wrapper.setProps({ activity: compact({ context_tokens_before: 80000 }) });
    expect(scales(wrapper).every((scale) => Number.isNaN(scale) || scale === 1)).toBe(true);
  });

  it('G-A8 形态二（tokens A → B）不接放大：没有数字节点、没有任何 transform', async () => {
    const wrapper = mountItem(activity({
      activity_type: 'compact',
      status: 'succeeded',
      metadata: { context_tokens_before: 80000, context_tokens_after: 4200 },
    }));

    expect(wrapper.find('.agent-activity-item__tokens').text()).toBe('tokens 80000 → 4200');
    // 形态二是「压缩前后两个定值」，没有「在变」的量，与 split 的 `before` 同理不接放大。
    expect(numbers(wrapper)).toHaveLength(0);
    expect(wrapper.findAll('[style*="transform"]')).toHaveLength(0);
  });
});

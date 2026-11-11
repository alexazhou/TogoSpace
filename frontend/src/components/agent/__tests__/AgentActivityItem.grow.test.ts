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
});

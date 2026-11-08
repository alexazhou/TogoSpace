import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { effectScope, nextTick, ref } from 'vue';
import { TWEEN_DURATION_MS, useAnimatedNumber } from '../useAnimatedNumber';

/**
 * `useAnimatedNumber` 的判据（#168 P1–P6）。
 *
 * 测试环境是 happy-dom，`requestAnimationFrame` 是真实现（靠定时器驱动），
 * 所以这里**一律替换成手动驱动的假 rAF**：用例自己决定「推进到第几毫秒」，
 * 既不用真等 250ms，也能拿到确定的中间帧。
 */

type FrameCallback = (timestamp: number) => void;

interface FakeRaf {
  now: () => number;
  /** 推进时间并触发当前排队的帧回调（一帧）。 */
  step: (ms: number) => void;
  /** 连续推进，直到没有排队中的帧（最多 5 秒，防死循环）。 */
  runUntilIdle: (stepMs?: number) => number;
  pendingCount: () => number;
  scheduleCount: () => number;
  canceledCount: () => number;
}

function installFakeRaf(): FakeRaf {
  let time = 0;
  let nextId = 1;
  let queue: Array<{ id: number; callback: FrameCallback }> = [];
  let scheduled = 0;
  let canceled = 0;

  const raf = (callback: FrameCallback): number => {
    const id = nextId++;
    queue.push({ id, callback });
    scheduled += 1;
    return id;
  };
  const caf = (id: number): void => {
    const before = queue.length;
    queue = queue.filter((entry) => entry.id !== id);
    if (queue.length !== before) {
      canceled += 1;
    }
  };

  vi.stubGlobal('requestAnimationFrame', raf);
  vi.stubGlobal('cancelAnimationFrame', caf);

  return {
    now: () => time,
    step: (ms: number) => {
      time += ms;
      const batch = queue;
      queue = [];
      for (const entry of batch) {
        entry.callback(time);
      }
    },
    runUntilIdle: (stepMs = 16) => {
      let frames = 0;
      while (queue.length > 0 && frames < 1000) {
        time += stepMs;
        const batch = queue;
        queue = [];
        for (const entry of batch) {
          entry.callback(time);
        }
        frames += 1;
      }
      return frames;
    },
    pendingCount: () => queue.length,
    scheduleCount: () => scheduled,
    canceledCount: () => canceled,
  };
}

/** 在独立的 effect scope 里跑用，便于模拟卸载。 */
function mountTween(initial: number | null, duration = TWEEN_DURATION_MS) {
  const scope = effectScope();
  const target = ref<number | null>(initial);
  const display = scope.run(() => useAnimatedNumber(() => target.value, { duration }))!;
  return { scope, target, display };
}

function stubReducedMotion(matches: boolean) {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: query.includes('prefers-reduced-motion') ? matches : false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  }));
}

let raf: FakeRaf;

beforeEach(() => {
  raf = installFakeRaf();
  // happy-dom 没实现 matchMedia，默认给「不减少动画」。
  stubReducedMotion(false);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('useAnimatedNumber', () => {
  it('P3 首值直显：初始目标 1000 ⇒ 第一次读到就是 1000（不是 0、不是 null）', () => {
    const { display } = mountTween(1000);

    expect(display.value).toBe(1000);
    // 首值不该排帧：没有动画要放。
    expect(raf.pendingCount()).toBe(0);
  });

  it('P1 向上补间有中间态：100→200 推进 125ms 严格介于两者之间，≥250ms 后 == 200', () => {
    const { target, display } = mountTween(100);
    expect(display.value).toBe(100);

    target.value = 200;
    expect(raf.pendingCount()).toBe(1);

    // 逐帧推进到约 125ms（半个时长），此时必须已经动过、且没到终点。
    let elapsed = 0;
    let frames = 0;
    while (elapsed < 125) {
      raf.step(16);
      elapsed += 16;
      frames += 1;
    }
    // 守卫：中间态断言必须落在「确实被驱动过的帧」上。假时钟一次 advance 只消化 2 帧时，
    // 这一行会先红，避免把「补间没跑」误读成「补间没有中间态」。
    expect(frames).toBeGreaterThan(2);
    expect(raf.scheduleCount()).toBeGreaterThan(2);

    const middle = display.value;
    expect(middle).not.toBe(100);
    expect(middle).not.toBe(200);
    expect(middle!).toBeGreaterThan(100);
    expect(middle!).toBeLessThan(200);

    // 推进到 ≥250ms 后收敛，且不再排帧（收敛即停）。
    raf.runUntilIdle(16);
    expect(display.value).toBe(200);
    expect(raf.pendingCount()).toBe(0);
  });

  it('P2 变小立即落位：200→150 同一 tick 就是 150，且从没出现过 (150,200) 之间的值', () => {
    const { target, display } = mountTween(200);
    expect(display.value).toBe(200);

    target.value = 150;
    // 关键：不推进任何帧，同步读值。
    expect(display.value).toBe(150);
    expect(raf.pendingCount()).toBe(0);

    // 之后推进也不该出现「从 200 往下数」的中间值。
    const samples: number[] = [];
    let elapsed = 0;
    while (elapsed < 400) {
      raf.step(16);
      elapsed += 16;
      samples.push(display.value!);
    }
    expect(samples.every((value) => value === 150)).toBe(true);
  });

  it('P4 中途换目标续补：补到约 150 时目标改 300 ⇒ 之后全程单调不减且落 300', () => {
    const { target, display } = mountTween(100);

    target.value = 200;
    // 补到中间态（约 125ms）。
    let elapsed = 0;
    while (elapsed < 125) {
      raf.step(16);
      elapsed += 16;
    }
    const switchedAt = display.value!;
    expect(switchedAt).toBeGreaterThan(100);
    expect(switchedAt).toBeLessThan(200);

    target.value = 300;
    // 换目标当帧不回退、不跳回旧值。
    expect(display.value).toBe(switchedAt);

    const samples: number[] = [display.value!];
    let guard = 0;
    while (raf.pendingCount() > 0 && guard < 500) {
      raf.step(16);
      samples.push(display.value!);
      guard += 1;
    }
    for (let index = 1; index < samples.length; index += 1) {
      expect(samples[index]).toBeGreaterThanOrEqual(samples[index - 1]);
    }
    expect(samples.every((value) => value >= switchedAt)).toBe(true);
    expect(display.value).toBe(300);
  });

  it('P5 卸载即停：scope 停掉后 rAF 不再被调用', () => {
    const { scope, target, display } = mountTween(100);
    target.value = 200;
    raf.step(16);

    // 前置断言（#843）：卸载前补间必须**仍在进行中**，即显示值还没到目标。
    // 少了这一条，「卸载后帧数不涨」可能只是补间自己收敛了，cancelAnimationFrame
    // 到底有没有生效根本测不出来——那是假绿。
    expect(display.value).not.toBe(200);
    expect(raf.pendingCount()).toBe(1);

    scope.stop();

    // 卸载后帧数不再增长。不用 `vi.getTimerCount()` 当判据：happy-dom 里空组件
    // mount() 后是 1、unmount() 后仍是 1（mount 残留），照它判会误绿。
    const scheduledAfterStop = raf.scheduleCount();
    const canceledAfterStop = raf.canceledCount();
    // cancelAnimationFrame 必须真的被调用过（对照 P5-m：漏掉 cancel 时这里就是 0）。
    expect(canceledAfterStop).toBeGreaterThan(0);

    raf.runUntilIdle(16);
    raf.runUntilIdle(16);
    expect(raf.scheduleCount()).toBe(scheduledAfterStop);
    // 在途帧被驱动也不能把显示值送到目标（令牌已失效）。
    expect(display.value).not.toBe(200);

    // 目标再变也不该开新帧（watch 已停）。
    target.value = 500;
    expect(raf.pendingCount()).toBe(0);
    expect(display.value).not.toBe(500);
  });

  it('P5-b 对照：不卸载时帧数会继续增长（证明 P5 的「不增长」不是恒真）', () => {
    // 这是 P5 的反向对照。没有它，P5 可能因为「补间早就收敛」而恒绿。
    const { target } = mountTween(100);
    target.value = 200;
    raf.step(16);

    const before = raf.scheduleCount();
    raf.step(16);
    raf.step(16);
    expect(raf.scheduleCount()).toBeGreaterThan(before);
  });

  it('P6 reduced-motion 命中：目标变化同 tick 落位，无中间态', () => {
    stubReducedMotion(true);
    const { target, display } = mountTween(100);

    target.value = 200;
    expect(display.value).toBe(200);
    expect(raf.pendingCount()).toBe(0);
  });

  it('目标不变不开帧；置 null 清空显示值', () => {
    const { target, display } = mountTween(100);

    target.value = 100;
    expect(raf.pendingCount()).toBe(0);

    target.value = null;
    expect(display.value).toBeNull();
    expect(raf.pendingCount()).toBe(0);
  });

  it('无 rAF 环境直接落位，不把显示值卡在旧值上', async () => {
    vi.stubGlobal('requestAnimationFrame', undefined);
    vi.stubGlobal('cancelAnimationFrame', undefined);
    const { target, display } = mountTween(100);

    target.value = 200;
    await nextTick();
    expect(display.value).toBe(200);
  });
});

import { afterEach, describe, expect, it, vi } from 'vitest';
import { effectScope, ref, type Ref } from 'vue';
import {
  GROWING_DECAY_MS,
  MAX_SCALE_EXTRA,
  MIN_SCALE_EXTRA,
  scaleForSpeed,
  useGrowingScale,
} from '../useGrowingScale';

/**
 * `useGrowingScale` 的判据（S1–S9）。
 *
 * 时钟与 rAF 都注入：速度是「本次增量 / 时间」，不控制时间就没法断言峰值；
 * 帧也手动推进，才能验证 500ms 衰减曲线与「连续推送能接上」。
 */

interface Harness {
  target: Ref<number | null>;
  scale: Ref<number>;
  advance(ms: number): void;
  runFrame(): void;
  pendingFrames(): number;
  stop(): void;
}

function setup(initial: number | null = 100, decayMs = GROWING_DECAY_MS): Harness {
  let time = 0;
  const queue: Array<() => void> = [];
  const now = () => time;
  vi.stubGlobal('requestAnimationFrame', vi.fn((callback: FrameRequestCallback) => {
    queue.push(() => callback(time));
    return queue.length;
  }));
  vi.stubGlobal('cancelAnimationFrame', vi.fn());

  const target = ref<number | null>(initial);
  const scope = effectScope();
  let scale!: Ref<number>;
  scope.run(() => {
    scale = useGrowingScale(() => target.value, { decayMs, now });
  });

  return {
    target,
    scale,
    advance(ms: number): void {
      time += ms;
    },
    runFrame(): void {
      const callback = queue.shift();
      if (!callback) {
        throw new Error('no pending animation frame');
      }
      callback();
    },
    pendingFrames: () => queue.length,
    stop: () => scope.stop(),
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('scaleForSpeed', () => {
  it('S1 峰值随速度线性增长，并夹在上下限内', () => {
    expect(scaleForSpeed(0)).toBeCloseTo(1 + MIN_SCALE_EXTRA);
    expect(scaleForSpeed(50)).toBeCloseTo(1.14);
    expect(scaleForSpeed(100)).toBeCloseTo(1.28);
    expect(scaleForSpeed(10_000)).toBeCloseTo(1 + MAX_SCALE_EXTRA);
  });
});

describe('useGrowingScale', () => {
  it('S2 首次值不放大', () => {
    const { scale } = setup(100);
    expect(scale.value).toBe(1);
  });

  it('S3 50 t/s（200ms 增 10）对应 1.14 峰值', () => {
    const h = setup(100);
    h.advance(200);
    h.target.value = 110;
    expect(h.scale.value).toBeCloseTo(1.14);
  });

  it('S4 100 t/s（200ms 增 20）对应 1.28 峰值', () => {
    const h = setup(100);
    h.advance(200);
    h.target.value = 120;
    expect(h.scale.value).toBeCloseTo(1.28);
  });

  it('S5 峰值在 500ms 内线性衰减回 1，然后停止排帧', () => {
    const h = setup(100);
    h.advance(200);
    h.target.value = 110;
    expect(h.scale.value).toBeCloseTo(1.14);

    h.advance(250);
    h.runFrame();
    expect(h.scale.value).toBeCloseTo(1.07);

    h.advance(250);
    h.runFrame();
    expect(h.scale.value).toBe(1);
    expect(h.pendingFrames()).toBe(0);
  });

  it('S6 连续推送能接上：下一次变大时不会先掉回 1', () => {
    const h = setup(100);
    h.advance(200);
    h.target.value = 110;
    expect(h.scale.value).toBeCloseTo(1.14);

    h.advance(200);
    h.runFrame();
    expect(h.scale.value).toBeCloseTo(1.084);

    // 第二次 50 t/s：新峰值 1.14 高于当前的 1.084，直接接上。
    h.target.value = 120;
    expect(h.scale.value).toBeCloseTo(1.14);
  });

  it('S7 速度越快峰值越高（150 t/s 触顶）', () => {
    const h = setup(100);
    h.advance(200);
    h.target.value = 110;
    h.advance(200);
    h.runFrame();
    h.target.value = 140;
    expect(h.scale.value).toBeCloseTo(1 + MAX_SCALE_EXTRA);
  });

  it('S8 变小不触发放大，正在进行的衰减自然走完', () => {
    const h = setup(100);
    h.advance(200);
    h.target.value = 120;
    expect(h.scale.value).toBeCloseTo(1.28);

    h.advance(250);
    h.target.value = 90;
    h.runFrame();
    expect(h.scale.value).toBeCloseTo(1.14);
  });

  it('S9 卸载后不再响应目标变化', () => {
    const h = setup(100);
    h.stop();
    h.advance(200);
    h.target.value = 120;
    expect(h.scale.value).toBe(1);
  });

  it('S10 系统偏好减少动态效果时始终为 1', () => {
    const spy = vi.spyOn(window, 'matchMedia').mockReturnValue({ matches: true } as MediaQueryList);
    const h = setup(100);
    h.advance(200);
    h.target.value = 120;
    expect(h.scale.value).toBe(1);
    spy.mockRestore();
  });
});

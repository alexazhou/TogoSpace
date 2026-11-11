import { getCurrentScope, onScopeDispose, ref, watch, type Ref } from 'vue';

/**
 * 「增长放大」：每次目标值变大时，按本次增长速度决定放大峰值，然后衰减回 1。
 *
 * 速度 = 本次新增 token / 距上次变大的时间，单位 tokens/s。
 * 让幅度跟速度走，流快时数字跳得明显、流慢时只轻轻跳一下，观感与数据节奏一致。
 *
 * 衰减周期取 500ms：后端进度约 200ms 推一次，下一次推送时上一次放大还剩约六成，
 * 新峰值直接与当前值取大者，所以连续推送能自然接上，不会每次都掉回 1 再重新弹起。
 * 减速/结束（值不再变大）时不做额外处理，让正在进行的衰减自然走完。
 */

/** 放大峰值的衰减时长（毫秒）。 */
export const GROWING_DECAY_MS = 500;

/** 速度 → 放大峰值的映射：每 1 t/s 增加 0.28% 的尺寸（50→1.14，100→1.28）。 */
export const SCALE_PER_SPEED = 0.0028;
/** 再慢的流也保留一点跳动感。 */
export const MIN_SCALE_EXTRA = 0.06;
/** 再快的流也不让数字大到影响布局观感。 */
export const MAX_SCALE_EXTRA = 0.3;

/** 把 tokens/s 映射成缩放峰值（1.x）。 */
export function scaleForSpeed(speed: number): number {
  const extra = Math.min(MAX_SCALE_EXTRA, Math.max(MIN_SCALE_EXTRA, speed * SCALE_PER_SPEED));
  return 1 + extra;
}

export interface GrowingScaleOptions {
  decayMs?: number;
  /** 测试注入时钟；默认 `performance.now`。 */
  now?: () => number;
}

export function useGrowingScale(
  target: () => number | null,
  options: GrowingScaleOptions = {},
): Ref<number> {
  const scale = ref(1);

  // 系统偏好减少动态效果：不启动任何计时，尺寸始终为 1。
  if (prefersReducedMotion()) {
    return scale;
  }

  const decayMs = options.decayMs ?? GROWING_DECAY_MS;
  const now = options.now ?? (() => performance.now());

  let previous: number | null = null;
  let previousTime = now();
  let decayFrom = 1;
  let decayStart = 0;
  let raf: number | null = null;

  function frame(): void {
    const progress = Math.min(1, (now() - decayStart) / decayMs);
    scale.value = 1 + (decayFrom - 1) * (1 - progress);
    if (progress < 1) {
      raf = requestAnimationFrame(frame);
    } else {
      raf = null;
      scale.value = 1;
    }
  }

  // `flush: 'sync'`：值一变就按本次速度设定峰值，下一帧开始衰减。
  const stopWatch = watch(target, (next) => {
    const time = now();
    const prev = previous;
    const elapsedMs = time - previousTime;
    previous = next;
    previousTime = time;
    if (next === null || prev === null || next <= prev) {
      return;
    }
    const speed = elapsedMs > 0 ? (next - prev) / (elapsedMs / 1000) : Number.POSITIVE_INFINITY;
    // 新峰值不低于当前缩放值：连续推送时只往上跳，不制造向下的跳变。
    decayFrom = Math.max(scale.value, scaleForSpeed(speed));
    decayStart = time;
    scale.value = decayFrom;
    if (raf === null) {
      raf = requestAnimationFrame(frame);
    }
  }, { immediate: true, flush: 'sync' });

  if (getCurrentScope()) {
    onScopeDispose(() => {
      stopWatch();
      if (raf !== null) {
        cancelAnimationFrame(raf);
      }
    });
  }

  return scale;
}

function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return false;
  }
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

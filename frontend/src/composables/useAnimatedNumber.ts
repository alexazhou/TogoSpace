import { getCurrentScope, onScopeDispose, ref, watch, type Ref } from 'vue';

/**
 * 数字补间：目标值变化时用 rAF 把「显示值」从当前值数到新目标。
 *
 * 为什么需要它：后端活动进度按 `max(200ms, 10 个 chunk)` 节流推送，慢模型每个 chunk
 * 一个 token，攒够 10 个 chunk 才推一次 ⇒ 两次推送之间画面完全静止（最长 2 秒不动）。
 * 提高推送频率已被否决（每次推送都要读库 + 全量广播）。所以补间只能在显示侧做：
 * 帧率由 rAF 决定，与推送节奏解耦。
 *
 * 行为口径（每条都有对应的测试，改动前先看测试）：
 * 1. **只对变大补间**。变小直接落位，不倒数——结束后真值常比流中估算小
 *    （估算按 delta 累加会偏高），倒着数看起来像数据在回退，比跳变更糟。
 * 2. **首个值直显**，不从 0 数上来。
 * 3. **中途换目标从「当前实际显示值」续补**，不排队、不跳回旧值、不重置曲线。
 * 4. **收敛即停 rAF**，空闲时不留空转帧；组件卸载时取消并在途回调失效。
 * 5. **尊重 `prefers-reduced-motion: reduce`**，命中时直接落位。
 * 6. 无 rAF 的环境（SSR / 纯逻辑测试）直接落位，不把显示值卡在旧值上。
 */

/** 补间时长（毫秒）。略大于后端最小推送间隔 200ms，观感上是「一直在涨」。 */
export const TWEEN_DURATION_MS = 250;

const REDUCED_MOTION_QUERY = '(prefers-reduced-motion: reduce)';

export interface UseAnimatedNumberOptions {
  /** 补间时长（毫秒），默认 `TWEEN_DURATION_MS`。 */
  duration?: number;
}

function prefixedReducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return false;
  }
  try {
    return window.matchMedia(REDUCED_MOTION_QUERY).matches === true;
  } catch {
    return false;
  }
}

/** ease-out cubic：起步快、收尾缓，数字「冲到目标」的观感比线性自然。 */
function easeOutCubic(progress: number): number {
  const remaining = 1 - progress;
  return 1 - remaining * remaining * remaining;
}

export function useAnimatedNumber(
  target: () => number | null,
  options: UseAnimatedNumberOptions = {},
): Ref<number | null> {
  const duration = Math.max(0, options.duration ?? TWEEN_DURATION_MS);
  const display = ref<number | null>(null);

  let frameId: number | null = null;
  /** 递增令牌：取消后旧回调即使被浏览器调用也不再生效（rAF 回调无法真正撤回）。 */
  let frameToken = 0;
  let fromValue = 0;
  let toValue = 0;
  /** 首帧时间戳只用来取基准：rAF 回调的 ts 才是唯一时间来源，不读 `performance.now()`。 */
  let startedAt: number | null = null;

  function rafAvailable(): boolean {
    return typeof requestAnimationFrame === 'function' && typeof cancelAnimationFrame === 'function';
  }

  function cancelFrame(): void {
    frameToken += 1;
    if (frameId !== null) {
      if (rafAvailable()) {
        cancelAnimationFrame(frameId);
      }
      frameId = null;
    }
    startedAt = null;
  }

  /** 直接落位（不动画）：首值、变小、reduced-motion、收敛，都走这里。 */
  function snapTo(value: number): void {
    cancelFrame();
    display.value = value;
  }

  function scheduleFrame(): void {
    if (!rafAvailable()) {
      snapTo(toValue);
      return;
    }
    const token = ++frameToken;
    frameId = requestAnimationFrame((timestamp) => {
      if (token !== frameToken) {
        return;
      }
      frameId = null;
      if (startedAt === null) {
        startedAt = timestamp;
      }
      const elapsed = timestamp - startedAt;
      if (elapsed >= duration) {
        display.value = toValue;
        cancelFrame();
        return;
      }
      const progress = duration <= 0 ? 1 : elapsed / duration;
      display.value = Math.round(fromValue + (toValue - fromValue) * easeOutCubic(progress));
      scheduleFrame();
    });
  }

  function applyTarget(next: number | null): void {
    if (next === null) {
      cancelFrame();
      display.value = null;
      return;
    }
    const current = display.value;
    if (current === null) {
      snapTo(next);
      return;
    }
    if (next === current) {
      cancelFrame();
      return;
    }
    if (next < current) {
      snapTo(next);
      return;
    }
    fromValue = current;
    toValue = next;
    // 先取消上一条曲线，再从「当前实际显示值」续补：新目标优先，不排队。
    cancelFrame();
    if (duration <= 0 || prefixedReducedMotion()) {
      snapTo(next);
      return;
    }
    scheduleFrame();
  }

  // `flush: 'sync'`：目标一变就同步落位，别等渲染前批处理。
  // 补间本身由 rAF 平滑，这里同步不会造成抖动，却让「变小立即落位」成为可断言的事实。
  const stopWatch = watch(target, (next) => applyTarget(next ?? null), { immediate: true, flush: 'sync' });

  if (getCurrentScope()) {
    onScopeDispose(() => {
      stopWatch();
      cancelFrame();
    });
  }

  return display;
}

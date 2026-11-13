import { getCurrentInstance, onUnmounted, ref, watch, type Ref } from 'vue';
import { createResizeObserver } from './useResizeObserver';

/**
 * 「列表自动贴底」的通用实现。
 *
 * 设计要点（踩过的坑都写在这里）：
 * 1. **不靠 scroll 事件猜原因**。scroll 事件分不清用户滚动、程序赋值、浏览器 scroll anchoring
 *    补偿，用它反推「要不要跟随」必然误判，而一旦误判就被写进状态、从此不自愈。
 * 2. **意图与事实分离**：意图只由「用户输入事件」（wheel / touch / 键盘）证明；
 *    事实只由几何量 `distanceToBottom` 证明。两者结合才切换状态。
 * 3. 输入事件的处理**延后一帧**：wheel 事件早于滚动生效，当帧读到的位置还是旧的。
 * 4. 状态只有两态：FOLLOWING（贴底）/ IDLE（用户接管）。切换条件：
 *    - 输入意图向上 + 位置离开硬底（> HARD_BOTTOM_PX）→ IDLE
 *    - 位置回到硬底（≤ HARD_BOTTOM_PX）→ FOLLOWING（无论谁造成的：用户滚回、点了「回到底部」）
 *    - 无输入的纯内容变化（变高变矮）**永远不改变状态**
 * 5. FOLLOWING 下内容变化 → 滚到底，并**下一帧复核一次**（异步渲染常在 RO 之后才把高度定下来）。
 * 6. IDLE 下程序**零次**写 scrollTop；新内容到达只置 `hasNewContent`，由 UI 给出可见出口。
 */

/** 硬底阈值（px）：`distanceToBottom ≤ 它` 才算「真的在最底部」。 */
export const HARD_BOTTOM_PX = 4;

/** 判定「位置被动过」的最小位移，避免亚像素抖动触发状态机。 */
const MOVED_EPSILON_PX = 2;

/** 手指/滚轮方向判定的死区。 */
const INPUT_EPSILON_PX = 4;

const FRAME_FALLBACK_MS = 16;

type InputIntent = 'up' | 'down';

export interface UseStickToBottomOptions {
  /** 跟随状态变化回调（用于同步外部 UI，如输入框底部的跟随态装饰）。 */
  onFollowChange?: (following: boolean) => void;
}

export interface StickToBottomController {
  /** 当前是否在贴底跟随。 */
  following: Ref<boolean>;
  /** 处于 IDLE 时是否又来了新内容（用于展示「有新活动 ↓」出口）。 */
  hasNewContent: Ref<boolean>;
  /** 绑定滚动容器与被观察的内容元素；重复调用会自动解绑旧的。 */
  bind: (viewport: HTMLElement | null, content?: HTMLElement | null) => void;
  /** 内容尺寸变化（ResizeObserver 已自动接上，手动调用用于测试或无 RO 环境）。 */
  notifyContentChanged: () => void;
  /** 强制回到最新内容并恢复跟随（「↓ 新活动」按钮 / 初次加载）。 */
  scrollToLatest: () => void;
  /** 解绑全部监听与观察器。 */
  dispose: () => void;
}

export function useStickToBottom(options: UseStickToBottomOptions = {}): StickToBottomController {
  const following = ref(true);
  const hasNewContent = ref(false);

  let viewport: HTMLElement | null = null;
  // 用纯工厂而不是 `useResizeObserver`：`bind(nextViewport, content)` / `dispose()` 是
  // 命令式的，且本 composable 允许在非组件上下文里使用（那里没有卸载钩子可挂）。
  const contentObserver = createResizeObserver(() => notifyContentChanged());
  let removeListeners: Array<() => void> = [];
  let intentFrame: number | null = null;
  let settleFrame: number | null = null;
  let pendingIntent: InputIntent | null = null;
  let lastScrollTop = 0;
  let lastScrollHeight = 0;
  let touchStartY: number | null = null;

  watch(following, (value) => options.onFollowChange?.(value), { immediate: true });

  function nextFrame(callback: () => void): number {
    if (typeof requestAnimationFrame === 'function') {
      return requestAnimationFrame(callback);
    }
    return setTimeout(callback, FRAME_FALLBACK_MS) as unknown as number;
  }

  function cancelFrame(id: number | null): null {
    if (id === null) {
      return null;
    }
    if (typeof cancelAnimationFrame === 'function') {
      cancelAnimationFrame(id);
    } else {
      clearTimeout(id);
    }
    return null;
  }

  function distanceToBottom(): number {
    if (!viewport) {
      return 0;
    }
    return viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight;
  }

  /** 滚到底；下一帧复核一次，防止「滚到的是上一帧的底」。 */
  function settleToBottom(attempt = 0): void {
    if (!viewport || !following.value) {
      return;
    }
    viewport.scrollTop = viewport.scrollHeight;
    lastScrollTop = viewport.scrollTop;
    if (attempt > 0) {
      return;
    }
    settleFrame = cancelFrame(settleFrame);
    settleFrame = nextFrame(() => {
      settleFrame = null;
      if (following.value && distanceToBottom() > MOVED_EPSILON_PX) {
        settleToBottom(attempt + 1);
      }
    });
  }

  /** 恢复跟随并贴到最新（用户滚回底部 / 点「↓ 新活动」/ 初次加载）。 */
  function followToLatest(): void {
    following.value = true;
    hasNewContent.value = false;
    settleToBottom();
  }

  function detach(): void {
    following.value = false;
  }

  function notifyContentChanged(): void {
    if (!viewport) {
      return;
    }
    const height = viewport.scrollHeight;
    const grew = height > lastScrollHeight + 1;
    lastScrollHeight = height;
    if (following.value) {
      settleToBottom();
      return;
    }
    // 只有「变高」才算有新内容；卡片折叠导致变矮不该弹「↓ 新活动」。
    if (grew) {
      hasNewContent.value = true;
    }
  }

  function evaluateIntent(intent: InputIntent): void {
    if (!viewport) {
      return;
    }
    const distance = distanceToBottom();
    if (intent === 'up' && distance > HARD_BOTTOM_PX) {
      detach();
      return;
    }
    if (distance <= HARD_BOTTOM_PX) {
      followToLatest();
    }
  }

  /** 输入事件早于滚动生效，统一延后一帧再读位置。 */
  function scheduleIntent(intent: InputIntent): void {
    pendingIntent = intent;
    if (intentFrame !== null) {
      return;
    }
    intentFrame = nextFrame(() => {
      intentFrame = null;
      const value = pendingIntent;
      pendingIntent = null;
      if (value) {
        evaluateIntent(value);
      }
    });
  }

  function handleScroll(): void {
    if (!viewport) {
      return;
    }
    lastScrollTop = viewport.scrollTop;

    // 回到硬底 → 恢复跟随（用户滚回来、点按钮都走这条路）。
    if (distanceToBottom() <= HARD_BOTTOM_PX) {
      followToLatest();
    }
  }

  function handleWheel(event: WheelEvent): void {
    if (Math.abs(event.deltaY) < INPUT_EPSILON_PX) {
      return;
    }
    scheduleIntent(event.deltaY < 0 ? 'up' : 'down');
  }

  function handleTouchStart(event: TouchEvent): void {
    touchStartY = event.touches[0]?.clientY ?? null;
  }

  function handleTouchMove(event: TouchEvent): void {
    const currentY = event.touches[0]?.clientY;
    if (currentY == null || touchStartY == null) {
      return;
    }
    const delta = currentY - touchStartY;
    if (Math.abs(delta) < INPUT_EPSILON_PX) {
      return;
    }
    // 手指下移 = 看更早的内容 = 向上滚
    scheduleIntent(delta > 0 ? 'up' : 'down');
    touchStartY = currentY;
  }

  function handleKeydown(event: KeyboardEvent): void {
    const key = event.key;
    const up = key === 'ArrowUp' || key === 'PageUp' || key === 'Home'
      || (key === ' ' && event.shiftKey);
    const down = key === 'ArrowDown' || key === 'PageDown' || key === 'End'
      || (key === ' ' && !event.shiftKey);
    if (up) {
      scheduleIntent('up');
    } else if (down) {
      scheduleIntent('down');
    }
  }

  function dispose(): void {
    contentObserver.disconnect();
    for (const off of removeListeners) {
      off();
    }
    removeListeners = [];
    intentFrame = cancelFrame(intentFrame);
    settleFrame = cancelFrame(settleFrame);
    pendingIntent = null;
    touchStartY = null;
    viewport = null;
  }

  function bind(nextViewport: HTMLElement | null, content: HTMLElement | null = null): void {
    dispose();
    if (!nextViewport) {
      return;
    }
    viewport = nextViewport;
    lastScrollTop = nextViewport.scrollTop;
    lastScrollHeight = nextViewport.scrollHeight;

    const listen = <K extends keyof HTMLElementEventMap>(
      type: K,
      handler: (event: HTMLElementEventMap[K]) => void,
      passive = true,
    ): void => {
      const listener = handler as EventListener;
      nextViewport.addEventListener(type, listener, { passive });
      removeListeners.push(() => nextViewport.removeEventListener(type, listener));
    };

    listen('scroll', handleScroll, true);
    listen('wheel', handleWheel);
    listen('touchstart', handleTouchStart);
    listen('touchmove', handleTouchMove);
    listen('keydown', handleKeydown, false);

    // scrollend 是兜底：拖滚动条 thumb / 惯性结束时不带输入事件，也要复核一次位置。
    if ('onscrollend' in nextViewport) {
      listen('scrollend', () => {
        if (distanceToBottom() <= HARD_BOTTOM_PX) {
          followToLatest();
        }
      });
    }

    // viewport 高度变化（窗口缩放、输入框高度变化）与 content 高度变化（新消息、展开）
    // 都要复核一次「是否还贴着底」。两者共用同一个观察器，`observe(null)` 会被工厂忽略。
    contentObserver.observe(nextViewport);
    contentObserver.observe(content);

    if (following.value) {
      settleToBottom();
    }
  }

  if (getCurrentInstance()) {
    onUnmounted(dispose);
  }

  return {
    following,
    hasNewContent,
    bind,
    notifyContentChanged,
    scrollToLatest: followToLatest,
    dispose,
  };
}

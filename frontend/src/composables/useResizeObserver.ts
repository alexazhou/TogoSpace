import { getCurrentInstance, onBeforeUnmount, unref, watch, type Ref } from 'vue';

/**
 * `ResizeObserver` 的两层封装。
 *
 * 为什么分两层（不是「统一用一个 composable 更整洁」）：
 * 1. **命令式场景**（`useStickToBottom` 的 `bind(nextViewport, content)` / `dispose()`、
 *    `ChatPanel` 的多目标重挂、`PendingMessageBar` 的节点替换）需要在组件生命周期之外
 *    手动挂载与卸载，而且可能在非组件上下文里调用 —— `getCurrentInstance()` 为空的
 *    `useResizeObserver` 在里面拿不到卸载钩子，必须用纯工厂。
 * 2. **声明式场景**（`leftStack` / `streamRef` 这类模板 ref、`[graphRef, canvasRef]` 这类
 *    多目标）只需要「ref 变了就重新挂」，用 `useResizeObserver` 更不容易漏掉卸载。
 * 3. 部分场景只关心「尺寸变了」，不关心 `entry` 参数，因此回调的 `entry` 是可选的。
 *
 * 另外统一承担了 `typeof ResizeObserver === 'function'` 的环境判断：测试宿主（happy-dom）
 * 与老 WebView 没有该 API，调用方不必每处再写一遍守卫。
 */

export type MaybeElement = HTMLElement | null | undefined;
export type ResizeTarget = MaybeElement | Ref<MaybeElement>;
export type ResizeCallback = (entry?: ResizeObserverEntry) => void;

export interface ResizeObserverHandle {
  /** 开始观察；`null` / `undefined` 目标直接忽略（模板 ref 未绑定时不会静默失效）。 */
  observe(target: MaybeElement): void;
  /** 取消对单个目标的观察（观察器句柄保留）。 */
  unobserve(target: MaybeElement): void;
  /** 停止全部观察并丢弃内部实例；之后再 `observe` 会重建。 */
  disconnect(): void;
}

function isResizeObserverAvailable(): boolean {
  return typeof ResizeObserver === 'function';
}

/**
 * 纯工厂：不依赖 Vue 生命周期，适合命令式 bind / dispose。
 * 实例**惰性创建**——第一次 `observe` 到非空目标时才真正构造观察器，
 * 这样「没有目标」的场景不会白白构造一个观察器。
 */
export function createResizeObserver(callback: ResizeCallback): ResizeObserverHandle {
  let observer: ResizeObserver | null = null;

  const ensureObserver = (): ResizeObserver | null => {
    if (!observer && isResizeObserverAvailable()) {
      observer = new ResizeObserver((entries) => {
        callback(entries[0]);
      });
    }
    return observer;
  };

  return {
    observe(target: MaybeElement): void {
      if (!target) {
        return;
      }
      ensureObserver()?.observe(target);
    },
    unobserve(target: MaybeElement): void {
      if (target && observer) {
        observer.unobserve(target);
      }
    },
    disconnect(): void {
      observer?.disconnect();
      observer = null;
    },
  };
}

/**
 * 组件级组合式：自动响应 `Ref` / `Ref[]` 变化，并在组件卸载时清理。
 *
 * 用 getter 作为 watch 源（而不是 `MultiWatchSources` 数组），一是能规避 `vue-tsc` 对
 * 联合类型的报错，二是能在同一处过滤掉尚未绑定的空 ref。
 */
export function useResizeObserver(
  target: ResizeTarget | ResizeTarget[],
  callback: ResizeCallback,
): ResizeObserverHandle {
  const handle = createResizeObserver(callback);

  const getElements = (): HTMLElement[] => {
    const list = Array.isArray(target) ? target : [target];
    return list
      .map((item) => unref(item))
      .filter((el): el is HTMLElement => Boolean(el));
  };

  watch(
    getElements,
    (elements) => {
      handle.disconnect();
      elements.forEach((el) => handle.observe(el));
    },
    { immediate: true },
  );

  if (getCurrentInstance()) {
    onBeforeUnmount(() => handle.disconnect());
  }

  return handle;
}
